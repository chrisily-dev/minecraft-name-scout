from better_profanity import profanity

from name_generator import (
    BRAND_WORDS,
    PRISON_ESCAPE_WORDS,
    WATCHLIST_NAMES,
    Candidate,
    _base_score,
    build_candidate_pool,
    is_valid_name,
    select_candidate,
)


def test_name_constraints() -> None:
    assert is_valid_name("PrisonEscape")
    assert is_valid_name("Harbor")
    assert not is_valid_name("abc")
    assert not is_valid_name("ThirteenChars")
    assert not is_valid_name("Box-PvP")


def test_pool_is_a_small_clean_shortlist() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    assert 200 <= len(pool) <= 500
    assert all(is_valid_name(candidate.name) for candidate in pool)
    assert all(not profanity.contains_profanity(candidate.name) for candidate in pool)
    assert len(names) == len(pool)


def test_wishlist_is_preserved_and_ranked_first() -> None:
    pool = build_candidate_pool()
    first_names = {candidate.name for candidate in pool[:len(WATCHLIST_NAMES)]}

    assert first_names == set(WATCHLIST_NAMES)
    assert {candidate.style for candidate in pool[:len(WATCHLIST_NAMES)]} == {"Watchlist"}


def test_prison_escape_words_lead_the_broader_names() -> None:
    pool = build_candidate_pool()
    positions = {candidate.name: index for index, candidate in enumerate(pool)}
    themed = {"Escape", "Breakout", "Outlaw", "Warden", "Refuge", "Liberty", "Vault"}

    assert themed <= positions.keys()
    assert max(positions[name] for name in themed) < min(
        positions[name] for name in ("Nova", "Ember", "Cloud", "Grove")
    )
    assert sum(candidate.style == "PrisonEscape word" for candidate in pool[:100]) >= 60


def test_broader_wishlist_style_words_are_still_included() -> None:
    names = {candidate.name for candidate in build_candidate_pool()}

    assert {"Beacon", "Frontier", "Citadel", "Summit", "Drift", "Realm", "Basalt"} <= names


def test_dictionary_filler_and_awkward_archive_words_are_gone() -> None:
    names = {candidate.name.casefold() for candidate in build_candidate_pool()}

    assert {
        "installed", "evaluator", "refocuses", "coheres", "caroms", "valuation",
        "listeners", "modelling", "notably", "steadily", "promptly", "compiled",
        "adding", "allows", "because", "barely", "tony", "jose", "kenya", "fifa",
    }.isdisjoint(names)


def test_no_game_mode_suffixes_or_unrequested_compounds_are_added() -> None:
    names = {candidate.name.casefold() for candidate in build_candidate_pool()}

    assert {
        "randomkits", "boxpvp", "genpvp", "voidgens", "novamines", "valorpvp",
        "emberpvp", "amberhub", "fillpvp", "openkits", "woolgens", "gensfood",
        "minetycoon", "frosthaven", "pixelcove", "prisonhub", "escapepvp",
    }.isdisjoint(names)
    assert {"prisonrp", "prisonescape", "raidrise"} <= names
    assert not any(name.endswith(("mc", "pvp", "gens", "hub", "kits", "smp")) for name in names)


def test_reviewed_names_are_legal_and_safe() -> None:
    names = WATCHLIST_NAMES + PRISON_ESCAPE_WORDS + BRAND_WORDS

    assert all(is_valid_name(name) for name in names)
    assert all(not profanity.contains_profanity(name) for name in names)


def test_recognizable_short_words_receive_priority_within_a_group() -> None:
    assert min(
        _base_score("Loud", 4.53),
        _base_score("Cloud", 4.53),
        _base_score("Planet", 4.53),
    ) > _base_score("Example", 4.53)


def test_selection_is_deterministic_and_returns_one_candidate() -> None:
    pool = [
        Candidate("Escape", 10.0, "PrisonEscape word", "test"),
        Candidate("Harbor", 9.0, "Watchlist", "test"),
        Candidate("Realm", 8.0, "Brand word", "test"),
    ]

    assert select_candidate(10, pool) == select_candidate(10, pool)
    assert select_candidate(10, pool) in pool


def test_successive_runs_walk_the_entire_pool_without_repeating() -> None:
    pool = build_candidate_pool()
    selected = [select_candidate(run, pool).name for run in range(1, len(pool) + 1)]

    assert len(set(selected)) == len(pool)
    assert select_candidate(len(pool) + 1, pool) == pool[0]
