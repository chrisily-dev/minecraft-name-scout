"""Check a paced batch of Minecraft server-name candidates."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

import requests

from name_generator import (
    WATCHLIST_NAMES,
    Candidate,
    build_candidate_pool,
    select_candidate,
)


DISCORD_COLOR = 0x57F287
DISCORD_ERROR_COLOR = 0xED4245
# Amber: a name being deleted is neither free nor a dead end. It sits between
# the two, and the colour should say so at a glance.
DISCORD_PENDING_COLOR = 0xE67E22
AVAILABLE_WEBHOOK_ENV = "DISCORD_WEBHOOK"
TAKEN_WEBHOOK_ENV = "DISCORD_WEBHOOK_TAKEN"
DELETING_WEBHOOK_ENV = "DISCORD_WEBHOOK_DELETING"
MINEHUT_CREATE_URL = "https://dashboard.minehut.com/servers/create"
MINEHUT_LOOKUP_URL = "https://api.minehut.com/server/{name}?byName=true"
USER_AGENT = "MinecraftNameScout/3.0 (+GitHub Actions; paced availability checks)"
DEFAULT_QUEUE_PATH = Path("data/retry_queue.json")
RETRY_DELAY = timedelta(days=1)
# Watched names get their own cadence. Left to the ordinary rotation they would
# only come round once the whole pool cycles, roughly a day, which is far too
# slow for a name someone is actively waiting to claim.
WATCH_REFRESH = timedelta(hours=1)
# A name whose holder is being deleted is the closest thing to a lead this bot
# produces, so it is revisited far sooner than an ordinary taken name, and kept
# on that cadence until it either frees up or stops being marked.
PENDING_DELETION_REFRESH = timedelta(hours=3)
DEFAULT_CHECKS_PER_RUN = 20
MAX_CHECKS_PER_RUN = 80
DEFAULT_REQUEST_INTERVAL_SECONDS = 13.0
MIN_REQUEST_INTERVAL_SECONDS = 13.0
# Share of each batch that may be spent draining due retries. Each taken name
# needs one retry to leave the queue, so in a steady state roughly half of all
# checks are retries.
RETRY_SLOT_SHARE = 0.5

# Discord user IDs to ping when a specific name is checked, keyed by the
# casefolded name. Every name here also lives in
# name_generator.WATCHLIST_NAMES so the pool actually reaches it.
NAME_WATCHERS: dict[str, tuple[str, ...]] = {
    "harbor": ("1154980857342345286",),
    "harbour": ("1154980857342345286",),
    "sete": ("672518392447762462",),
    "dungeon": ("672518392447762462",),
    "dungeons": ("672518392447762462",),
    "dunheon": ("672518392447762462",),
}

# Pinged on every result. Set to "" to stop the per-message role ping.
ALWAYS_NOTIFY_ROLE = "1531794005107671081"


@dataclass(frozen=True, slots=True)
class ServerDetails:
    """What Minehut knows about a server that already holds a name.

    Only populated for taken names. ``last_online`` is the useful one: a name
    held by a server nobody has started in a year is a far better bet than one
    in daily use, and ``deletion_started`` means the name is about to free up.
    """

    created_at: datetime | None
    last_online: datetime | None
    online: bool
    joins: int
    deletion_started: bool
    plan: str


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    available: bool
    reason: str
    status_code: int
    details: ServerDetails | None = None


def check_name_availability(
    name: str,
    *,
    session: requests.Session | None = None,
) -> AvailabilityResult:
    """Make exactly one Minehut lookup for one candidate."""
    client = session or requests.Session()
    response = client.get(
        MINEHUT_LOOKUP_URL.format(name=name),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    )

    if response.status_code == 404:
        return AvailabilityResult(
            available=True,
            reason="No Minehut server was found with this name.",
            status_code=404,
        )

    response.raise_for_status()
    if response.status_code != 200:
        raise RuntimeError(f"Unexpected Minehut status: {response.status_code}")

    try:
        data = response.json()
    except ValueError as error:
        raise RuntimeError("Minehut returned invalid JSON.") from error

    server = data.get("server") if isinstance(data, dict) else None
    if not server:
        return AvailabilityResult(
            available=True,
            reason="No Minehut server was found with this name.",
            status_code=200,
        )

    return AvailabilityResult(
        available=False,
        reason="Minehut says this name is already registered.",
        status_code=200,
        details=_read_details(server),
    )


def _epoch_millis(value: object) -> datetime | None:
    """Convert a Minehut millisecond timestamp, treating 0 as never."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _read_details(server: object) -> ServerDetails | None:
    """Pull the interesting fields out of a server payload, tolerating gaps."""
    if not isinstance(server, dict):
        return None

    deletion = server.get("deletion")
    deletion_started = bool(
        isinstance(deletion, dict) and deletion.get("started")
    ) or bool(server.get("deleted"))

    joins = server.get("joins")
    return ServerDetails(
        created_at=_epoch_millis(server.get("creation")),
        last_online=_epoch_millis(server.get("last_online")),
        online=bool(server.get("online")),
        joins=int(joins) if isinstance(joins, (int, float)) else 0,
        deletion_started=deletion_started,
        plan=str(server.get("activeServerPlan") or server.get("server_plan") or "unknown"),
    )


def load_retry_queue(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "items": []}

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError(f"Invalid retry queue format: {path}")
    return data


def save_retry_queue(path: Path, queue: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(queue, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def select_check_target(
    run_number: int,
    pool: list[Candidate],
    queue: dict[str, object],
    now: datetime,
    *,
    excluded_names: set[str] | None = None,
    allow_due_retry: bool = False,
) -> tuple[Candidate, bool]:
    """Select a new name, allowing a due retry only at the queue's bottom."""
    items = queue["items"]
    excluded = {name.casefold() for name in (excluded_names or set())}
    due_items = sorted(
        (
            item
            for item in items
            if isinstance(item, dict)
            and str(item.get("name", "")).casefold() not in excluded
            and isinstance(item.get("retry_after"), str)
            and _parse_timestamp(item["retry_after"]) <= now
        ),
        key=lambda item: (item["retry_after"], item["name"].casefold()),
    )
    if allow_due_retry and due_items:
        item = due_items[0]
        return (
            Candidate(
                name=item["name"],
                score=float(item["score"]),
                style=item["style"],
                source=item["source"],
            ),
            True,
        )

    queued_names = {
        item["name"].casefold()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    queued_names.update(excluded)
    for offset in range(len(pool)):
        candidate = select_candidate(run_number + offset, pool)
        if candidate.name.casefold() not in queued_names:
            return candidate, False

    # If every new name is queued, a due retry is the only useful fallback.
    if due_items:
        item = due_items[0]
        return (
            Candidate(
                name=item["name"],
                score=float(item["score"]),
                style=item["style"],
                source=item["source"],
            ),
            True,
        )

    raise RuntimeError("Every generated candidate is already in the retry queue.")


def watchlist_keys() -> set[str]:
    """Casefolded names that are on the hourly watch cadence."""
    return {name.casefold() for name in WATCHLIST_NAMES}


def watch_slots(checks_per_run: int) -> int:
    """How many slots in one batch may go to watched names.

    Capped so a backlog of watched names cannot swallow a whole batch and stall
    the ordinary rotation. At the usual batch size this is far more than the
    handful of names that actually come due each hour.
    """
    return max(1, checks_per_run // 4)


def due_watchlist_names(queue: dict[str, object], now: datetime) -> list[str]:
    """Watched names that have not been checked within the refresh window."""
    raw = queue.get("watch_checks")
    checks = raw if isinstance(raw, dict) else {}

    due: list[tuple[datetime | None, str]] = []
    for name in WATCHLIST_NAMES:
        stamp = checks.get(name.casefold())
        last = _parse_timestamp(stamp) if isinstance(stamp, str) else None
        if last is None or last + WATCH_REFRESH <= now:
            due.append((last, name))

    # Never-checked names first, then the longest-waiting.
    due.sort(key=lambda item: (item[0] is not None, item[0] or now))
    return [name for _, name in due]


def record_watch_check(queue: dict[str, object], name: str, now: datetime) -> None:
    raw = queue.get("watch_checks")
    if not isinstance(raw, dict):
        raw = {}
        queue["watch_checks"] = raw
    raw[name.casefold()] = now.isoformat()


def update_retry_queue(
    queue: dict[str, object],
    candidate: Candidate,
    availability: AvailabilityResult,
    *,
    is_retry: bool,
    now: datetime,
) -> str:
    """Update the queue and return text suitable for the Discord embed."""
    items = [
        item
        for item in queue["items"]
        if not (
            isinstance(item, dict)
            and str(item.get("name", "")).casefold() == candidate.name.casefold()
        )
    ]

    if availability.available:
        queue["items"] = items
        return (
            "Available on retry. Removed from the queue."
            if is_retry
            else "No retry needed."
        )

    if candidate.name.casefold() in watchlist_keys():
        # Watched names run on the hourly cadence instead of the retry queue.
        # Queueing them would also make them ineligible for a day, which is the
        # opposite of what a watch is for.
        queue["items"] = items
        hours = int(WATCH_REFRESH.total_seconds() // 3600)
        return f"Watched name. Checked again every {hours}h."

    # Checked before the is_retry branch on purpose. An ordinary name leaves the
    # queue after its single retry, but a name being deleted has to stay on the
    # short cycle until it either frees up or the deletion is called off.
    if is_pending_deletion(availability):
        retry_after = now + PENDING_DELETION_REFRESH
        items.append(
            {
                "name": candidate.name,
                "score": candidate.score,
                "style": candidate.style,
                "source": candidate.source,
                "first_checked_at": now.isoformat(),
                "retry_after": retry_after.isoformat(),
                "pending_deletion": True,
            }
        )
        queue["items"] = items
        hours = int(PENDING_DELETION_REFRESH.total_seconds() // 3600)
        unix_time = int(retry_after.timestamp())
        return (
            f"Marked for deletion, so rechecked every {hours}h. "
            f"Next check <t:{unix_time}:R>."
        )

    if is_retry:
        queue["items"] = items
        return "Retry finished. Removed from the queue."

    retry_after = now + RETRY_DELAY
    items.append(
        {
            "name": candidate.name,
            "score": candidate.score,
            "style": candidate.style,
            "source": candidate.source,
            "first_checked_at": now.isoformat(),
            "retry_after": retry_after.isoformat(),
        }
    )
    queue["items"] = items
    unix_time = int(retry_after.timestamp())
    return f"Checking again <t:{unix_time}:R>."


def _holder_fields(details: ServerDetails | None) -> list[dict[str, object]]:
    """Describe the server currently holding a name.

    Rendered with Discord timestamps so everyone reads them in their own
    timezone, and so "last online" shows as a relative age. A name last used
    years ago is a much better prospect than one in daily use.
    """
    if details is None:
        return []

    fields: list[dict[str, object]] = []

    if details.last_online is not None:
        stamp = int(details.last_online.timestamp())
        fields.append({
            "name": "Last online",
            "value": f"<t:{stamp}:R> (<t:{stamp}:d>)",
            "inline": True,
        })
    else:
        fields.append({"name": "Last online", "value": "Never started", "inline": True})

    if details.created_at is not None:
        stamp = int(details.created_at.timestamp())
        fields.append({
            "name": "Created",
            "value": f"<t:{stamp}:d> (<t:{stamp}:R>)",
            "inline": True,
        })

    activity = "Online now" if details.online else "Offline"
    if details.joins:
        activity += f" | {details.joins:,} joins"
    fields.append({"name": "Activity", "value": activity, "inline": True})

    # Always stated rather than only when true, so the absence of a warning is
    # a positive answer instead of an ambiguous silence.
    fields.append({
        "name": "Deletion Status",
        "value": (
            "This server is marked for Deletion."
            if details.deletion_started
            else "This server is not marked for deletion yet."
        ),
        "inline": False,
    })

    return fields


def build_embed(
    candidate: Candidate,
    availability: AvailabilityResult,
    *,
    is_retry: bool = False,
    queue_status: str = "No retry needed.",
) -> dict[str, object]:
    status_word = "Available" if availability.available else "Taken"
    return {
        "title": f"{status_word}: {candidate.name}",
        "description": (
            f"`{candidate.name}` looks available on Minehut."
            if availability.available
            else f"`{candidate.name}` is already in use on Minehut."
        ),
        "color": (
            DISCORD_COLOR
            if availability.available
            else DISCORD_PENDING_COLOR
            if is_pending_deletion(availability)
            else DISCORD_ERROR_COLOR
        ),
        "fields": [
            {
                "name": "Status",
                "value": status_word,
                "inline": True,
            },
            {
                "name": "Length",
                "value": f"{len(candidate.name)} characters",
                "inline": True,
            },
            *_holder_fields(availability.details),
            {
                "name": "Result",
                "value": availability.reason,
                "inline": False,
            },
            {
                "name": "Retry",
                "value": queue_status,
                "inline": False,
            },
            # Only offered when the name can actually be claimed. On a taken
            # name the link just leads to a rejected form.
            *(
                [{
                    "name": "Minehut",
                    "value": f"[Create server]({MINEHUT_CREATE_URL})",
                    "inline": False,
                }]
                if availability.available
                else []
            ),
        ],
        # No footer text. The bare timestamp is enough, and the old line
        # repeated the same rate-limit blurb on every single message.
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_mentions(
    name: str,
    *,
    available: bool,
) -> tuple[str, dict[str, object]]:
    """Return the ping line for a name and the mentions Discord may resolve.

    Nothing pings unless the name is actually open. Almost every check comes
    back taken, so pinging on those would fire constantly and get both the role
    and the individual watchers to mute the channel. A ping here always means
    the name is claimable right now.
    """
    if not available:
        return "", {"parse": []}

    role_ids = [ALWAYS_NOTIFY_ROLE] if ALWAYS_NOTIFY_ROLE else []
    user_ids = list(NAME_WATCHERS.get(name.casefold(), ()))

    content = " ".join(
        [
            *(f"<@&{role_id}>" for role_id in role_ids),
            *(f"<@{user_id}>" for user_id in user_ids),
        ]
    )
    # "parse" stays empty so only these exact IDs can ping. A stray @everyone in
    # a generated name can never resolve.
    allowed_mentions: dict[str, object] = {"parse": []}
    if role_ids:
        allowed_mentions["roles"] = role_ids
    if user_ids:
        allowed_mentions["users"] = user_ids
    return content, allowed_mentions


def build_payload(
    candidate: Candidate,
    availability: AvailabilityResult,
    *,
    is_retry: bool = False,
    queue_status: str = "No retry needed.",
) -> dict[str, object]:
    content, allowed_mentions = build_mentions(
        candidate.name,
        available=availability.available,
    )
    payload: dict[str, object] = {
        "username": "Minecraft Name Scout",
        "allowed_mentions": allowed_mentions,
        "embeds": [
            build_embed(
                candidate,
                availability,
                is_retry=is_retry,
                queue_status=queue_status,
            )
        ],
    }
    if content:
        payload["content"] = content
    return payload


def validate_webhook_url(
    webhook_url: str,
    *,
    env_name: str = AVAILABLE_WEBHOOK_ENV,
) -> None:
    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        raise ValueError(f"{env_name} is missing or is not a Discord webhook URL.")


def is_pending_deletion(availability: AvailabilityResult) -> bool:
    """A taken name whose holder is being removed, so it should free up soon."""
    return (
        not availability.available
        and availability.details is not None
        and availability.details.deletion_started
    )


@dataclass(frozen=True, slots=True)
class Webhooks:
    """One destination per outcome, so each channel means one thing."""

    available: str
    taken: str
    deleting: str


def select_webhook(
    availability: AvailabilityResult,
    *,
    webhooks: Webhooks,
) -> tuple[str, str]:
    """Route each outcome to its own channel.

    Three tiers because there are three actions: claim it now, watch it because
    it is about to free up, or ignore it. Names pending deletion are the rarest
    and most useful of the three, and they were previously buried among the
    taken results.
    """
    if availability.available:
        return webhooks.available, AVAILABLE_WEBHOOK_ENV
    if is_pending_deletion(availability):
        return webhooks.deleting, DELETING_WEBHOOK_ENV
    return webhooks.taken, TAKEN_WEBHOOK_ENV


def resolve_webhooks(environ: dict[str, str]) -> Webhooks:
    """Read the webhooks, folding missing channels into the ones that exist."""
    available = environ.get(AVAILABLE_WEBHOOK_ENV, "")
    validate_webhook_url(available, env_name=AVAILABLE_WEBHOOK_ENV)

    taken = environ.get(TAKEN_WEBHOOK_ENV, "")
    if not taken:
        print(
            f"WARNING: {TAKEN_WEBHOOK_ENV} is not set, so taken names still go to "
            f"the {AVAILABLE_WEBHOOK_ENV} channel. Set the secret to split them."
        )
        taken = available
    else:
        validate_webhook_url(taken, env_name=TAKEN_WEBHOOK_ENV)

    deleting = environ.get(DELETING_WEBHOOK_ENV, "")
    if not deleting:
        # Falls back to the taken channel, which is where these results used to
        # go, so an unset secret changes nothing rather than dropping messages.
        print(
            f"WARNING: {DELETING_WEBHOOK_ENV} is not set, so names pending "
            f"deletion stay in the {TAKEN_WEBHOOK_ENV} channel."
        )
        deleting = taken
    else:
        validate_webhook_url(deleting, env_name=DELETING_WEBHOOK_ENV)

    return Webhooks(available=available, taken=taken, deleting=deleting)


def send_to_discord(
    webhook_url: str,
    payload: dict[str, object],
    *,
    session: requests.Session | None = None,
    env_name: str = AVAILABLE_WEBHOOK_ENV,
) -> None:
    validate_webhook_url(webhook_url, env_name=env_name)

    client = session or requests.Session()
    response = client.post(webhook_url, json=payload, timeout=15)
    response.raise_for_status()


def _run_number() -> int:
    raw_value = os.environ.get("GITHUB_RUN_NUMBER")
    if raw_value:
        try:
            return max(int(raw_value), 1)
        except ValueError as error:
            raise ValueError("GITHUB_RUN_NUMBER must be an integer.") from error

    # Local fallback: one deterministic batch per five-minute interval.
    return max(int(time.time() // 300), 1)


def _checks_per_run() -> int:
    raw_value = os.environ.get("CHECKS_PER_RUN", str(DEFAULT_CHECKS_PER_RUN))
    try:
        return int(raw_value)
    except ValueError as error:
        raise ValueError("CHECKS_PER_RUN must be an integer.") from error


def _request_interval_seconds() -> float:
    raw_value = os.environ.get(
        "REQUEST_INTERVAL_SECONDS",
        str(DEFAULT_REQUEST_INTERVAL_SECONDS),
    )
    try:
        return float(raw_value)
    except ValueError as error:
        raise ValueError("REQUEST_INTERVAL_SECONDS must be a number.") from error


def validate_rate_settings(checks_per_run: int, interval_seconds: float) -> None:
    if not 1 <= checks_per_run <= MAX_CHECKS_PER_RUN:
        raise ValueError(
            f"Checks per run must be between 1 and {MAX_CHECKS_PER_RUN}."
        )
    if interval_seconds < MIN_REQUEST_INTERVAL_SECONDS:
        raise ValueError(
            f"Request interval must be at least "
            f"{MIN_REQUEST_INTERVAL_SECONDS:.0f} seconds."
        )


def selection_number(run_number: int, checks_per_run: int, slot: int) -> int:
    """Map each workflow slot to a non-overlapping deterministic pool index."""
    return ((max(run_number, 1) - 1) * checks_per_run) + slot + 1


def is_retry_slot(slot: int, checks_per_run: int) -> bool:
    """Return whether this slot may spend itself on a due retry.

    Every taken name joins the queue, but a name only leaves it by being
    retried. Allowing a single retry per run made the queue grow by roughly
    checks_per_run each time while draining by one, so it eventually covered
    the whole pool and select_check_target had nothing left to offer. Retries
    now get a share of the batch, which keeps the queue flat over time. They
    still sit at the tail so new names are always checked first.
    """
    new_name_slots = max(1, round(checks_per_run * (1 - RETRY_SLOT_SHARE)))
    return slot >= new_name_slots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selected candidates without making network requests.",
    )
    parser.add_argument(
        "--run-number",
        type=int,
        help="Override the deterministic selection index.",
    )
    parser.add_argument(
        "--checks-per-run",
        type=int,
        help=f"Candidates to check (default {DEFAULT_CHECKS_PER_RUN}).",
    )
    parser.add_argument(
        "--request-interval-seconds",
        type=float,
        help=(
            "Seconds between Minehut requests "
            f"(minimum {MIN_REQUEST_INTERVAL_SECONDS:.0f})."
        ),
    )
    args = parser.parse_args()

    run_number = args.run_number if args.run_number is not None else _run_number()
    checks_per_run = (
        args.checks_per_run
        if args.checks_per_run is not None
        else _checks_per_run()
    )
    interval_seconds = (
        args.request_interval_seconds
        if args.request_interval_seconds is not None
        else _request_interval_seconds()
    )
    try:
        validate_rate_settings(checks_per_run, interval_seconds)
    except ValueError as error:
        parser.error(str(error))

    pool = build_candidate_pool()
    checked_names: set[str] = set()

    if args.dry_run:
        dry_queue: dict[str, object] = {"version": 1, "items": []}
        candidates: list[Candidate] = []
        now = datetime.now(timezone.utc)
        for slot in range(checks_per_run):
            candidate, _ = select_check_target(
                selection_number(run_number, checks_per_run, slot),
                pool,
                dry_queue,
                now,
                excluded_names=checked_names,
                allow_due_retry=is_retry_slot(slot, checks_per_run),
            )
            checked_names.add(candidate.name.casefold())
            candidates.append(candidate)

        print(f"Selected {len(candidates)} unique candidates for the paced batch.")
        print(
            json.dumps(
                {
                    "candidates": [
                        {
                            "name": candidate.name,
                            "length": len(candidate.name),
                            "score": candidate.score,
                            "style": candidate.style,
                        }
                        for candidate in candidates
                    ],
                    "request_interval_seconds": interval_seconds,
                    "api_lookups_performed": 0,
                },
                indent=2,
            )
        )
        return

    queue_path = Path(os.environ.get("RETRY_QUEUE_PATH", DEFAULT_QUEUE_PATH))
    queue = load_retry_queue(queue_path)
    webhooks = resolve_webhooks(dict(os.environ))

    watch_used = 0
    for slot in range(checks_per_run):
        now = datetime.now(timezone.utc)

        # Watched names come first and are never held back by the retry queue,
        # so someone waiting on a name hears within the refresh window rather
        # than whenever the rotation happens to reach it.
        due_watch = [
            name
            for name in due_watchlist_names(queue, now)
            if name.casefold() not in checked_names
        ]
        if due_watch and watch_used < watch_slots(checks_per_run):
            watch_used += 1
            name = due_watch[0]
            candidate = next(
                (item for item in pool if item.name.casefold() == name.casefold()),
                Candidate(name, 0.0, "Watchlist", "watched name"),
            )
            is_retry = False
            kind = "watch"
        else:
            candidate, is_retry = select_check_target(
                selection_number(run_number, checks_per_run, slot),
                pool,
                queue,
                now,
                # Watched names are handled above; letting the ordinary
                # rotation pick one too would waste a slot on a duplicate.
                excluded_names=checked_names | watchlist_keys(),
                allow_due_retry=is_retry_slot(slot, checks_per_run),
            )
            kind = "retry" if is_retry else "new"

        checked_names.add(candidate.name.casefold())
        print(f"Checking {slot + 1}/{checks_per_run} ({kind}): {candidate.name}")

        availability = check_name_availability(candidate.name)
        print(availability.reason)
        if candidate.name.casefold() in watchlist_keys():
            record_watch_check(queue, candidate.name, now)
        queue_status = update_retry_queue(
            queue,
            candidate,
            availability,
            is_retry=is_retry,
            now=now,
        )
        payload = build_payload(
            candidate,
            availability,
            is_retry=is_retry,
            queue_status=queue_status,
        )
        webhook_url, webhook_env = select_webhook(availability, webhooks=webhooks)
        send_to_discord(webhook_url, payload, env_name=webhook_env)
        save_retry_queue(queue_path, queue)
        channel = (
            "available"
            if availability.available
            else "pending deletion"
            if is_pending_deletion(availability)
            else "taken"
        )
        print(f"Sent the {candidate.name} result embed to the {channel} channel.")

        is_last_check = slot == checks_per_run - 1
        if not is_last_check:
            time.sleep(interval_seconds)

    print(f"Completed {checks_per_run} paced availability checks.")


if __name__ == "__main__":
    main()
