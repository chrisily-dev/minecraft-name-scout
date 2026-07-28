# Minecraft Name Scout

A scheduled Discord bot that checks exactly one desirable Minecraft server name
per run and reports the result with a polished Discord embed.

## What it does

- Generates names automatically from common English dictionary vocabulary.
- Adds Minecraft-style transformations such as `PvP`, `Kits`, `Gens`, `Craft`,
  `Box`, and `Sky`.
- Scores names for commonness, brandability, length, and Minecraft relevance.
- Includes styles such as `Tycoon`, `Sales`, `Installed`, `Mining`, `Farming`,
  `Dancer`, `Major`, `Mayor`, `Flat`, `Platform`, `RandomKits`, `BoxPvP`,
  `GenPvP`, and `Gens`.
- Includes the requested shapes such as `WoolGens`, `GensFood`, `LoopGens`,
  `AcidGens`, `AdonisMine`, `NestMines`, `NylonGN`, `Gmini`, `FlagClash`,
  `Beans`, and `Valknet`.
- Builds additional generator, mining, kit, PvP, and clash compounds from
  semantic stem groups instead of requiring every possible name by hand.
- Includes the strong reference words `Harbor`, `Ashen`, `Basalt`, `Cabin`,
  `Drift`, `Ember`, `Flint`, and `Grove`, plus selected historical Minehut
  references as availability candidates.
- Rejects profanity, rare dictionary curiosities, awkward consonant clusters,
  symbols, and anything outside the 4-12 character limit.
- Walks a ranked candidate pool without accidental random repeats.
- Makes one Minehut availability request per workflow run.
- Sends a green embed when a name appears available.
- Sends a red embed when a name is unavailable and queues it for one retry the
  next day.
- Gives a due retry the run's single check slot. After that retry, the name is
  removed so old unavailable names cannot permanently block new suggestions.

## Minehut API notice

[Minehut's current published rules](https://support.minehut.com/hc/en-us/articles/27075816947731-Minehut-Rules)
state that its API may not be used. Do not enable the scheduled workflow or raise
its request rate unless you have written permission to use the endpoint. Even
when the API reports a name as available, verify and claim it through the
official Minehut dashboard.

## Setup

1. Create a private GitHub repository and add these files.
2. Open **Settings -> Secrets and variables -> Actions**.
3. Create a repository secret named `DISCORD_WEBHOOK`.
4. Paste the Discord webhook URL as its value.
5. Open **Actions -> Run Minecraft Name Scout -> Run workflow** to test it.

The scheduled workflow runs every five minutes, which is GitHub Actions' shortest
supported schedule interval. Each run selects and checks exactly one name. A due
next-day retry takes that one slot; otherwise the bot selects a new ranked
candidate. GitHub may delay scheduled workflows during periods of high load.

The workflow commits `data/retry_queue.json` after a successful notification so
the queue survives between GitHub-hosted runners.

## Local use

```bash
python -m pip install -r requirements-dev.txt
python bot.py --dry-run --run-number 1
pytest
```

Do not put the webhook URL in source code, commits, logs, screenshots, or issues.
