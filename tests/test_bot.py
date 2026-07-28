from unittest.mock import Mock

import pytest

from bot import (
    AvailabilityResult,
    MINEHUT_LOOKUP_URL,
    build_payload,
    check_name_availability,
    send_to_discord,
)
from name_generator import Candidate


@pytest.fixture
def candidate() -> Candidate:
    return Candidate("Tycoon", 14.2, "Brand word", "common English")


@pytest.fixture
def available() -> AvailabilityResult:
    return AvailabilityResult(True, "No registered server found.", 404)


def test_payload_uses_one_embed(
    candidate: Candidate,
    available: AvailabilityResult,
) -> None:
    payload = build_payload(candidate, available)

    assert "content" not in payload
    assert len(payload["embeds"]) == 1
    assert "`Tycoon`" in payload["embeds"][0]["description"]
    assert payload["embeds"][0]["title"] == "Available Minehut Server Name"
    assert payload["allowed_mentions"] == {"parse": []}


def test_webhook_validation_rejects_missing_url(
    candidate: Candidate,
    available: AvailabilityResult,
) -> None:
    with pytest.raises(ValueError):
        send_to_discord("", build_payload(candidate, available))


def test_webhook_request_has_timeout(
    candidate: Candidate,
    available: AvailabilityResult,
) -> None:
    response = Mock()
    response.raise_for_status = Mock()
    session = Mock()
    session.post.return_value = response

    payload = build_payload(candidate, available)
    send_to_discord(
        "https://discord.com/api/webhooks/example/token",
        payload,
        session=session,
    )

    session.post.assert_called_once_with(
        "https://discord.com/api/webhooks/example/token",
        json=payload,
        timeout=15,
    )
    response.raise_for_status.assert_called_once_with()


def test_404_means_available_with_exactly_one_lookup() -> None:
    response = Mock(status_code=404)
    session = Mock()
    session.get.return_value = response

    result = check_name_availability("Tycoon", session=session)

    assert result.available is True
    session.get.assert_called_once()
    assert session.get.call_args.args[0] == MINEHUT_LOOKUP_URL.format(name="Tycoon")


def test_empty_server_object_means_available() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"server": None}
    session = Mock()
    session.get.return_value = response

    result = check_name_availability("Tycoon", session=session)

    assert result.available is True
    response.raise_for_status.assert_called_once_with()


def test_existing_server_does_not_notify() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"server": {"_id": "existing"}}
    session = Mock()
    session.get.return_value = response

    result = check_name_availability("Tycoon", session=session)

    assert result.available is False
