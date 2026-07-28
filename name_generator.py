"""Generate brandable Minecraft server-name candidates."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from better_profanity import profanity
from wordfreq import top_n_list, zipf_frequency


MIN_LENGTH = 4
MAX_LENGTH = 12
POOL_LIMIT = 5_000
STANDALONE_NAMES_PER_SPECIAL = 4

# Preferred standalone words stay near the front of the pool even when their
# dictionary frequency is lower than the automatic cutoff.
CORE_STANDALONE_WORDS = (
    "Tycoon",
    "Sales",
    "Installed",
    "Open",
    "Flee",
    "Zombie",
    "Gens",
    "Prison",
    "Mining",
    "Farming",
    "Dancer",
    "Major",
    "Mayor",
    "Flat",
    "Platform",
    "Random",
    "Kits",
    "Skyblock",
    "Survival",
    "Nexus",
    "Vortex",
    "Haven",
    "Realm",
    "Empire",
    "Legacy",
    "Titan",
    "Nova",
    "Quest",
)

# These are style anchors, not a list of every name the bot can produce.
ANCHORS = (
    "Tycoon",
    "Notch",
    "Hypixel",
    "Sales",
    "Installed",
    "Mining",
    "Farming",
    "Dancer",
    "Major",
    "Mayor",
    "Flat",
    "Platform",
    "Random",
    "RandomKits",
    "BoxPvP",
    "GenPvP",
    "Kits",
    "Gens",
    "Skyblock",
    "Survival",
    "Nexus",
    "Vortex",
    "Haven",
    "Realm",
    "Empire",
    "Legacy",
    "Titan",
    "Nova",
    "Quest",
)

# Exact examples requested by the owner. They also guide the generalized
# compound families below; the Discord presentation remains this bot's own.
REQUESTED_NAMES = (
    "WoolGens",
    "GensFood",
    "LoopGens",
    "AcidGens",
    "AdonisMine",
    "NestMines",
    "NylonGN",
    "Gmini",
    "FlagClash",
    "Beans",
    "Valknet",
)

# Strong single-word shapes supplied in the reference screenshot.
REFERENCE_WORDS = (
    "Harbor",
    "Ashen",
    "Basalt",
    "Cabin",
    "Drift",
    "Ember",
    "Flint",
    "Grove",
)

# Recognizable names retained from the owner's 2022 checker archive. Hundreds
# of rare technical and dictionary-curiosity results were intentionally omitted.
ARCHIVE_INSPIRATION = (
    "Backseat",
    "Evaluator",
    "Recasting",
    "Grabby",
    "Workmates",
    "Unfurling",
    "Formwork",
    "Trackball",
    "Bulwarks",
    "Scruple",
    "Refocuses",
    "Skydives",
    "Oilskin",
    "Coheres",
    "Caroms",
)

# Recognizable names retained from the newer supplied archive. The source
# contained thousands of awkward or unsafe results, so only clean names remain.
RECENT_ARCHIVE_INSPIRATION = (
    "Measures",
    "Managed",
    "Ongoing",
    "Notably",
    "Deployed",
    "Sooner",
    "Heavier",
    "Promptly",
    "Steadily",
    "Touches",
    "Compiled",
    "Fixtures",
    "Tablets",
    "Editorial",
    "Leaning",
    "Proudly",
    "Statute",
    "Specialty",
    "Transfers",
    "Patents",
    "Embraced",
    "Feasible",
    "Valuation",
    "Portraits",
    "Fulfilled",
    "Listeners",
    "Modelling",
    "Spurs",
)

# Historical references from the official Minehut Wiki's Notable Servers
# directory. These are checked like any other candidate and are not assumed
# to be available.
NOTABLE_REFERENCES = (
    "CapeCraft",
    "Fewer",
    "FunMinesX",
    "Glowcraft",
    "HotdogWater",
    "HyruleGG",
    "LabsMC",
    "LeoneMC",
    "Lifesteal",
    "Lightskies",
    "Mlum",
    "Overcast",
    "SurvivalGG",
    "SynthCraft",
    "TowerDefense",
    "UnitedLands",
    "Warzone",
    "ZedarMC",
)

# Semantically grouped stems produce related names without relying on a fixed
# hand-written list for every combination.
GEN_STEMS = (
    "Wool",
    "Food",
    "Loop",
    "Acid",
    "Nest",
    "Void",
    "Ore",
    "Crop",
    "Flux",
    "Rune",
    "Bloom",
    "Stone",
)

MINE_STEMS = (
    "Adonis",
    "Nest",
    "Tech",
    "Rift",
    "Nova",
    "Forge",
    "Solar",
    "Titan",
    "Deep",
    "Crystal",
)

PVP_STEMS = (
    "Flag",
    "Rune",
    "Rift",
    "Titan",
    "Nova",
    "Valor",
    "Crown",
    "Blaze",
)

# Curated parts that produce natural two-part server names. Invalid results
# over the 12-character limit are discarded by the normal name validator.
BRAND_STEMS = (
    "Amber",
    "Arcane",
    "Arctic",
    "Ash",
    "Aurora",
    "Blaze",
    "Bloom",
    "Cinder",
    "Cloud",
    "Coral",
    "Cosmic",
    "Crimson",
    "Crown",
    "Crystal",
    "Dawn",
    "Dragon",
    "Dusk",
    "Echo",
    "Ember",
    "Fable",
    "Flame",
    "Forest",
    "Frost",
    "Galaxy",
    "Glacier",
    "Golden",
    "Hollow",
    "Iron",
    "Jade",
    "Lunar",
    "Mystic",
    "Neon",
    "Nova",
    "Oak",
    "Obsidian",
    "Pixel",
    "Quartz",
    "Raven",
    "Rift",
    "River",
    "Ruby",
    "Shadow",
    "Silver",
    "Solar",
    "Storm",
    "Titan",
    "Valor",
    "Velvet",
    "Void",
    "Wild",
    "Winter",
)

BRAND_SUFFIXES = (
    "Craft",
    "Gens",
    "Haven",
    "Hub",
    "Kits",
    "MC",
    "Mines",
    "PvP",
    "Realm",
    "SMP",
)

CURATED_COMPOUNDS = (
    "AshenVale",
    "BlazePeak",
    "BloomCove",
    "CloudForge",
    "CoralQuest",
    "CrownVale",
    "DawnHarbor",
    "DragonCove",
    "DuskForge",
    "EchoValley",
    "EmberPeak",
    "FableHaven",
    "FlameCove",
    "ForestVale",
    "FrostHaven",
    "FrostPeak",
    "GalaxyForge",
    "GoldenVale",
    "HollowPeak",
    "IronHaven",
    "JadeHarbor",
    "LunarVale",
    "MysticCove",
    "NeonForge",
    "NovaHarbor",
    "OakValley",
    "PixelCove",
    "QuartzPeak",
    "RavenHaven",
    "RiftValley",
    "RiverForge",
    "RubyHaven",
    "ShadowCove",
    "SilverPeak",
    "SolarVale",
    "StormHaven",
    "TitanForge",
    "ValorPeak",
    "VelvetCove",
    "VoidHarbor",
    "WildHaven",
    "WinterVale",
)

PREFIXES = (
    "Mine",
    "Craft",
    "Block",
    "Sky",
    "Box",
    "Gen",
    "Kit",
    "Pixel",
    "Nether",
    "Ender",
)

SUFFIXES = (
    "Craft",
    "PvP",
    "Kits",
    "Gens",
    "SMP",
    "Realm",
    "Hub",
    "Core",
    "MC",
)

POWER_WORDS = {
    "alpha",
    "apex",
    "blaze",
    "champion",
    "crown",
    "dynasty",
    "empire",
    "epic",
    "forge",
    "haven",
    "hero",
    "kingdom",
    "legacy",
    "legend",
    "nexus",
    "nova",
    "prime",
    "quest",
    "realm",
    "royal",
    "summit",
    "titan",
    "tycoon",
    "vortex",
}

NEGATIVE_WORDS = {
    "abuse",
    "abused",
    "cancer",
    "crime",
    "dead",
    "death",
    "dying",
    "fraud",
    "hate",
    "illness",
    "murder",
    "tax",
    "taxes",
    "terror",
    "virus",
}

STOP_WORDS = {
    "about",
    "after",
    "again",
    "also",
    "always",
    "another",
    "around",
    "away",
    "back",
    "been",
    "because",
    "before",
    "being",
    "best",
    "between",
    "both",
    "came",
    "come",
    "could",
    "does",
    "doing",
    "done",
    "down",
    "each",
    "even",
    "every",
    "find",
    "first",
    "from",
    "gave",
    "give",
    "going",
    "good",
    "great",
    "have",
    "here",
    "high",
    "however",
    "into",
    "just",
    "keep",
    "kind",
    "know",
    "last",
    "like",
    "little",
    "long",
    "look",
    "made",
    "make",
    "many",
    "might",
    "more",
    "most",
    "much",
    "must",
    "never",
    "next",
    "only",
    "other",
    "people",
    "really",
    "right",
    "said",
    "over",
    "same",
    "seen",
    "should",
    "since",
    "some",
    "still",
    "such",
    "take",
    "than",
    "that",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "think",
    "those",
    "time",
    "took",
    "through",
    "under",
    "upon",
    "used",
    "using",
    "very",
    "want",
    "well",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "will",
    "with",
    "work",
    "would",
    "year",
    "your",
}

WORD_RE = re.compile(r"^[A-Za-z]+$")
VOWELS = frozenset("aeiouy")


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    score: float
    style: str
    source: str


def is_valid_name(name: str) -> bool:
    """Return whether a name follows Minehut's documented name shape."""
    return MIN_LENGTH <= len(name) <= MAX_LENGTH and bool(WORD_RE.fullmatch(name))


def _display_word(word: str) -> str:
    return word[:1].upper() + word[1:].lower()


def _is_pronounceable(word: str) -> bool:
    lowered = word.lower()
    vowel_count = sum(char in VOWELS for char in lowered)
    return (
        vowel_count >= 1
        and not re.search(r"[^aeiouy]{5}", lowered)
        and not re.search(r"(.)\1\1", lowered)
    )


def _base_score(name: str, frequency: float, minecraft_weight: float = 0.0) -> float:
    length = len(name)
    length_bonus = {
        4: 0.5,
        5: 1.3,
        6: 2.2,
        7: 2.8,
        8: 2.8,
        9: 2.3,
        10: 1.7,
        11: 1.0,
        12: 0.5,
    }.get(length, 0.0)
    # Very common words tend to be grammatical filler; very rare words are obscure.
    # Favor the familiar-but-distinctive middle of the frequency curve.
    frequency_bonus = max(0.0, 7.0 - (abs(frequency - 4.65) * 2.4))
    power_bonus = 5.0 if name.lower() in POWER_WORDS else 0.0
    letter_bonus = min(sum(char in "kvxyz" for char in name.lower()) * 0.35, 1.05)
    return round(
        frequency_bonus + length_bonus + power_bonus + letter_bonus + minecraft_weight,
        3,
    )


def _common_words(limit: int = 6_000) -> list[tuple[str, float]]:
    words: list[tuple[str, float]] = []
    for raw_word in top_n_list("en", limit):
        word = raw_word.lower()
        if (
            word in STOP_WORDS
            or not WORD_RE.fullmatch(word)
            or not MIN_LENGTH <= len(word) <= MAX_LENGTH
            or not _is_pronounceable(word)
        ):
            continue

        frequency = zipf_frequency(word, "en")
        # This deliberately excludes dictionary curiosities and rare jargon.
        if (
            frequency >= 3.95
            and word not in NEGATIVE_WORDS
            and not profanity.contains_profanity(word)
        ):
            words.append((word, frequency))
    return words


def _add_candidate(
    output: dict[str, Candidate],
    name: str,
    score: float,
    style: str,
    source: str,
) -> None:
    if not is_valid_name(name):
        return

    key = name.casefold()
    candidate = Candidate(name=name, score=round(score, 3), style=style, source=source)
    existing = output.get(key)
    if existing is None or candidate.score > existing.score:
        output[key] = candidate


def build_candidate_pool() -> list[Candidate]:
    """Build a ranked, deduplicated pool from common words and transformations."""
    candidates: dict[str, Candidate] = {}
    common_words = _common_words()
    standalone_keys = {
        name.casefold()
        for name in (
            *CORE_STANDALONE_WORDS,
            *REFERENCE_WORDS,
            *ARCHIVE_INSPIRATION,
            *RECENT_ARCHIVE_INSPIRATION,
        )
    }

    for anchor in ANCHORS:
        frequency = max(zipf_frequency(anchor.lower(), "en"), 4.0)
        _add_candidate(
            candidates,
            anchor,
            _base_score(anchor, frequency, minecraft_weight=10.0),
            "Minecraft anchor",
            "curated style anchor",
        )

    curated_groups = (
        (
            CORE_STANDALONE_WORDS,
            10.5,
            "Word",
            "owner-preferred word",
        ),
        (REQUESTED_NAMES, 11.0, "Requested example", "owner-provided example"),
        (REFERENCE_WORDS, 9.0, "Strong word", "reference screenshot"),
        (
            ARCHIVE_INSPIRATION,
            6.5,
            "Archive inspiration",
            "filtered owner archive",
        ),
        (
            RECENT_ARCHIVE_INSPIRATION,
            7.25,
            "Archive pick",
            "filtered newer owner archive",
        ),
        (
            NOTABLE_REFERENCES,
            8.0,
            "Minehut reference",
            "official notable-server directory",
        ),
        (
            CURATED_COMPOUNDS,
            7.5,
            "Server brand",
            "curated compound bank",
        ),
    )
    for names, weight, style, source in curated_groups:
        for name in names:
            frequency = max(zipf_frequency(name.lower(), "en"), 4.0)
            _add_candidate(
                candidates,
                name,
                _base_score(name, frequency, minecraft_weight=weight),
                style,
                source,
            )

    compound_families = (
        (GEN_STEMS, ("Gens",), ("", "Gens"), "Generator brand"),
        (MINE_STEMS, ("Mine", "Mines"), ("",), "Mining brand"),
        (PVP_STEMS, ("Clash", "PvP", "Kits"), ("",), "PvP brand"),
    )
    for stems, suffixes, prefixes, style in compound_families:
        for stem in stems:
            frequency = max(zipf_frequency(stem.lower(), "en"), 4.0)
            for suffix in suffixes:
                compound = f"{stem}{suffix}"
                _add_candidate(
                    candidates,
                    compound,
                    _base_score(compound, frequency, minecraft_weight=6.0),
                    style,
                    "semantic compound",
                )
            for prefix in prefixes:
                if prefix:
                    compound = f"{prefix}{stem}"
                    _add_candidate(
                        candidates,
                        compound,
                        _base_score(compound, frequency, minecraft_weight=5.8),
                        style,
                        "semantic compound",
                    )

    for stem in BRAND_STEMS:
        frequency = max(zipf_frequency(stem.lower(), "en"), 4.0)
        for suffix in BRAND_SUFFIXES:
            compound = f"{stem}{suffix}"
            _add_candidate(
                candidates,
                compound,
                _base_score(compound, frequency, minecraft_weight=5.5),
                "Server brand",
                "curated brand parts",
            )

    # Strong standalone words such as Tycoon, Realm, Empire, and Nova.
    for word, frequency in common_words:
        display = _display_word(word)
        standalone_keys.add(display.casefold())
        score = _base_score(display, frequency)
        if word in POWER_WORDS:
            score += 3.0
        _add_candidate(candidates, display, score, "Brand word", "common English")

    # Transform only the strongest common words, keeping the pool useful and fast.
    transform_words = sorted(
        common_words,
        key=lambda item: (
            item[0] not in POWER_WORDS,
            abs(item[1] - 4.65),
            abs(len(item[0]) - 6),
            item[0],
        ),
    )[:650]

    for word, frequency in transform_words:
        display = _display_word(word)

        for suffix in SUFFIXES:
            compound = f"{display}{suffix}"
            _add_candidate(
                candidates,
                compound,
                _base_score(compound, frequency, minecraft_weight=3.0),
                f"{suffix} server",
                f"{word} + {suffix}",
            )

        for prefix in PREFIXES:
            compound = f"{prefix}{display}"
            _add_candidate(
                candidates,
                compound,
                _base_score(compound, frequency, minecraft_weight=2.6),
                f"{prefix} brand",
                f"{prefix} + {word}",
            )

    ranked = sorted(
        candidates.values(),
        key=lambda candidate: (-candidate.score, len(candidate.name), candidate.name.casefold()),
    )
    standalone = [
        candidate
        for candidate in ranked
        if candidate.name.casefold() in standalone_keys
    ]
    special = [
        candidate
        for candidate in ranked
        if candidate.name.casefold() not in standalone_keys
    ]

    # Interleave a small number of special names instead of letting suffix
    # scoring crowd ordinary English words out of the first several thousand.
    balanced: list[Candidate] = []
    standalone_index = 0
    special_index = 0
    while len(balanced) < POOL_LIMIT and (
        standalone_index < len(standalone) or special_index < len(special)
    ):
        for _ in range(STANDALONE_NAMES_PER_SPECIAL):
            if standalone_index >= len(standalone) or len(balanced) >= POOL_LIMIT:
                break
            balanced.append(standalone[standalone_index])
            standalone_index += 1

        if special_index < len(special) and len(balanced) < POOL_LIMIT:
            balanced.append(special[special_index])
            special_index += 1

    return balanced


def select_candidate(run_number: int, pool: Iterable[Candidate] | None = None) -> Candidate:
    """Select exactly one candidate, walking the ranked pool without random repeats."""
    candidates = list(pool) if pool is not None else build_candidate_pool()
    if not candidates:
        raise RuntimeError("The candidate pool is empty.")

    index = (max(run_number, 1) - 1) % len(candidates)
    return candidates[index]
