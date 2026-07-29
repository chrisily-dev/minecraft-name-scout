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
        "nylongn",
        "gmini",
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


def test_no_compounds_are_generated_at_all() -> None:
    """Only the three hand-listed category names may carry a second word."""
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    assert {"randomkits", "boxpvp", "genpvp"} <= names
    assert {
        # Former stem-times-suffix output.
        "voidgens",
        "novamines",
        "riftclash",
        "valorpvp",
        "stormgens",
        "ironmines",
        "emberpvp",
        "amberhub",
        # Former common-word-times-suffix output, the FillPvP shape.
        "fillpvp",
        "openkits",
        "workcraft",
        # Former hand-listed compounds.
        "woolgens",
        "gensfood",
        "loopgens",
        "acidgens",
        "adonismine",
        "nestmines",
        "flagclash",
    }.isdisjoint(names)


def test_the_only_compounds_are_the_three_allowed_ones() -> None:
    pool = build_candidate_pool()
    allowed = {"randomkits", "boxpvp", "genpvp"}
    # Whole words that merely end in these letters are fine; a compound is a
    # real word with a game mode bolted on, which is what must not appear.
    modes = ("pvp", "hub", "kits", "gens", "smp", "clash")

    offenders = {
        candidate.name
        for candidate in pool
        if candidate.name.casefold() not in allowed
        and any(
            candidate.name.casefold().endswith(mode)
            and len(candidate.name) > len(mode)
            for mode in modes
        )
    }

    assert offenders == set(), f"unexpected compounds: {sorted(offenders)}"


def test_pool_drops_mc_tags_and_existing_servers() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    assert not any(name.endswith("mc") for name in names)
    assert {
        "warzone",
        "towerdefense",
        "zedarmc",
        "labsmc",
        "capecraft",
        "lifesteal",
        "hypixel",
        "notch",
    }.isdisjoint(names)


def test_a_second_word_is_always_a_game_mode() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    # RandomKits and BoxPvP are fine; FrostHaven and PixelCove are the shape
    # that got dropped, along with prefix forms such as MineTycoon.
    assert {"randomkits", "boxpvp"} <= names
    assert {
        "frosthaven",
        "pixelcove",
        "ashenvale",
        "dawnharbor",
        "echovalley",
        "minetycoon",
        "skyempire",
        "gensrune",
    }.isdisjoint(names)


def test_watchlist_names_reach_the_pool() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    assert {
        "harbor",
        "harbour",
        "sete",
        "dungeon",
        "dungeons",
        "dunheon",
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
