"""Send one Minecraft server-name candidate to a Discord webhook."""

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
MINEHUT_DASHBOARD = "https://app.minehut.com/dashboard"
MINEHUT_LOOKUP_URL = "https://api.minehut.com/server/{name}?byName=true"
USER_AGENT = "MinecraftNameScout/2.0 (+GitHub Actions; one lookup per run)"
DEFAULT_QUEUE_PATH = Path("data/retry_queue.json")
RETRY_DELAY = timedelta(days=1)


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
            reason="Minehut returned 404 (no registered server found).",
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
            reason="Minehut returned no registered server object.",
            status_code=200,
        )

    return AvailabilityResult(
        available=False,
        reason="A registered Minehut server already uses this name.",
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
) -> tuple[Candidate, bool]:
    """Prefer the oldest due retry; otherwise select one new ranked candidate."""
    items = queue["items"]
    due_items = sorted(
        (
            item
            for item in items
            if isinstance(item, dict)
            and isinstance(item.get("retry_after"), str)
            and _parse_timestamp(item["retry_after"]) <= now
        ),
        key=lambda item: (item["retry_after"], item["name"].casefold()),
    )
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

    queued_names = {
        item["name"].casefold()
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for offset in range(len(pool)):
        candidate = select_candidate(run_number + offset, pool)
        if candidate.name.casefold() not in queued_names:
            return candidate, False

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
            "Removed from the retry queue after becoming available."
            if is_retry
            else "No retry needed."
        )

    if is_retry:
        queue["items"] = items
        return "Next-day retry completed; the name was removed from the queue."

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
    return f"Queued for one retry <t:{unix_time}:R>."


def build_embed(
    candidate: Candidate,
    availability: AvailabilityResult,
    *,
    is_retry: bool = False,
    queue_status: str = "No retry needed.",
) -> dict[str, object]:
    status_word = "available" if availability.available else "unavailable"
    return {
        "title": (
            "Available Minehut Server Name"
            if availability.available
            else "Minehut Server Name Unavailable"
        ),
        "description": (
            f"## `{candidate.name}`\n"
            f"This dictionary-driven candidate is currently **{status_word}**."
        ),
        "color": DISCORD_COLOR if availability.available else DISCORD_ERROR_COLOR,
        "fields": [
            {
                "name": "Length",
                "value": f"{len(candidate.name)} characters",
                "inline": True,
            },
            {
                "name": "Style",
                "value": candidate.style,
                "inline": True,
            },
            {
                "name": "Desirability score",
                "value": f"{candidate.score:.1f}",
                "inline": True,
            },
            {
                "name": "Availability check",
                "value": availability.reason,
                "inline": False,
            },
            {
                "name": "Queue status",
                "value": queue_status,
                "inline": False,
            },
            {
                "name": "Claim or verify",
                "value": f"[Open the Minehut dashboard]({MINEHUT_DASHBOARD})",
                "inline": False,
            },
        ],
        "footer": {
            "text": (
                f"{'Retry' if is_retry else 'New candidate'} | "
                "one API lookup per run | 4-12 letters"
            )
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_payload(
    candidate: Candidate,
    availability: AvailabilityResult,
    *,
    is_retry: bool = False,
    queue_status: str = "No retry needed.",
) -> dict[str, object]:
    return {
        "username": "Minecraft Name Scout",
        "allowed_mentions": {"parse": []},
        "embeds": [
            build_embed(
                candidate,
                availability,
                is_retry=is_retry,
                queue_status=queue_status,
            )
        ],
    }


def send_to_discord(
    webhook_url: str,
    payload: dict[str, object],
    *,
    session: requests.Session | None = None,
) -> None:
    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        raise ValueError("DISCORD_WEBHOOK is missing or is not a Discord webhook URL.")

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

    # Local fallback: one deterministic slot per five-minute interval.
    return max(int(time.time() // 300), 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the candidate and payload without sending to Discord.",
    )
    parser.add_argument(
        "--run-number",
        type=int,
        help="Override the deterministic selection index.",
    )
    args = parser.parse_args()

    run_number = args.run_number or _run_number()
    pool = build_candidate_pool()

    if args.dry_run:
        candidate = select_candidate(run_number, pool)
        print(
            f"Selected exactly one candidate: "
            f"{candidate.name} (score {candidate.score:.1f})"
        )
        print(
            json.dumps(
                {
                    "candidate": candidate.name,
                    "length": len(candidate.name),
                    "score": candidate.score,
                    "style": candidate.style,
                    "api_lookup_performed": False,
                },
                indent=2,
            )
        )
        return

    queue_path = Path(os.environ.get("RETRY_QUEUE_PATH", DEFAULT_QUEUE_PATH))
    queue = load_retry_queue(queue_path)
    now = datetime.now(timezone.utc)
    candidate, is_retry = select_check_target(run_number, pool, queue, now)
    print(
        f"Selected exactly one {'retry' if is_retry else 'new candidate'}: "
        f"{candidate.name} (score {candidate.score:.1f})"
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
    webhook_url = os.environ.get("DISCORD_WEBHOOK", "")
    send_to_discord(webhook_url, payload)
    save_retry_queue(queue_path, queue)
    print("Discord embed sent successfully.")


if __name__ == "__main__":
    main()
