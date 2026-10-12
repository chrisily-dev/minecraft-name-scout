"""Rank a reviewed shortlist of Minecraft server names for PrisonEscape."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from better_profanity import profanity
from wordfreq import zipf_frequency


MIN_LENGTH = 4
MAX_LENGTH = 12
WORD_RE = re.compile(r"^[A-Za-z]+$")

# Preserve the active wishlist's exact spellings and explicit compounds.
# bot.NAME_WATCHERS uses these names for the existing hourly checks and pings.
# They also set the style: recognizable words such as Harbor, State,
# and Dungeon, with PrisonEscape as the main use case.
WATCHLIST_NAMES = (
    "Harbor",
    "Harbour",
    "Dungeon",
    "Dungeons",
    "Dunheon",
    "Prison",
    "PrisonRP",
    "Roleplay",
    "PrisonEscape",
    "State",
)

# Freedom, pursuit, prison, and the world outside. These are whole words,
# reviewed individually; no word + PvP/Gens/Hub or other suffix is generated.
PRISON_ESCAPE_WORDS = (
    "Escape", "Breakout", "Outlaw", "Fugitive", "Runaway", "Freedom",
    "Liberty", "Rebel", "Revolt", "Riot", "Rogue", "Renegade", "Defiant",
    "Defiance", "Unbound", "Unchained", "Unbroken", "Flee", "Flight",
    "Pursuit", "Chase", "Hunted", "Wanted", "Bounty", "Heist", "Bandit",
    "Hideout", "Refuge", "Shelter", "Sanctuary", "Haven", "Frontier",
    "Outlands", "Outpost", "Badlands", "Exile", "Rival", "Uprising",
    "Warden", "Wardens", "Inmate", "Convict", "Lockup", "Lockdown",
    "Cellblock", "Cells", "Shackles", "Chains", "Parole", "Pardon",
    "Verdict", "Justice", "Sentence", "Trial", "Guard", "Guards",
    "Patrol", "Vault", "Iron", "Steel", "Grit", "Tunnel", "Tunnels",
    "Passage", "Gateway", "Portal", "Rescue", "Breach", "Rift", "Rise",
    "Survive", "Survival", "Beyond", "Dawn", "Daybreak", "Open", "Free",
)

# Broader names in the wishlist's simple, memorable style. A word enters the
# pool only by being added here or above. Frequency is useful for ordering
# approved words, but cannot decide whether a random dictionary token belongs.
BRAND_WORDS = (
    "Anchor", "Beacon", "Coast", "Cove", "Shore", "Tide", "Tides", "Wave",
    "Waves", "Ocean", "Reef", "Coral", "Island", "Isles", "Lagoon", "Drift",
    "Voyage", "Sail", "Compass", "Atlas", "Summit", "Peak", "Ridge", "Cliff",
    "Valley", "Canyon", "Mesa", "Grove", "Forest", "Glade", "Cedar", "Maple",
    "Willow", "Birch", "Aspen", "Thorn", "Briar", "Bloom", "Flora", "Meadow",
    "Field", "Fields", "Garden", "Timber", "Boulder", "Stone", "Slate",
    "Basalt", "Flint", "Granite", "Marble", "Obsidian", "Onyx", "Quartz",
    "Crystal", "Copper", "Silver", "Golden", "Ember", "Embers", "Ashen",
    "Blaze", "Flame", "Spark", "Forge", "Frost", "Snow", "Winter", "Glacier",
    "Storm", "Thunder", "Tempest", "Cloud", "Rain", "Mist", "Breeze", "Gale",
    "Solar", "Lunar", "Orbit", "Comet", "Meteor", "Cosmos", "Nebula", "Nova",
    "Stellar", "Eclipse", "Aurora", "Twilight", "Dusk", "Night", "Midnight",
    "Shadow", "Shade", "Phantom", "Ghost", "Echo", "Mirage", "Enigma",
    "Mystic", "Arcane", "Rune", "Relic", "Myth", "Fable", "Legend", "Legacy",
    "Quest", "Saga", "Realm", "Kingdom", "Empire", "Citadel", "Castle",
    "Bastion", "Fortress", "Stronghold", "Keep", "Tower", "Crown", "Royal",
    "Reign", "Throne", "Sovereign", "Noble", "Knight", "Squire", "Paladin",
    "Valor", "Honor", "Glory", "Triumph", "Victory", "Champion", "Titan",
    "Giant", "Hero", "Apex", "Prime", "Elite", "Alpha", "Omega", "Nexus",
    "Unity", "Union", "Faction", "Alliance", "Order", "Guild", "Tribe",
    "Nation", "District", "Capital", "Metro", "Urban", "City", "Town",
    "Village", "Cabin", "Lodge", "Manor", "Estate", "Domain", "Territory",
    "Expanse", "Wander", "Roam", "Journey", "Venture", "Odyssey", "Adventure",
    "Discovery", "Destiny", "Fortune", "Prosper", "Tycoon", "Mining",
    "Bedrock", "Cobble", "Pickaxe", "Quarry", "Cavern", "Caves", "Depths",
    "Abyss", "Chasm", "Hollow", "Burrow", "Nest", "Roost", "Raven",
    "Falcon", "Hawk", "Eagle", "Wolf", "Lynx", "Viper", "Cobra", "Dragon",
    "Phoenix", "Griffin", "Hydra", "Wraith", "Zombie", "Specter", "Spirit",
    "Dream", "Dreams", "Lucid", "Serene", "Solace", "Bliss", "Grace", "Frank",
    "Loud", "Pulse", "Flux", "Vortex", "Surge", "Rush", "Stride", "Sprint",
    "Dash", "Leap", "Soar", "Ascend", "Zenith", "Elevate", "Awaken",
)


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    score: float
    style: str
    source: str


def is_valid_name(name: str) -> bool:
    """Return whether a name follows Minehut's documented name shape."""
    return MIN_LENGTH <= len(name) <= MAX_LENGTH and bool(WORD_RE.fullmatch(name))


def _base_score(name: str, frequency: float, minecraft_weight: float = 0.0) -> float:
    """Favor short, familiar words within each curated group."""
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
    }.get(len(name), 0.0)
    frequency_bonus = max(0.0, 7.0 - (abs(frequency - 4.65) * 2.4))
    return round(frequency_bonus + length_bonus + minecraft_weight, 3)


def build_candidate_pool() -> list[Candidate]:
    """Put the wishlist first, then PrisonEscape words, then broader brands."""
    candidates: dict[str, Candidate] = {}
    groups = (
        (WATCHLIST_NAMES, 100.0, "Watchlist", "requested watch name"),
        (PRISON_ESCAPE_WORDS, 50.0, "PrisonEscape word", "curated prison and escape word"),
        (BRAND_WORDS, 0.0, "Brand word", "curated wishlist-style word"),
    )
    for names, weight, style, source in groups:
        for name in names:
            if not is_valid_name(name) or profanity.contains_profanity(name):
                continue
            key = name.casefold()
            if key in candidates:
                continue
            frequency = max(zipf_frequency(name.lower(), "en"), 4.0)
            candidates[key] = Candidate(
                name, _base_score(name, frequency, weight), style, source
            )

    return sorted(
        candidates.values(),
        key=lambda candidate: (-candidate.score, len(candidate.name), candidate.name.casefold()),
    )


def select_candidate(run_number: int, pool: Iterable[Candidate] | None = None) -> Candidate:
    """Select one candidate, walking the ranked pool without random repeats."""
    candidates = list(pool) if pool is not None else build_candidate_pool()
    if not candidates:
        raise RuntimeError("The candidate pool is empty.")

    index = (max(run_number, 1) - 1) % len(candidates)
    return candidates[index]
