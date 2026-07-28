"""Send one Minecraft server-name candidate to a Discord webhook."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import time

import requests

from name_generator import Candidate, build_candidate_pool, select_candidate


DISCORD_COLOR = 0x57F287
MINEHUT_DASHBOARD = "https://app.minehut.com/dashboard"
MINEHUT_LOOKUP_URL = "https://api.minehut.com/server/{name}?byName=true"
USER_AGENT = "MinecraftNameScout/2.0 (+GitHub Actions; one lookup per run)"


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


def build_embed(
    candidate: Candidate,
    availability: AvailabilityResult,
) -> dict[str, object]:
    return {
        "title": "Available Minehut Server Name",
        "description": (
            f"## `{candidate.name}`\n"
            "One dictionary-driven, Minecraft-friendly candidate passed the availability check."
        ),
        "color": DISCORD_COLOR,
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
                "name": "Claim or verify",
                "value": f"[Open the Minehut dashboard]({MINEHUT_DASHBOARD})",
                "inline": False,
            },
        ],
        "footer": {
            "text": "One API lookup per run • 4–12 letters • common vocabulary"
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def build_payload(
    candidate: Candidate,
    availability: AvailabilityResult,
) -> dict[str, object]:
    return {
        "username": "Minecraft Name Scout",
        "allowed_mentions": {"parse": []},
        "embeds": [build_embed(candidate, availability)],
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
    candidate = select_candidate(run_number, build_candidate_pool())
    print(f"Selected exactly one candidate: {candidate.name} (score {candidate.score:.1f})")

    if args.dry_run:
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

    availability = check_name_availability(candidate.name)
    print(availability.reason)
    if not availability.available:
        print("Name is already registered; no Discord message sent.")
        return

    payload = build_payload(candidate, availability)
    webhook_url = os.environ.get("DISCORD_WEBHOOK", "")
    send_to_discord(webhook_url, payload)
    print("Discord embed sent successfully.")


if __name__ == "__main__":
    main()
