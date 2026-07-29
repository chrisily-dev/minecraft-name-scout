# Minecraft Name Scout

A scheduled Discord bot that checks a paced batch of desirable Minecraft server
names and reports every result with polished Discord embeds.

## What it does

- Generates names automatically from common English dictionary vocabulary.
- Keeps the pool roughly 80% standalone words and limits suffix-heavy names
  such as `xGens`, `xKits`, `xPvP`, and `xCraft` to a smaller supporting share.
- Gives recognizable 4-6 character words the strongest length priority instead
  of favoring longer names.
- Adds a second word only when that word is a game mode, giving `RandomKits`
  and `BoxPvP` while rejecting generic mashups such as `FrostHaven`,
  `PixelCove`, and `MineTycoon`.
- Never emits an `MC` tag, and does not suggest names of servers that already
  exist.
- Scores names for commonness, brandability, length, and Minecraft relevance.
- Includes styles such as `Tycoon`, `Sales`, `Installed`, `Mining`, `Farming`,
  `Dancer`, `Major`, `Mayor`, `Flat`, `Platform`, `RandomKits`, `BoxPvP`,
  `GenPvP`, and `Gens`.
- Prioritizes straightforward words such as `Open`, `Flee`, `Zombie`, and
  `Prison`, with short memorable words such as `Loud` ranked automatically.
- Includes the requested shapes such as `WoolGens`, `GensFood`, `LoopGens`,
  `AcidGens`, `AdonisMine`, `NestMines`, `NylonGN`, `Gmini`, `FlagClash`,
  `Beans`, and `Valknet`.
- Builds additional generator, mining, kit, PvP, and clash compounds from
  semantic stem groups instead of requiring every possible name by hand.
- Builds thousands of extra server-ready combinations from curated parts such
  as `FrostHaven`, `PixelCove`, `StormGens`, `IronMines`, and `EmberPvP`.
- Includes the strong reference words `Harbor`, `Ashen`, `Basalt`, `Cabin`,
  `Drift`, `Ember`, `Flint`, and `Grove`.
- Checks the `WATCHLIST_NAMES` group early and pings whoever is waiting on each
  of those names.
- Retains recognizable inspiration from the supplied 2022 archive, including
  `Backseat`, `Formwork`, `Trackball`, `Bulwarks`, `Refocuses`, and `Skydives`,
  while rejecting hundreds of obscure dictionary curiosities.
- Adds a tightly filtered set from the newer archive, including `Managed`,
  `Ongoing`, `Deployed`, `Promptly`, `Compiled`, `Fixtures`, `Specialty`,
  `Patents`, `Feasible`, `Valuation`, `Portraits`, and `Fulfilled`.
- Rejects profanity, rare dictionary curiosities, awkward consonant clusters,
  symbols, and anything outside the 4-12 character limit.
- Walks a ranked candidate pool without accidental random repeats.
- Checks 20 unique names per workflow run, with 13 seconds between Minehut
  requests so the rate never exceeds five lookups per minute.
- Sends one separate Discord embed message for every checked name.
- Sends a green embed to the `DISCORD_WEBHOOK` channel when a name appears
  available.
- Sends a red embed to the separate `DISCORD_WEBHOOK_TAKEN` channel when a name
  is unavailable, and queues it for one retry the next day.
- Checks new names first. At most one due retry can use the final slot in a
  batch, keeping known unavailable names at the bottom of the queue.
- Removes an unavailable name after its one retry so it cannot permanently
  block new suggestions.

## Minehut API notice

[Minehut's current published rules](https://support.minehut.com/hc/en-us/articles/27075816947731-Minehut-Rules)
state that its API may not be used. This repository is configured based on the
owner's report that a Minehut moderator approved availability-only checks at no
more than five requests per minute. The bot never logs into Minehut, reserves,
renames, or claims a server. Verify and claim results manually through the
official Minehut dashboard.

## Setup

1. Create a private GitHub repository and add these files.
2. Open **Settings -> Secrets and variables -> Actions**.
3. Create a repository secret named `DISCORD_WEBHOOK` and paste the webhook URL
   for the channel that should receive available names.
4. Create a second secret named `DISCORD_WEBHOOK_TAKEN` and paste the webhook
   URL for the channel that should receive taken names.
5. Open **Actions -> Run Minecraft Name Scout -> Run workflow** to test it.

If `DISCORD_WEBHOOK_TAKEN` is missing the run still succeeds, but every result
goes to the single `DISCORD_WEBHOOK` channel and the log prints a warning.

## Scheduling

GitHub treats scheduled workflows as best effort. They are routinely delayed by
15 to 60 minutes and dropped entirely at busy times, and everything scheduled on
a round minute competes in the same queue. Relying on `cron` alone gives an
irregular cadence with long silent gaps.

So each run starts the next one itself. The last step calls the
`workflow_dispatch` API, which begins another run as soon as the current one
finishes. The `cron` entry stays as a fallback, on deliberately odd minutes, to
restart the chain if it ever breaks.

To enable it, create a **fine-grained personal access token** with
**Actions: read and write** on this repository only, and save it as a repository
secret named `CHAIN_TOKEN`:

```bash
gh secret set CHAIN_TOKEN -R <owner>/<repo>
```

The built-in `GITHUB_TOKEN` cannot be used. GitHub refuses to start a workflow
from an event raised by `GITHUB_TOKEN`, to prevent runaway recursion, so opting
in requires a PAT.

Without `CHAIN_TOKEN` the workflow still runs; it just falls back to `cron` and
its irregular timing.

The chain only continues when the scan step succeeds. A failed scan stops it and
leaves `cron` to restart things later, so a repeatable failure cannot spin in a
tight loop and burn the Actions allowance.

To stop the chain: disable the workflow in the Actions tab, or run it once from
the UI with **chain** set to `false`.

**Cost.** Chained runs are continuous, so this consumes Actions minutes at
roughly 60 per hour. On a private repository that exhausts the monthly free
allowance in about a day and a half. Public repositories get unlimited minutes.

## Pings

`bot.ALWAYS_NOTIFY_ROLE` is pinged when a name comes back **available**, and only
then. Set it to `""` to turn that off. Taken names never ping the role: nearly
every check comes back taken, so pinging on those would fire constantly and get
the channel muted.

`bot.NAME_WATCHERS` maps a name to the Discord user IDs pinged when that specific
name is checked, available or not. Someone watching a name asked about that name
rather than about good news, so they hear either way.

A watched name must also appear in `name_generator.WATCHLIST_NAMES`, otherwise
the generator never produces it and the ping can never fire. A test enforces
this. `allowed_mentions` lists only these exact IDs, so a generated name can
never cause an unintended `@everyone`.

The scheduled workflow runs every five minutes, which is GitHub Actions' shortest
supported schedule interval. Each run selects 20 non-repeating names and spaces
the checks 13 seconds apart. The final slot may receive the oldest due retry,
while the first 19 slots remain new names. GitHub may delay scheduled workflows
during periods of high load.

The workflow commits `data/retry_queue.json` after a successful notification so
the queue survives between GitHub-hosted runners.

## Local use

```bash
python -m pip install -r requirements-dev.txt
python bot.py --dry-run --run-number 1
pytest
```

Do not put the webhook URL in source code, commits, logs, screenshots, or issues.
