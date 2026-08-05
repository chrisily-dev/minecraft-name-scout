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
# Violet: a suspended server is disabled but not scheduled for removal. It is a
# maybe, and should not be mistaken for either of the other two states.
DISCORD_SUSPENDED_COLOR = 0x9B59B6
AVAILABLE_WEBHOOK_ENV = "DISCORD_WEBHOOK"
TAKEN_WEBHOOK_ENV = "DISCORD_WEBHOOK_TAKEN"
DELETING_WEBHOOK_ENV = "DISCORD_WEBHOOK_DELETING"
SUSPENDED_WEBHOOK_ENV = "DISCORD_WEBHOOK_SUSPENDED"
SUMMARY_WEBHOOK_ENV = "DISCORD_WEBHOOK_SUMMARY"
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
# Watched names run on top of the batch, so this is the ceiling on how far a
# run may stretch. At 13 seconds a check, 25 extra names is about 5 minutes on
# top of the batch, comfortably inside the workflow timeout.
MAX_WATCH_CHECKS_PER_RUN = 25

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
    "prison": ("615580983881760787",),
    "prisonrp": ("615580983881760787",),
    "roleplay": ("615580983881760787",),
    "horizon": ("615580983881760787",),
    "raidrise": ("615580983881760787",),
    "prisonescape": ("615580983881760787",),
    "state": ("615580983881760787",),
}

# Off. It fired on every available name, which is a ping per result rather than
# a ping worth reading. Individual watchers in NAME_WATCHERS still get theirs.
# Put the role ID back here to turn it on again.
ALWAYS_NOTIFY_ROLE = ""


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
    deletion_reason: str
    suspended: bool
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

    reason = deletion.get("reason") if isinstance(deletion, dict) else ""
    joins = server.get("joins")
    return ServerDetails(
        created_at=_epoch_millis(server.get("creation")),
        last_online=_epoch_millis(server.get("last_online")),
        online=bool(server.get("online")),
        joins=int(joins) if isinstance(joins, (int, float)) else 0,
        deletion_started=deletion_started,
        deletion_reason=str(reason or ""),
        suspended=bool(server.get("suspended")),
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


def result_status(availability: AvailabilityResult) -> str:
    """The one word that decides which channel a result belongs in."""
    if availability.available:
        return "available"
    if is_pending_deletion(availability):
        return "deleting"
    if is_suspended(availability):
        return "suspended"
    return "taken"


def should_announce(name: str, status: str) -> bool:
    """Whether this result gets its own embed.

    Everything except a plain taken name does. Taken results are the bulk of
    every run and say the same thing each time, so they are counted into the
    end-of-run summary instead.

    Watched names are the exception: someone is waiting on those specifically,
    and a silent pass is indistinguishable from a broken one, so they always
    report even when nothing has changed.
    """
    return status != "taken" or name.casefold() in watchlist_keys()


# Discord renders ANSI escapes inside an ```ansi block, which is the only way
# to get more than the two colours a ```diff block offers. 32 green, 33 yellow,
# 35 pink, 31 red.
_ANSI = {
    "available": "[1;32m",
    "deleting": "[1;33m",
    "suspended": "[1;35m",
    "taken": "[1;31m",
}
_ANSI_RESET = "[0m"
_SUMMARY_LABELS = {
    "available": "AVAILABLE",
    "deleting": "DELETING",
    "suspended": "SUSPENDED",
    "taken": "TAKEN",
}
# Discord caps an embed description at 4096 characters, and a run can check 80
# names. Listing every taken one would blow past that and re-create the noise
# this summary exists to remove.
MAX_LISTED_PER_STATUS = 12


def build_status_block(seen: dict[str, list[str]]) -> str:
    """Render the run breakdown as a coloured ANSI code block."""
    lines = []
    for status in ("available", "deleting", "suspended", "taken"):
        names = seen.get(status, [])
        if not names:
            continue

        header = f"{_ANSI[status]}{_SUMMARY_LABELS[status]:<10}{len(names):>3}{_ANSI_RESET}"
        lines.append(header)

        # Taken names are counted, not listed. Naming all of them is exactly
        # the noise the summary replaced.
        if status == "taken":
            continue
        for name in names[:MAX_LISTED_PER_STATUS]:
            lines.append(f"  {name}")
        if len(names) > MAX_LISTED_PER_STATUS:
            lines.append(f"  ... and {len(names) - MAX_LISTED_PER_STATUS} more")

    return "```ansi\n" + "\n".join(lines) + "\n```"


def build_run_summary(seen: dict[str, list[str]], checked: int) -> dict[str, object]:
    """One message standing in for every taken result in a run."""
    available = len(seen.get("available", []))

    if available:
        title = f"{available} name{'s' if available != 1 else ''} came free this run."
        # Says outright that the detail is elsewhere, so a quiet taken channel
        # never looks like a failed run.
        note = "Their embeds are in the available channel."
        color = DISCORD_COLOR
    else:
        title = "No new servers available."
        note = "Everything checked this run is still in use."
        color = DISCORD_ERROR_COLOR

    description = (
        f"{note}\nChecked {checked} name{'s' if checked != 1 else ''}.\n"
        + build_status_block(seen)
    )

    return {
        "username": "Minecraft Name Scout",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ],
    }


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
            "Available on retry. No longer queued."
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
        # Leaving the queue is what makes a name selectable again: anything
        # still queued is skipped by new-name selection. So this is the back of
        # the line, not the end of the road, and the wording should say so.
        queue["items"] = items
        return "Retry finished. Back of the line for another pass."

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

    # Both states are always stated rather than only when true, so the absence
    # of a warning is a positive answer instead of an ambiguous silence.
    fields.append({
        "name": "Suspended",
        "value": (
            "This server is suspended by Minehut."
            if details.suspended
            else "This server is not suspended."
        ),
        "inline": False,
    })

    deletion = (
        "This server is marked for Deletion."
        if details.deletion_started
        else "This server is not marked for deletion yet."
    )
    if details.deletion_started and details.deletion_reason:
        # The raw code is shown as-is. It is the only explanation Minehut gives
        # for why a name is being freed, and paraphrasing codes I have not seen
        # would risk inventing meanings.
        deletion += f"\nReason: `{details.deletion_reason}`"
    fields.append({
        "name": "Deletion Status",
        "value": deletion,
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
            else DISCORD_SUSPENDED_COLOR
            if is_suspended(availability)
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
            # Only shown for an available name. On a taken one the title, the
            # description, and this field all said the same thing three times.
            *(
                [{
                    "name": "Result",
                    "value": availability.reason,
                    "inline": False,
                }]
                if availability.available
                else []
            ),
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
    pending_deletion: bool = False,
) -> tuple[str, dict[str, object]]:
    """Return the ping line for a name and the mentions Discord may resolve.

    The role only hears about names that are open right now, because almost
    every check comes back taken and pinging on those would get the channel
    muted.

    Someone watching a specific name also hears when its holder is marked for
    deletion. That is the advance warning a watch exists for: it is the moment
    to get ready, not after someone else has already claimed it.
    """
    if not available and not pending_deletion:
        return "", {"parse": []}

    role_ids = [ALWAYS_NOTIFY_ROLE] if (available and ALWAYS_NOTIFY_ROLE) else []
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
        pending_deletion=is_pending_deletion(availability),
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


def is_suspended(availability: AvailabilityResult) -> bool:
    """A taken name whose holder Minehut has disabled."""
    return (
        not availability.available
        and availability.details is not None
        and availability.details.suspended
    )


@dataclass(frozen=True, slots=True)
class Webhooks:
    """One destination per outcome, so each channel means one thing."""

    available: str
    taken: str
    deleting: str
    suspended: str
    # The end-of-run summary, which is about the run rather than about any one
    # name, so it does not belong in a results channel.
    summary: str


def select_webhook(
    availability: AvailabilityResult,
    *,
    webhooks: Webhooks,
) -> tuple[str, str]:
    """Route each outcome to its own channel, strongest signal first.

    Four tiers, ordered by how much they are worth acting on: claim it now, it
    is being deleted so get ready, it is disabled so it might go either way, or
    ignore it.

    Deletion outranks suspension deliberately. A server can be both, and when
    it is, the scheduled removal is the fact that matters.
    """
    if availability.available:
        return webhooks.available, AVAILABLE_WEBHOOK_ENV
    if is_pending_deletion(availability):
        return webhooks.deleting, DELETING_WEBHOOK_ENV
    if is_suspended(availability):
        return webhooks.suspended, SUSPENDED_WEBHOOK_ENV
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

    suspended = environ.get(SUSPENDED_WEBHOOK_ENV, "")
    if not suspended:
        print(
            f"WARNING: {SUSPENDED_WEBHOOK_ENV} is not set, so suspended servers "
            f"stay in the {TAKEN_WEBHOOK_ENV} channel."
        )
        suspended = taken
    else:
        validate_webhook_url(suspended, env_name=SUSPENDED_WEBHOOK_ENV)

    summary = environ.get(SUMMARY_WEBHOOK_ENV, "")
    if not summary:
        print(
            f"WARNING: {SUMMARY_WEBHOOK_ENV} is not set, so the run summary "
            f"stays in the {TAKEN_WEBHOOK_ENV} channel."
        )
        summary = taken
    else:
        validate_webhook_url(summary, env_name=SUMMARY_WEBHOOK_ENV)

    return Webhooks(
        available=available,
        taken=taken,
        deleting=deleting,
        suspended=suspended,
        summary=summary,
    )


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

    # Watched names are checked in addition to the batch, not out of it. They
    # are a handful of specific requests, and spending the batch on them would
    # quietly shrink how much of the pool each run actually covers.
    watch_targets = due_watchlist_names(queue, datetime.now(timezone.utc))[
        :MAX_WATCH_CHECKS_PER_RUN
    ]
    total_checks = len(watch_targets) + checks_per_run
    print(
        f"Checking {len(watch_targets)} watched name(s) plus a batch of "
        f"{checks_per_run}."
    )

    seen: dict[str, list[str]] = {}
    for index in range(total_checks):
        now = datetime.now(timezone.utc)

        if index < len(watch_targets):
            name = watch_targets[index]
            candidate = next(
                (item for item in pool if item.name.casefold() == name.casefold()),
                Candidate(name, 0.0, "Watchlist", "watched name"),
            )
            is_retry = False
            kind = "watch"
        else:
            slot = index - len(watch_targets)
            candidate, is_retry = select_check_target(
                selection_number(run_number, checks_per_run, slot),
                pool,
                queue,
                now,
                # Watched names ran above; letting the rotation pick one too
                # would spend a batch slot re-checking it.
                excluded_names=checked_names | watchlist_keys(),
                allow_due_retry=is_retry_slot(slot, checks_per_run),
            )
            kind = "retry" if is_retry else "new"

        checked_names.add(candidate.name.casefold())
        print(f"Checking {index + 1}/{total_checks} ({kind}): {candidate.name}")

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
        status = result_status(availability)
        seen.setdefault(status, []).append(candidate.name)

        if should_announce(candidate.name, status):
            payload = build_payload(
                candidate,
                availability,
                is_retry=is_retry,
                queue_status=queue_status,
            )
            webhook_url, webhook_env = select_webhook(availability, webhooks=webhooks)
            send_to_discord(webhook_url, payload, env_name=webhook_env)
            print(f"Sent {candidate.name} to the {status} channel.")
        else:
            print(f"{candidate.name} is taken. Counted into the run summary.")

        save_retry_queue(queue_path, queue)

        is_last_check = index == total_checks - 1
        if not is_last_check:
            time.sleep(interval_seconds)

    # Stands in for the taken embeds that were suppressed. Sent every run,
    # including runs where nothing was taken, so a silent channel always means
    # the scan ran rather than that it died.
    send_to_discord(
        webhooks.summary,
        build_run_summary(seen, total_checks),
        env_name=SUMMARY_WEBHOOK_ENV,
    )
    tally = {status: len(names) for status, names in seen.items()}
    print(f"Completed {total_checks} paced availability checks. {tally}")


if __name__ == "__main__":
    main()
