from name_generator import (
    Candidate,
    _base_score,
    build_candidate_pool,
    is_valid_name,
    select_candidate,
)


def test_name_constraints() -> None:
    assert is_valid_name("Tycoon")
    assert is_valid_name("RandomKits")
    assert not is_valid_name("abc")
    assert not is_valid_name("ThirteenChars")
    assert not is_valid_name("Box-PvP")


def test_pool_contains_requested_style_and_no_obscure_shapes() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    expected = {
        "tycoon",
        "sales",
        "installed",
        "open",
        "flee",
        "loud",
        "zombie",
        "prison",
        "mining",
        "farming",
        "dancer",
        "major",
        "mayor",
        "flat",
        "platform",
        "randomkits",
        "boxpvp",
        "genpvp",
        "gens",
        "woolgens",
        "gensfood",
        "loopgens",
        "acidgens",
        "adonismine",
        "nestmines",
        "nylongn",
        "gmini",
        "flagclash",
        "beans",
        "valknet",
        "harbor",
        "ashen",
        "basalt",
        "cabin",
        "drift",
        "ember",
        "flint",
        "grove",
        "backseat",
        "formwork",
        "trackball",
        "bulwarks",
        "refocuses",
        "skydives",
        "measures",
        "managed",
        "ongoing",
        "notably",
        "deployed",
        "promptly",
        "steadily",
        "compiled",
        "fixtures",
        "specialty",
        "patents",
        "embraced",
        "feasible",
        "valuation",
        "portraits",
        "fulfilled",
        "listeners",
        "modelling",
        "spurs",
        "warzone",
        "towerdefense",
    }
    assert expected <= names
    assert len(pool) >= 4_000
    assert all(is_valid_name(candidate.name) for candidate in pool)
    assert len(names) == len(pool)


def test_pool_is_mostly_standalone_words() -> None:
    pool = build_candidate_pool()
    limited_suffixes = (
        "craft",
        "gens",
        "hub",
        "kits",
        "mc",
        "mines",
        "pvp",
        "smp",
    )

    suffix_names = [
        candidate
        for candidate in pool
        if any(candidate.name.casefold().endswith(suffix) for suffix in limited_suffixes)
    ]
    first_hundred_suffix_names = [
        candidate
        for candidate in pool[:100]
        if any(candidate.name.casefold().endswith(suffix) for suffix in limited_suffixes)
    ]

    assert len(suffix_names) <= len(pool) // 5
    assert len(first_hundred_suffix_names) <= 20


def test_recognizable_short_words_receive_priority() -> None:
    pool = build_candidate_pool()
    positions = {
        candidate.name.casefold(): index
        for index, candidate in enumerate(pool, start=1)
    }
    short_names = [
        candidate
        for candidate in pool[:100]
        if 4 <= len(candidate.name) <= 6
    ]

    assert positions["loud"] <= 500
    assert len(short_names) >= 50
    assert min(
        _base_score("Loud", 4.53),
        _base_score("Cloud", 4.53),
        _base_score("Planet", 4.53),
    ) > _base_score("Example", 4.53)


def test_semantic_families_generate_more_than_the_examples() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    assert {
        "voidgens",
        "gensrune",
        "novamines",
        "riftclash",
        "valorpvp",
        "frosthaven",
        "pixelcove",
        "stormgens",
        "ironmines",
        "emberpvp",
    } <= names


def test_selection_is_deterministic_and_returns_one_candidate() -> None:
    pool = [
        Candidate("Tycoon", 10.0, "Brand word", "test"),
        Candidate("BoxPvP", 9.0, "PvP server", "test"),
        Candidate("Gens", 8.0, "Minecraft anchor", "test"),
    ]

    assert select_candidate(10, pool) == select_candidate(10, pool)
    assert select_candidate(10, pool) in pool


def test_successive_runs_walk_the_pool_without_repeating() -> None:
    pool = [
        Candidate(f"Name{suffix}", 10.0, "test", "test")
        for suffix in ("Four", "Five", "Sixx", "Sevn", "Eight")
    ]
    selected = [select_candidate(run, pool).name for run in range(1, len(pool) + 1)]

    assert len(set(selected)) == len(pool)
