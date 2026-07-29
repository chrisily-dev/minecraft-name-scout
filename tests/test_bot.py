import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from bot import (
    ALWAYS_NOTIFY_ROLE,
    AVAILABLE_WEBHOOK_ENV,
    AvailabilityResult,
    DELETING_WEBHOOK_ENV,
    DISCORD_ERROR_COLOR,
    DISCORD_PENDING_COLOR,
    DISCORD_SUSPENDED_COLOR,
    SUSPENDED_WEBHOOK_ENV,
    MAX_CHECKS_PER_RUN,
    ServerDetails,
    Webhooks,
    MIN_REQUEST_INTERVAL_SECONDS,
    MINEHUT_LOOKUP_URL,
    NAME_WATCHERS,
    PENDING_DELETION_REFRESH,
    TAKEN_WEBHOOK_ENV,
    build_mentions,
    build_payload,
    WATCH_REFRESH,
    check_name_availability,
    due_watchlist_names,
    is_retry_slot,
    load_retry_queue,
    main,
    resolve_webhooks,
    record_watch_check,
    save_retry_queue,
    select_check_target,
    select_webhook,
    selection_number,
    send_to_discord,
    update_retry_queue,
    validate_rate_settings,
    watch_slots,
)
from name_generator import WATCHLIST_NAMES, Candidate


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
    # An unwatched but available name pings the role, and nothing else.
    assert payload["content"] == f"<@&{ALWAYS_NOTIFY_ROLE}>"
    assert payload["allowed_mentions"] == {
        "parse": [],
        "roles": [ALWAYS_NOTIFY_ROLE],
    }


def test_a_taken_name_never_pings_the_role(candidate: Candidate) -> None:
    """Nearly every check comes back taken, so the role must stay quiet."""
    taken = AvailabilityResult(False, "Already registered.", 200)

    payload = build_payload(candidate, taken)

    assert "content" not in payload
    assert payload["allowed_mentions"] == {"parse": []}


def test_a_taken_name_never_pings_a_watcher_either() -> None:
    """A ping always means the name is claimable right now."""
    taken = AvailabilityResult(False, "Already registered.", 200)

    payload = build_payload(Candidate("Harbor", 12.0, "Watchlist", "test"), taken)

    assert "content" not in payload
    assert payload["allowed_mentions"] == {"parse": []}


def test_a_watcher_hears_when_their_name_is_marked_for_deletion() -> None:
    """The advance warning is the whole point of watching a name."""
    watcher = NAME_WATCHERS["harbor"][0]
    pending = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=True)
    )

    payload = build_payload(Candidate("Harbor", 12.0, "Watchlist", "test"), pending)

    assert payload["content"] == f"<@{watcher}>"
    # The role is not pinged: it only hears about names that are open now.
    assert payload["allowed_mentions"] == {"parse": [], "users": [watcher]}


def test_an_unwatched_name_marked_for_deletion_pings_nobody() -> None:
    pending = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=True)
    )

    payload = build_payload(Candidate("Tycoon", 14.2, "Brand word", "test"), pending)

    assert "content" not in payload
    assert payload["allowed_mentions"] == {"parse": []}


def test_no_embed_carries_a_footer() -> None:
    """The old footer repeated the same rate-limit blurb on every message."""
    available = AvailabilityResult(True, "No registered server found.", 404)
    taken = AvailabilityResult(False, "Already registered.", 200)
    candidate = Candidate("Tycoon", 14.2, "Brand word", "test")

    for result in (available, taken):
        embed = build_payload(candidate, result)["embeds"][0]

        assert "footer" not in embed
        assert "timestamp" in embed


def test_available_watched_name_pings_the_role_and_the_watcher(
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
    assert build_mentions("HARBOUR", available=True) == build_mentions(
        "harbour", available=True
    )
    assert NAME_WATCHERS["harbour"][0] in build_mentions("Harbour", available=True)[0]


def test_mentions_never_let_discord_parse_anything_else() -> None:
    for name in ("Tycoon", "Harbor", "Dungeons"):
        for available in (True, False):
            _, allowed = build_mentions(name, available=available)

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
    # A taken name cannot be created, so the link is not offered.
    assert "Minehut" not in fields
    assert "Type" not in fields
    # The title and description already say the name is taken. A third
    # restatement was pure noise on the busiest channels.
    assert "Result" not in fields


def test_an_available_embed_keeps_the_result_line(
    candidate: Candidate,
    available: AvailabilityResult,
) -> None:
    fields = {
        f["name"]: f["value"]
        for f in build_payload(candidate, available)["embeds"][0]["fields"]
    }

    assert fields["Result"] == "No registered server found."


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
DELETING_URL = "https://discord.com/api/webhooks/example/deleting"

SUSPENDED_URL = "https://discord.com/api/webhooks/example/suspended"

ALL_WEBHOOKS = Webhooks(
    available=AVAILABLE_URL,
    taken=TAKEN_URL,
    deleting=DELETING_URL,
    suspended=SUSPENDED_URL,
)


def _details(
    *, deleting: bool = False, suspended: bool = False, reason: str = ""
) -> ServerDetails:
    return ServerDetails(
        created_at=None,
        last_online=None,
        online=False,
        joins=0,
        deletion_started=deleting,
        deletion_reason=reason,
        suspended=suspended,
        plan="FREE",
    )


def test_each_outcome_routes_to_its_own_channel(
    available: AvailabilityResult,
) -> None:
    taken = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=False)
    )
    pending = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=True)
    )

    assert select_webhook(available, webhooks=ALL_WEBHOOKS) == (
        AVAILABLE_URL,
        AVAILABLE_WEBHOOK_ENV,
    )
    assert select_webhook(taken, webhooks=ALL_WEBHOOKS) == (
        TAKEN_URL,
        TAKEN_WEBHOOK_ENV,
    )
    # The whole point of the third channel: these are about to free up.
    assert select_webhook(pending, webhooks=ALL_WEBHOOKS) == (
        DELETING_URL,
        DELETING_WEBHOOK_ENV,
    )


def test_a_taken_name_without_details_is_not_treated_as_pending() -> None:
    bare = AvailabilityResult(False, "Already registered.", 200)

    assert select_webhook(bare, webhooks=ALL_WEBHOOKS) == (
        TAKEN_URL,
        TAKEN_WEBHOOK_ENV,
    )


def test_a_pending_name_gets_its_own_colour() -> None:
    pending = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=True)
    )
    taken = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=False)
    )
    candidate = Candidate("Empire", 10.0, "test", "test")

    assert (
        build_payload(candidate, pending)["embeds"][0]["color"]
        == DISCORD_PENDING_COLOR
    )
    assert (
        build_payload(candidate, taken)["embeds"][0]["color"] == DISCORD_ERROR_COLOR
    )


def test_a_suspended_server_gets_its_own_channel() -> None:
    suspended = AvailabilityResult(
        False, "Already registered.", 200, _details(suspended=True)
    )

    assert select_webhook(suspended, webhooks=ALL_WEBHOOKS) == (
        SUSPENDED_URL,
        SUSPENDED_WEBHOOK_ENV,
    )


def test_deletion_outranks_suspension_when_a_server_is_both() -> None:
    """Observed on a real server: suspended and flagged STARTER_OVER_CAP."""
    both = AvailabilityResult(
        False,
        "Already registered.",
        200,
        _details(deleting=True, suspended=True, reason="STARTER_OVER_CAP"),
    )

    # The scheduled removal is the fact worth acting on.
    assert select_webhook(both, webhooks=ALL_WEBHOOKS) == (
        DELETING_URL,
        DELETING_WEBHOOK_ENV,
    )


def test_a_suspended_server_gets_its_own_colour() -> None:
    candidate = Candidate("Zyptrik", 10.0, "test", "test")
    suspended = AvailabilityResult(
        False, "Already registered.", 200, _details(suspended=True)
    )
    plain = AvailabilityResult(False, "Already registered.", 200, _details())

    assert (
        build_payload(candidate, suspended)["embeds"][0]["color"]
        == DISCORD_SUSPENDED_COLOR
    )
    assert (
        build_payload(candidate, plain)["embeds"][0]["color"] == DISCORD_ERROR_COLOR
    )


def test_the_embed_states_both_flags_and_the_deletion_reason() -> None:
    candidate = Candidate("Complex", 10.0, "test", "test")
    flagged = AvailabilityResult(
        False,
        "Already registered.",
        200,
        _details(deleting=True, suspended=True, reason="STARTER_OVER_CAP"),
    )
    plain = AvailabilityResult(False, "Already registered.", 200, _details())

    hot = {f["name"]: f["value"] for f in build_payload(candidate, flagged)["embeds"][0]["fields"]}
    cold = {f["name"]: f["value"] for f in build_payload(candidate, plain)["embeds"][0]["fields"]}

    assert hot["Suspended"] == "This server is suspended by Minehut."
    assert "marked for Deletion" in hot["Deletion Status"]
    assert "STARTER_OVER_CAP" in hot["Deletion Status"]

    # Both states are always stated, so silence is never the answer.
    assert cold["Suspended"] == "This server is not suspended."
    assert "not marked for deletion" in cold["Deletion Status"]
    # No reason line when nothing is scheduled.
    assert "Reason:" not in cold["Deletion Status"]


def test_resolve_webhooks_reads_all_four_channels() -> None:
    resolved = resolve_webhooks(
        {
            AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL,
            TAKEN_WEBHOOK_ENV: TAKEN_URL,
            DELETING_WEBHOOK_ENV: DELETING_URL,
            SUSPENDED_WEBHOOK_ENV: SUSPENDED_URL,
        }
    )

    assert resolved == ALL_WEBHOOKS


def test_suspended_falls_back_to_the_taken_channel(capsys) -> None:
    resolved = resolve_webhooks(
        {
            AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL,
            TAKEN_WEBHOOK_ENV: TAKEN_URL,
            DELETING_WEBHOOK_ENV: DELETING_URL,
        }
    )

    assert resolved.suspended == TAKEN_URL
    assert SUSPENDED_WEBHOOK_ENV in capsys.readouterr().out


def test_pending_deletion_falls_back_to_the_taken_channel(capsys) -> None:
    """An unset secret must change nothing, not drop messages."""
    resolved = resolve_webhooks(
        {
            AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL,
            TAKEN_WEBHOOK_ENV: TAKEN_URL,
        }
    )

    assert resolved.deleting == TAKEN_URL
    assert DELETING_WEBHOOK_ENV in capsys.readouterr().out


def test_resolve_webhooks_falls_back_when_the_split_is_unset(capsys) -> None:
    resolved = resolve_webhooks({AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL})

    assert resolved == Webhooks(
        AVAILABLE_URL, AVAILABLE_URL, AVAILABLE_URL, AVAILABLE_URL
    )
    assert TAKEN_WEBHOOK_ENV in capsys.readouterr().out


def test_resolve_webhooks_rejects_a_malformed_taken_url() -> None:
    with pytest.raises(ValueError, match=TAKEN_WEBHOOK_ENV):
        resolve_webhooks(
            {
                AVAILABLE_WEBHOOK_ENV: AVAILABLE_URL,
                TAKEN_WEBHOOK_ENV: "https://example.com/not-discord",
            }
        )


SERVER_PAYLOAD = {
    "server": {
        "_id": "5a5c07d79e8f962972a2bf84",
        "name": "Empire",
        "creation": 1515980759159,
        "last_online": 1784404485473,
        "online": False,
        "joins": 260,
        "activeServerPlan": "Starter",
        "deletion": {"started": False},
        "deleted": False,
    }
}


def test_taken_result_carries_the_holding_server_details() -> None:
    response = Mock(status_code=200)
    response.json.return_value = SERVER_PAYLOAD
    session = Mock()
    session.get.return_value = response

    result = check_name_availability("Empire", session=session)

    assert result.available is False
    assert result.details is not None
    assert result.details.created_at == datetime(
        2018, 1, 15, 1, 45, 59, 159000, tzinfo=timezone.utc
    )
    assert result.details.online is False
    assert result.details.joins == 260
    assert result.details.deletion_started is False


def test_taken_embed_renders_timestamps_for_discord() -> None:
    response = Mock(status_code=200)
    response.json.return_value = SERVER_PAYLOAD
    session = Mock()
    session.get.return_value = response
    result = check_name_availability("Empire", session=session)

    embed = build_payload(Candidate("Empire", 10.0, "test", "test"), result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}

    # Discord renders <t:...> in the reader's own timezone.
    assert fields["Last online"].startswith("<t:1784404485:R>")
    assert "<t:1515980759:d>" in fields["Created"]
    assert "260 joins" in fields["Activity"]


def test_a_server_being_deleted_is_called_out() -> None:
    payload = {"server": dict(SERVER_PAYLOAD["server"], deletion={"started": True})}
    response = Mock(status_code=200)
    response.json.return_value = payload
    session = Mock()
    session.get.return_value = response
    result = check_name_availability("Empire", session=session)

    embed = build_payload(Candidate("Empire", 10.0, "test", "test"), result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}

    assert fields["Deletion Status"] == "This server is marked for Deletion."


def test_missing_server_fields_do_not_break_the_embed() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {"server": {"_id": "x", "name": "Empire"}}
    session = Mock()
    session.get.return_value = response
    result = check_name_availability("Empire", session=session)

    embed = build_payload(Candidate("Empire", 10.0, "test", "test"), result)["embeds"][0]
    fields = {field["name"]: field["value"] for field in embed["fields"]}

    assert fields["Last online"] == "Never started"
    assert "Created" not in fields
    # Stated even on a sparse payload, so silence is never the answer.
    assert fields["Deletion Status"] == "This server is not marked for deletion yet."


def test_an_available_result_has_no_holder_fields(
    candidate: Candidate,
    available: AvailabilityResult,
) -> None:
    embed = build_payload(candidate, available)["embeds"][0]
    fields = {field["name"] for field in embed["fields"]}

    assert fields.isdisjoint(
        {"Last online", "Created", "Activity", "Deletion Status"}
    )
    # The create link is only offered where it would actually work.
    assert "Minehut" in fields


def test_a_deleting_name_is_rechecked_on_the_short_cycle() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue: dict[str, object] = {"version": 1, "items": []}
    pending = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=True)
    )

    status = update_retry_queue(
        queue,
        Candidate("Empire", 10.0, "test", "test"),
        pending,
        is_retry=False,
        now=now,
    )

    assert len(queue["items"]) == 1
    entry = queue["items"][0]
    assert entry["pending_deletion"] is True
    assert datetime.fromisoformat(entry["retry_after"]) == now + PENDING_DELETION_REFRESH
    assert "every 3h" in status


def test_a_deleting_name_stays_on_the_cycle_across_retries() -> None:
    """An ordinary name leaves after one retry; this one must not."""
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue: dict[str, object] = {"version": 1, "items": []}
    pending = AvailabilityResult(
        False, "Already registered.", 200, _details(deleting=True)
    )
    candidate = Candidate("Empire", 10.0, "test", "test")

    for cycle in range(3):
        moment = now + PENDING_DELETION_REFRESH * cycle
        update_retry_queue(queue, candidate, pending, is_retry=cycle > 0, now=moment)
        assert len(queue["items"]) == 1, f"dropped on cycle {cycle}"

    assert queue["items"][0]["pending_deletion"] is True


def test_a_deleting_name_leaves_the_queue_once_it_frees_up() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue: dict[str, object] = {"version": 1, "items": []}
    candidate = Candidate("Empire", 10.0, "test", "test")

    update_retry_queue(
        queue,
        candidate,
        AvailabilityResult(False, "Already registered.", 200, _details(deleting=True)),
        is_retry=False,
        now=now,
    )
    update_retry_queue(
        queue,
        candidate,
        AvailabilityResult(True, "No registered server found.", 404),
        is_retry=True,
        now=now + PENDING_DELETION_REFRESH,
    )

    assert queue["items"] == []


def test_a_deletion_that_is_called_off_returns_to_the_slow_cycle() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue: dict[str, object] = {"version": 1, "items": []}
    candidate = Candidate("Empire", 10.0, "test", "test")

    update_retry_queue(
        queue,
        candidate,
        AvailabilityResult(False, "Already registered.", 200, _details(deleting=True)),
        is_retry=False,
        now=now,
    )
    # No longer marked, so it should stop occupying a short-cycle slot.
    update_retry_queue(
        queue,
        candidate,
        AvailabilityResult(False, "Already registered.", 200, _details(deleting=False)),
        is_retry=True,
        now=now + PENDING_DELETION_REFRESH,
    )

    assert queue["items"] == []


def test_given_names_are_kept_out_of_the_pool() -> None:
    from name_generator import build_candidate_pool

    names = {candidate.name.casefold() for candidate in build_candidate_pool()}

    # wordfreq ranks tokens by how often they appear in text, so first names
    # score higher than most real vocabulary and flooded the pool.
    assert {
        "anna", "david", "sarah", "emma", "peter", "daniel", "maria",
        "michael", "jennifer", "oliver", "simon", "nancy",
    }.isdisjoint(names)

    # Names that are also ordinary words stay, because they are decent names.
    assert {"grace", "frank"} <= names


def test_a_watched_name_comes_due_after_the_refresh_window() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    just_checked = (now - timedelta(minutes=5)).isoformat()
    stale = (now - timedelta(hours=2)).isoformat()
    queue = {
        "version": 1,
        "items": [],
        "watch_checks": {"harbor": just_checked, "harbour": stale},
    }

    due = due_watchlist_names(queue, now)

    assert "Harbor" not in due, "checked 5 minutes ago, not due yet"
    assert "Harbour" in due, "checked 2 hours ago, overdue"
    # Never-checked names are due and sort ahead of merely stale ones.
    assert due[0] not in ("Harbour",)


def test_recording_a_check_stops_a_name_being_due() -> None:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue: dict[str, object] = {"version": 1, "items": []}

    assert "Harbor" in due_watchlist_names(queue, now)
    record_watch_check(queue, "Harbor", now)

    assert "Harbor" not in due_watchlist_names(queue, now)
    # And it comes back once the window has passed.
    assert "Harbor" in due_watchlist_names(queue, now + WATCH_REFRESH)


def test_a_watched_name_is_never_parked_in_the_retry_queue() -> None:
    """Queueing a watched name would make it ineligible for a whole day."""
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    queue: dict[str, object] = {"version": 1, "items": []}
    taken = AvailabilityResult(False, "Already registered.", 200)

    status = update_retry_queue(
        queue,
        Candidate("Harbor", 14.0, "Watchlist", "test"),
        taken,
        is_retry=False,
        now=now,
    )

    assert queue["items"] == []
    assert "Watched name" in status


def test_watched_names_cannot_swallow_a_whole_batch() -> None:
    # Six watched names must not starve the ordinary rotation.
    assert watch_slots(80) == 20
    assert watch_slots(8) == 2
    assert watch_slots(3) == 1
    assert watch_slots(1) == 1


def test_main_checks_due_watched_names_first(monkeypatch, tmp_path) -> None:
    queue_path = tmp_path / "retry_queue.json"
    pool = [Candidate(f"Filler{i:02d}", 5.0, "test", "test") for i in range(30)]
    check = Mock(
        return_value=AvailabilityResult(True, "No registered server found.", 404)
    )

    monkeypatch.setattr("bot.build_candidate_pool", lambda: pool)
    monkeypatch.setattr("bot.check_name_availability", check)
    monkeypatch.setattr("bot.send_to_discord", Mock())
    monkeypatch.setattr("bot.time.sleep", Mock())
    monkeypatch.setenv(AVAILABLE_WEBHOOK_ENV, AVAILABLE_URL)
    monkeypatch.setenv(TAKEN_WEBHOOK_ENV, TAKEN_URL)
    monkeypatch.setenv("RETRY_QUEUE_PATH", str(queue_path))
    monkeypatch.setattr(
        "sys.argv",
        ["bot.py", "--run-number", "1", "--checks-per-run", "20",
         "--request-interval-seconds", "13"],
    )

    main()

    checked = [call.args[0] for call in check.call_args_list]
    watched = {name.casefold() for name in WATCHLIST_NAMES}
    leading = [name for name in checked[:5] if name.casefold() in watched]

    # Every watched name is due on a fresh queue, so they lead the batch.
    assert len(leading) == 5
    # And the cap leaves room for ordinary names.
    assert any(name.casefold() not in watched for name in checked)

    # The cap admits five of the six, and the leftover rolls into the next
    # batch rather than being dropped.
    saved = load_retry_queue(queue_path)
    assert set(saved["watch_checks"]) == {
        name for name in watched if name in saved["watch_checks"]
    }
    assert len(saved["watch_checks"]) == watch_slots(20)
    assert set(saved["watch_checks"]) < watched


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
    # Leaving the queue is what makes a name selectable again, so the wording
    # must not read as the bot giving up on it.
    assert "back of the line" in status.casefold()
    assert "removed" not in status.casefold()


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
    # Mark every watched name as freshly checked so none are due. This test is
    # about where a retry lands, not about the watch cadence.
    fresh = datetime.now(timezone.utc).isoformat()
    save_retry_queue(
        queue_path,
        {
            "version": 1,
            "watch_checks": {name.casefold(): fresh for name in WATCHLIST_NAMES},
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
