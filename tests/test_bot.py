import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from bot import (
    ALWAYS_NOTIFY_ROLE,
    AVAILABLE_WEBHOOK_ENV,
    AvailabilityResult,
    DISCORD_ERROR_COLOR,
    MAX_CHECKS_PER_RUN,
    MIN_REQUEST_INTERVAL_SECONDS,
    MINEHUT_LOOKUP_URL,
    NAME_WATCHERS,
    TAKEN_WEBHOOK_ENV,
    build_mentions,
    build_payload,
    check_name_availability,
    is_retry_slot,
    load_retry_queue,
    main,
    resolve_webhooks,
    save_retry_queue,
    select_check_target,
    select_webhook,
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

    assert len(payload["embeds"]) == 1
    assert "`Tycoon`" in payload["embeds"][0]["description"]
    assert payload["embeds"][0]["title"] == "Available: Tycoon"
    # An unwatched name still pings the role, and nothing else.
    assert payload["content"] == f"<@&{ALWAYS_NOTIFY_ROLE}>"
    assert payload["allowed_mentions"] == {
        "parse": [],
        "roles": [ALWAYS_NOTIFY_ROLE],
    }


def test_watched_name_pings_the_role_and_the_watcher(
    available: AvailabilityResult,
) -> None:
    watcher = NAME_WATCHERS["harbor"][0]

    payload = build_payload(Candidate("Harbor", 12.0, "Watchlist", "test"), available)

    assert payload["content"] == f"<@&{ALWAYS_NOTIFY_ROLE}> <@{watcher}>"
    assert payload["allowed_mentions"] == {
        "parse": [],
        "roles": [ALWAYS_NOTIFY_ROLE],
        "users": [watcher],
    }


def test_watchers_are_matched_regardless_of_casing() -> None:
    assert build_mentions("HARBOUR") == build_mentions("harbour")
    assert NAME_WATCHERS["harbour"][0] in build_mentions("Harbour")[0]


def test_mentions_never_let_discord_parse_anything_else() -> None:
    for name in ("Tycoon", "Harbor", "Dungeons"):
        _, allowed = build_mentions(name)

        # An empty "parse" is what stops @everyone or a stray role from
        # resolving; only the IDs listed here can ping.
        assert allowed["parse"] == []
        assert set(allowed) <= {"parse", "roles", "users"}


def test_every_watched_name_can_actually_be_generated() -> None:
    from name_generator import WATCHLIST_NAMES

    watchlist = {name.casefold() for name in WATCHLIST_NAMES}

    # A watcher on a name the generator never produces would never fire.
    assert set(NAME_WATCHERS) <= watchlist


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
    fields = {field["name"]: field["value"] for field in embed["fields"]}

    assert embed["title"] == "Taken: Tycoon"
    assert embed["color"] == DISCORD_ERROR_COLOR
    assert "already in use" in embed["description"]
    assert fields["Retry"] == "Queued for one retry tomorrow."
    assert fields["Minehut"] == (
        "[Create server](https://dashboard.minehut.com/servers/create)"
    )
    assert "Type" not in fields


def test_webhook_copy_is_plain_and_has_no_long_dashes(
    candidate: Candidate,
    available: AvailabilityResult,
) -> None:
    payload_text = json.dumps(build_payload(candidate, available)).casefold()

    assert "—" not in payload_text
    assert "–" not in payload_text
    assert "ranked candidate" not in payload_text
    assert "desirability score" not in payload_text
    assert "currently" not in payload_text


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


AVAILABLE_URL = "https://discord.com/api/webhooks/example/available"
TAKEN_URL = "https://discord.com/api/webhooks/example/taken"


def test_each_availability_routes_to_its_own_channel(
    available: AvailabilityResult,
) -> None:
    taken = AvailabilityResult(False, "Already registered.", 200)
    routing = {
        "available_webhook_url": AVAILABLE_URL,
        "taken_webhook_url": TAKEN_URL,
    }

    assert select_webhook(available, **routing) == (
        AVAILABLE_URL,
        AVAILABLE_WEBHOOK_ENV,
    )
    assert select_webhook(taken, **routing) == (TAKEN_URL, TAKEN_WEBHOOK_ENV)


def test_resolve_webhooks_reads_both_channels() -> None:
    resolved = resolve_webhooks(
        {
            AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL,
            TAKEN_WEBHOOK_ENV: TAKEN_URL,
        }
    )

    assert resolved == (AVAILABLE_URL, TAKEN_URL)


def test_resolve_webhooks_falls_back_when_the_split_is_unset(capsys) -> None:
    resolved = resolve_webhooks({AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL})

    assert resolved == (AVAILABLE_URL, AVAILABLE_URL)
    assert TAKEN_WEBHOOK_ENV in capsys.readouterr().out


def test_resolve_webhooks_rejects_a_malformed_taken_url() -> None:
    with pytest.raises(ValueError, match=TAKEN_WEBHOOK_ENV):
        resolve_webhooks(
            {
                AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL,
                TAKEN_WEBHOOK_ENV: "https://example.com/not-discord",
            }
        )


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

    assert "Checking again" in status
    assert len(saved["items"]) == 1
    assert saved["items"][0]["name"] == "Tycoon"
    assert datetime.fromisoformat(saved["items"][0]["retry_after"]) == (
        checked_at + timedelta(days=1)
    )


def test_due_retry_waits_for_the_bottom_check_slot(candidate: Candidate) -> None:
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
    retry, is_retry_slot = select_check_target(
        10,
        pool,
        queue,
        now,
        allow_due_retry=True,
    )

    assert selected.name == "Mining"
    assert is_retry is False
    assert retry == candidate
    assert is_retry_slot is True


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
    assert "retry finished" in status.casefold()


def test_rate_guard_enforces_the_moderator_limit() -> None:
    validate_rate_settings(MAX_CHECKS_PER_RUN, 13.0)

    with pytest.raises(ValueError):
        validate_rate_settings(MAX_CHECKS_PER_RUN + 1, 13.0)
    with pytest.raises(ValueError):
        validate_rate_settings(MAX_CHECKS_PER_RUN, MIN_REQUEST_INTERVAL_SECONDS - 0.1)


def test_retry_slots_take_the_tail_of_the_batch() -> None:
    assert [is_retry_slot(slot, 80) for slot in (0, 39, 40, 79)] == [
        False,
        False,
        True,
        True,
    ]
    # A three-check batch keeps the original single trailing retry slot.
    assert [is_retry_slot(slot, 3) for slot in (0, 1, 2)] == [False, False, True]
    # A batch is never made up entirely of retries.
    assert is_retry_slot(0, 1) is False


def test_a_backlogged_queue_actually_drains(candidate: Candidate) -> None:
    """One retry slot per run grew the queue by ~checks_per_run and drained one."""
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    due = (now - timedelta(hours=1)).isoformat()
    queue = {
        "version": 1,
        "items": [
            {
                "name": f"Queued{index:03d}",
                "score": 9.0,
                "style": "test",
                "source": "test",
                "first_checked_at": due,
                "retry_after": due,
            }
            for index in range(40)
        ],
    }
    pool = [Candidate(f"FreshNm{index:03d}", 10.0, "test", "test") for index in range(60)]

    checked: set[str] = set()
    retries = 0
    for slot in range(20):
        selected, is_retry = select_check_target(
            selection_number(1, 20, slot),
            pool,
            queue,
            now,
            excluded_names=checked,
            allow_due_retry=is_retry_slot(slot, 20),
        )
        checked.add(selected.name.casefold())
        retries += is_retry

    # Half the batch works the backlog down instead of only ever adding to it.
    assert retries == 10
    assert len(checked) == 20


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


def test_main_places_a_due_retry_in_the_final_slot(
    candidate: Candidate,
    monkeypatch,
    tmp_path,
) -> None:
    queue_path = tmp_path / "retry_queue.json"
    save_retry_queue(
        queue_path,
        {
            "version": 1,
            "items": [
                {
                    "name": candidate.name,
                    "score": candidate.score,
                    "style": candidate.style,
                    "source": candidate.source,
                    "first_checked_at": "2026-07-27T10:00:00+00:00",
                    "retry_after": "2026-07-28T10:00:00+00:00",
                }
            ],
        },
    )
    pool = [
        Candidate("FreshOne", 10.0, "test", "test"),
        Candidate("FreshTwo", 9.9, "test", "test"),
        Candidate("FreshThree", 9.8, "test", "test"),
    ]
    check = Mock(
        return_value=AvailabilityResult(True, "No registered server found.", 404)
    )

    monkeypatch.setattr("bot.build_candidate_pool", lambda: pool)
    monkeypatch.setattr("bot.check_name_availability", check)
    monkeypatch.setattr("bot.send_to_discord", Mock())
    monkeypatch.setattr("bot.time.sleep", Mock())
    monkeypatch.setenv(
        "DISCORD_WEBHOOK",
        "https://discord.com/api/webhooks/example/token",
    )
    monkeypatch.setenv("RETRY_QUEUE_PATH", str(queue_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "bot.py",
            "--run-number",
            "1",
            "--checks-per-run",
            "3",
            "--request-interval-seconds",
            "13",
        ],
    )

    main()

    checked_names = [call.args[0] for call in check.call_args_list]
    assert checked_names[:2] == ["FreshOne", "FreshTwo"]
    assert checked_names[2] == "Tycoon"


def test_main_sends_taken_names_to_the_second_webhook(
    monkeypatch,
    tmp_path,
) -> None:
    pool = [
        Candidate(f"Name{chr(65 + index)}", 10.0 - index / 100, "test", "test")
        for index in range(8)
    ]
    check = Mock(
        side_effect=[
            AvailabilityResult(True, "No registered server found.", 404),
            AvailabilityResult(False, "Already registered.", 200),
            AvailabilityResult(True, "No registered server found.", 404),
            AvailabilityResult(False, "Already registered.", 200),
        ]
    )
    send = Mock()

    monkeypatch.setattr("bot.build_candidate_pool", lambda: pool)
    monkeypatch.setattr("bot.check_name_availability", check)
    monkeypatch.setattr("bot.send_to_discord", send)
    monkeypatch.setattr("bot.time.sleep", Mock())
    monkeypatch.setenv(AVAILABLE_WEBHOOK_ENV, AVAILABLE_URL)
    monkeypatch.setenv(TAKEN_WEBHOOK_ENV, TAKEN_URL)
    monkeypatch.setenv("RETRY_QUEUE_PATH", str(tmp_path / "retry_queue.json"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "bot.py",
            "--run-number",
            "1",
            "--checks-per-run",
            "4",
            "--request-interval-seconds",
            "13",
        ],
    )

    main()

    used_urls = [call.args[0] for call in send.call_args_list]
    titles = [call.args[1]["embeds"][0]["title"] for call in send.call_args_list]

    assert used_urls == [AVAILABLE_URL, TAKEN_URL, AVAILABLE_URL, TAKEN_URL]
    assert [title.split(":")[0] for title in titles] == [
        "Available",
        "Taken",
        "Available",
        "Taken",
    ]
