from name_generator import (
    Candidate,
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
    assert len(pool) >= 500
    assert all(is_valid_name(candidate.name) for candidate in pool)
    assert len(names) == len(pool)


def test_semantic_families_generate_more_than_the_examples() -> None:
    pool = build_candidate_pool()
    names = {candidate.name.casefold() for candidate in pool}

    assert {
        "voidgens",
        "gensrune",
        "novamines",
        "riftclash",
        "valorpvp",
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
