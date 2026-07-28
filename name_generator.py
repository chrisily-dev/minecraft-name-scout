"""Generate brandable Minecraft server-name candidates."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from better_profanity import profanity
from wordfreq import top_n_list, zipf_frequency


MIN_LENGTH = 4
MAX_LENGTH = 12
POOL_LIMIT = 2_000

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

    for anchor in ANCHORS:
        frequency = max(zipf_frequency(anchor.lower(), "en"), 4.0)
        _add_candidate(
            candidates,
            anchor,
            _base_score(anchor, frequency, minecraft_weight=10.0),
            "Minecraft anchor",
            "curated style anchor",
        )

    # Strong standalone words such as Tycoon, Realm, Empire, and Nova.
    for word, frequency in common_words:
        display = _display_word(word)
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
    return ranked[:POOL_LIMIT]


def select_candidate(run_number: int, pool: Iterable[Candidate] | None = None) -> Candidate:
    """Select exactly one candidate, walking the ranked pool without random repeats."""
    candidates = list(pool) if pool is not None else build_candidate_pool()
    if not candidates:
        raise RuntimeError("The candidate pool is empty.")

    index = (max(run_number, 1) - 1) % len(candidates)
    return candidates[index]
