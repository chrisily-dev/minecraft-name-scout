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

# Exact one-word examples requested by the owner.
#
# The compound entries that used to live here (WoolGens, GensFood, LoopGens,
# AcidGens, AdonisMine, NestMines, FlagClash) were removed: a name may no longer
# carry a game mode as its second word. Restoring one is a matter of adding it
# back to this tuple.
REQUESTED_NAMES = (
    "NylonGN",
    "Gmini",
    "Beans",
    "Valknet",
)

# The only compounds anywhere in the pool. These three are established category
# names rather than a pattern to extend, so nothing is generated from them.
# Adding a fourth is a deliberate edit, not a side effect of a suffix bank.
ALLOWED_COMPOUNDS = (
    "RandomKits",
    "BoxPvP",
    "GenPvP",
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

# Names someone is waiting on. They are scored high so the bot reaches them
# early, and bot.NAME_WATCHERS pings the interested person on each result.
# Several are not English dictionary words, so without this group they would
# never enter the pool and their ping could never fire.
WATCHLIST_NAMES = (
    "Harbor",
    "Harbour",
    "Sete",
    "Dungeon",
    "Dungeons",
    "Dunheon",
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

# No stem or suffix banks remain. Every mechanical combination of them
# produced the exact shapes that are unwanted: FillPvP, AmberHub, VoidGens,
# NovaMines. The pool is now single words plus a handful of names listed by
# hand, and nothing is assembled at runtime.

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

# wordfreq ranks how often a token appears in English text, not whether it is a
# dictionary word, and people write about people constantly. So given names rank
# very high: "david" outranks most real vocabulary. wordfreq also lowercases
# everything, leaving no capitalisation signal to filter on, hence this list.
#
# Deliberately excludes names that are also ordinary words, because those make
# perfectly good server names: Grace, Frank, Mark, Jack, Luke, Rose, Art, Bill,
# Dawn, Hope, Joy, May, Miles, Rusty, Sunny, Victor, Wade.
GIVEN_NAMES = {
    "aaron", "adam", "adrian", "alan", "albert", "alex", "alexander", "alice",
    "alicia", "amanda", "amber", "amy", "andrea", "andrew", "angela", "anna",
    "anne", "anthony", "antonio", "arthur", "ashley", "barbara", "benjamin",
    "beth", "betty", "brandon", "brenda", "brian", "bruce", "bryan", "carl",
    "carlos", "carol", "carolyn", "catherine", "cathy", "charles", "cheryl",
    "chris", "christian", "christina", "christine", "christopher", "cindy",
    "claire", "clara", "colin", "connie", "craig", "cynthia", "dan", "daniel",
    "danny", "darren", "dave", "david", "dean", "debbie", "deborah", "debra",
    "dennis", "derek", "diana", "diane", "donald", "donna", "doris", "dorothy",
    "douglas", "duncan", "edward", "eileen", "elaine", "eleanor", "elena",
    "elizabeth", "ellen", "emily", "emma", "eric", "erica", "erin", "ethan",
    "eugene", "evelyn", "fiona", "fran", "frances", "francis", "fred",
    "frederick", "gary", "gavin", "geoffrey", "george", "gerald", "gordon",
    "graham", "greg", "gregory", "hannah", "harold", "harriet", "harry",
    "heather", "heidi", "helen", "henry", "holly", "howard", "hugh", "ian",
    "irene", "isaac", "jack", "jacob", "james", "jamie", "jane", "janet",
    "janice", "jason", "jean", "jeff", "jeffrey", "jennifer", "jenny", "jeremy",
    "jerome", "jerry", "jessica", "jill", "jim", "joan", "joanna", "joanne",
    "joel", "john", "johnny", "jonathan", "jordan", "joseph", "joshua", "joyce",
    "juan", "judith", "judy", "julia", "julian", "julie", "justin", "karen",
    "kate", "katherine", "kathleen", "kathryn", "kathy", "keith", "kelly",
    "kenneth", "kevin", "kim", "kimberly", "kyle", "larry", "laura", "lauren",
    "laurie", "lawrence", "leonard", "leslie", "linda", "lindsay", "lisa",
    "lloyd", "logan", "lois", "loretta", "lori", "louis", "louise", "lucas",
    "lucy", "luke", "lydia", "lynn", "malcolm", "marcus", "margaret", "maria",
    "marian", "marie", "marilyn", "mario", "marion", "marjorie", "mark",
    "martha", "martin", "mary", "matthew", "maureen", "megan", "melissa",
    "michael", "michelle", "mike", "mildred", "monica", "nancy", "naomi",
    "natalie", "nathan", "neil", "nicholas", "nicole", "nigel", "nina", "noah",
    "norman", "olivia", "oscar", "owen", "pamela", "patricia", "patrick",
    "paul", "paula", "pauline", "peggy", "peter", "philip", "phillip",
    "phyllis", "rachel", "ralph", "randy", "raymond", "rebecca", "regina",
    "renee", "rhonda", "richard", "rita", "robert", "roberta", "robin",
    "rodney", "roger", "ronald", "rosemary", "roy", "russell", "ruth", "ryan",
    "sally", "samantha", "samuel", "sandra", "sara", "sarah", "scott", "sean",
    "sharon", "sheila", "shirley", "sidney", "simon", "sophia", "stanley",
    "stephanie", "stephen", "steve", "steven", "stuart", "susan", "suzanne",
    "sylvia", "tammy", "teresa", "terry", "theodore", "theresa", "thomas",
    "tiffany", "timothy", "tina", "todd", "tom", "tracy", "travis", "trevor",
    "tyler", "valerie", "vanessa", "vernon", "veronica", "vincent", "virginia",
    "walter", "wanda", "warren", "wayne", "wendy", "wesley", "william",
    "yvonne", "zachary",
    # Newer given names that are equally common in modern text.
    "aiden", "bennett", "caleb", "carter", "chloe", "connor", "elijah", "ellie",
    "evan", "felix", "gabriel", "hugo", "isla", "jonah", "leah", "liam", "maya",
    "milo", "nora", "oliver", "parker", "sophie", "tucker", "wyatt",
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
        4: 6.0,
        5: 5.5,
        6: 5.0,
        7: 2.6,
        8: 1.8,
        9: 1.2,
        10: 0.8,
        11: 0.4,
        12: 0.2,
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
            and word not in GIVEN_NAMES
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
            *WATCHLIST_NAMES,
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
        (WATCHLIST_NAMES, 14.0, "Watchlist", "requested watch name"),
        (ALLOWED_COMPOUNDS, 11.5, "Category name", "established category"),
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

    # No compounds are generated at all. Every mechanical "word + game mode"
    # family produced names like FillPvP, AmberHub, and VoidGens, which are not
    # wanted. The only three compounds in the pool are RandomKits, BoxPvP, and
    # GenPvP, and they are listed by hand in ANCHORS because those three are
    # established category names rather than a pattern to extend.

    # Strong standalone words such as Tycoon, Realm, Empire, and Nova.
    for word, frequency in common_words:
        display = _display_word(word)
        standalone_keys.add(display.casefold())
        score = _base_score(display, frequency)
        if word in POWER_WORDS:
            score += 3.0
        _add_candidate(candidates, display, score, "Brand word", "common English")

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
