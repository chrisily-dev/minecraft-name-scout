from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from bot import (
    AvailabilityResult,
    DISCORD_ERROR_COLOR,
    MINEHUT_LOOKUP_URL,
    build_payload,
    check_name_availability,
    load_retry_queue,
    main,
    save_retry_queue,
    select_check_target,
    selection_number,
    send_to_discord,
    update_retry_queue,
    validate_rate_settings,
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


def test_unavailable_payload_uses_red_embed(candidate: Candidate) -> None:
    unavailable = AvailabilityResult(
        False,
        "A registered Minehut server already uses this name.",
        200,
    )

    payload = build_payload(
        candidate,
        unavailable,
        queue_status="Queued for one retry tomorrow.",
    )
    embed = payload["embeds"][0]

    assert embed["title"] == "Minehut Server Name Unavailable"
    assert embed["color"] == DISCORD_ERROR_COLOR
    assert "currently **unavailable**" in embed["description"]
    assert embed["fields"][4]["value"] == "Queued for one retry tomorrow."


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


def test_existing_server_is_unavailable() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"server": {"_id": "existing"}}
    session = Mock()
    session.get.return_value = response

    result = check_name_availability("Tycoon", session=session)

    assert result.available is False


def test_new_unavailable_name_is_queued_for_tomorrow(
    candidate: Candidate,
    tmp_path,
) -> None:
    queue_path = tmp_path / "retry_queue.json"
    queue = load_retry_queue(queue_path)
    checked_at = datetime(2026, 7, 28, 10, 0, tzinfo=timezone.utc)
    unavailable = AvailabilityResult(False, "Already registered.", 200)

    status = update_retry_queue(
        queue,
        candidate,
        unavailable,
        is_retry=False,
        now=checked_at,
    )
    save_retry_queue(queue_path, queue)
    saved = load_retry_queue(queue_path)

    assert "Queued for one retry" in status
    assert len(saved["items"]) == 1
    assert saved["items"][0]["name"] == "Tycoon"
    assert datetime.fromisoformat(saved["items"][0]["retry_after"]) == (
        checked_at + timedelta(days=1)
    )


def test_due_retry_uses_the_single_check_slot(candidate: Candidate) -> None:
    now = datetime(2026, 7, 29, 10, 1, tzinfo=timezone.utc)
    queue = {
        "version": 1,
        "items": [
            {
                "name": candidate.name,
                "score": candidate.score,
                "style": candidate.style,
                "source": candidate.source,
                "first_checked_at": (now - timedelta(days=1, minutes=1)).isoformat(),
                "retry_after": (now - timedelta(minutes=1)).isoformat(),
            }
        ],
    }
    pool = [Candidate("Mining", 12.0, "Dictionary word", "common English")]

    selected, is_retry = select_check_target(10, pool, queue, now)

    assert selected == candidate
    assert is_retry is True


def test_completed_retry_is_removed_from_queue(candidate: Candidate) -> None:
    queue = {
        "version": 1,
        "items": [
            {
                "name": candidate.name,
                "score": candidate.score,
                "style": candidate.style,
                "source": candidate.source,
                "first_checked_at": "2026-07-28T10:00:00+00:00",
                "retry_after": "2026-07-29T10:00:00+00:00",
            }
        ],
    }
    unavailable = AvailabilityResult(False, "Still registered.", 200)

    status = update_retry_queue(
        queue,
        candidate,
        unavailable,
        is_retry=True,
        now=datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc),
    )

    assert queue["items"] == []
    assert "retry completed" in status.casefold()


def test_rate_guard_enforces_the_moderator_limit() -> None:
    validate_rate_settings(20, 13.0)

    with pytest.raises(ValueError):
        validate_rate_settings(21, 13.0)
    with pytest.raises(ValueError):
        validate_rate_settings(20, 12.9)


def test_batch_selection_numbers_do_not_overlap_between_runs() -> None:
    first_run = {selection_number(1, 20, slot) for slot in range(20)}
    second_run = {selection_number(2, 20, slot) for slot in range(20)}

    assert first_run == set(range(1, 21))
    assert second_run == set(range(21, 41))
    assert first_run.isdisjoint(second_run)


def test_main_sends_one_embed_for_each_of_twenty_unique_names(
    monkeypatch,
    tmp_path,
) -> None:
    pool = [
        Candidate(f"Name{chr(65 + index)}", 10.0 - index / 100, "test", "test")
        for index in range(24)
    ]
    check = Mock(
        return_value=AvailabilityResult(True, "No registered server found.", 404)
    )
    send = Mock()
    sleep = Mock()

    monkeypatch.setattr("bot.build_candidate_pool", lambda: pool)
    monkeypatch.setattr("bot.check_name_availability", check)
    monkeypatch.setattr("bot.send_to_discord", send)
    monkeypatch.setattr("bot.time.sleep", sleep)
    monkeypatch.setenv(
        "DISCORD_WEBHOOK",
        "https://discord.com/api/webhooks/example/token",
    )
    monkeypatch.setenv("RETRY_QUEUE_PATH", str(tmp_path / "retry_queue.json"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "bot.py",
            "--run-number",
            "1",
            "--checks-per-run",
            "20",
            "--request-interval-seconds",
            "13",
        ],
    )

    main()

    checked_names = [call.args[0] for call in check.call_args_list]
    assert len(checked_names) == 20
    assert len(set(checked_names)) == 20
    assert send.call_count == 20
    assert all(len(call.args[1]["embeds"]) == 1 for call in send.call_args_list)
    assert sleep.call_count == 19
    sleep.assert_called_with(13.0)
