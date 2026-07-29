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

from name_generator import Candidate, build_candidate_pool, select_candidate


DISCORD_COLOR = 0x57F287
DISCORD_ERROR_COLOR = 0xED4245
AVAILABLE_WEBHOOK_ENV = "DISCORD_WEBHOOK"
TAKEN_WEBHOOK_ENV = "DISCORD_WEBHOOK_TAKEN"
MINEHUT_CREATE_URL = "https://dashboard.minehut.com/servers/create"
MINEHUT_LOOKUP_URL = "https://api.minehut.com/server/{name}?byName=true"
USER_AGENT = "MinecraftNameScout/3.0 (+GitHub Actions; paced availability checks)"
DEFAULT_QUEUE_PATH = Path("data/retry_queue.json")
RETRY_DELAY = timedelta(days=1)
DEFAULT_CHECKS_PER_RUN = 20
MAX_CHECKS_PER_RUN = 80
DEFAULT_REQUEST_INTERVAL_SECONDS = 13.0
MIN_REQUEST_INTERVAL_SECONDS = 13.0

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
class AvailabilityResult:
    available: bool
    reason: str
    status_code: int


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
        "color": DISCORD_COLOR if availability.available else DISCORD_ERROR_COLOR,
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
            {
                "name": "Minehut",
                "value": f"[Create server]({MINEHUT_CREATE_URL})",
                "inline": False,
            },
        ],
        "footer": {
            "text": (
                f"{'Retry' if is_retry else 'New name'} | "
                "max 5 checks/min | 4-12 letters"
            )
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_mentions(name: str) -> tuple[str, dict[str, object]]:
    """Return the ping line for a name and the mentions Discord may resolve."""
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
    content, allowed_mentions = build_mentions(candidate.name)
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


def select_webhook(
    availability: AvailabilityResult,
    *,
    available_webhook_url: str,
    taken_webhook_url: str,
) -> tuple[str, str]:
    """Route available and taken names to their own Discord channels."""
    if availability.available:
        return available_webhook_url, AVAILABLE_WEBHOOK_ENV
    return taken_webhook_url, TAKEN_WEBHOOK_ENV


def resolve_webhooks(environ: dict[str, str]) -> tuple[str, str]:
    """Read both webhooks, falling back to one channel when the split is unset."""
    available_webhook_url = environ.get(AVAILABLE_WEBHOOK_ENV, "")
    validate_webhook_url(available_webhook_url, env_name=AVAILABLE_WEBHOOK_ENV)

    taken_webhook_url = environ.get(TAKEN_WEBHOOK_ENV, "")
    if not taken_webhook_url:
        print(
            f"WARNING: {TAKEN_WEBHOOK_ENV} is not set, so taken names still go to "
            f"the {AVAILABLE_WEBHOOK_ENV} channel. Set the secret to split them."
        )
        return available_webhook_url, available_webhook_url

    validate_webhook_url(taken_webhook_url, env_name=TAKEN_WEBHOOK_ENV)
    return available_webhook_url, taken_webhook_url


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
                allow_due_retry=slot == checks_per_run - 1,
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
    available_webhook_url, taken_webhook_url = resolve_webhooks(dict(os.environ))

    for slot in range(checks_per_run):
        now = datetime.now(timezone.utc)
        candidate, is_retry = select_check_target(
            selection_number(run_number, checks_per_run, slot),
            pool,
            queue,
            now,
            excluded_names=checked_names,
            allow_due_retry=slot == checks_per_run - 1,
        )
        checked_names.add(candidate.name.casefold())
        print(
            f"Checking {slot + 1}/{checks_per_run} "
            f"({'retry' if is_retry else 'new'}): {candidate.name}"
        )

        availability = check_name_availability(candidate.name)
        print(availability.reason)
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
        webhook_url, webhook_env = select_webhook(
            availability,
            available_webhook_url=available_webhook_url,
            taken_webhook_url=taken_webhook_url,
        )
        send_to_discord(webhook_url, payload, env_name=webhook_env)
        save_retry_queue(queue_path, queue)
        channel = "available" if availability.available else "taken"
        print(f"Sent the {candidate.name} result embed to the {channel} channel.")

        is_last_check = slot == checks_per_run - 1
        if not is_last_check:
            time.sleep(interval_seconds)

    print(f"Completed {checks_per_run} paced availability checks.")


if __name__ == "__main__":
    main()
