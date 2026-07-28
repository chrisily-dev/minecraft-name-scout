# Minecraft Name Scout

A scheduled Discord bot that checks exactly one desirable Minecraft server name
per run and sends a Discord embed when the name appears available.

## What it does

- Generates names automatically from common English dictionary vocabulary.
- Adds Minecraft-style transformations such as `PvP`, `Kits`, `Gens`, `Craft`,
  `Box`, and `Sky`.
- Scores names for commonness, brandability, length, and Minecraft relevance.
- Includes styles such as `Tycoon`, `Sales`, `Installed`, `Mining`, `Farming`,
  `Dancer`, `Major`, `Mayor`, `Flat`, `Platform`, `RandomKits`, `BoxPvP`,
  `GenPvP`, and `Gens`.
- Rejects profanity, rare dictionary curiosities, awkward consonant clusters,
  symbols, and anything outside the 4–12 character limit.
- Walks a ranked candidate pool without accidental random repeats.
- Makes one Minehut availability request per workflow run.
- Sends a Discord embed only when the candidate appears available.

## Minehut API notice

Minehut's current published rules state that its API may not be used. Do not enable
the scheduled workflow unless you have permission to use the endpoint. Even when
the API reports a name as available, verify and claim it through the official
Minehut dashboard.

## Setup

1. Create a private GitHub repository and add these files.
2. Open **Settings → Secrets and variables → Actions**.
3. Create a repository secret named `DISCORD_WEBHOOK`.
4. Paste the Discord webhook URL as its value.
5. Open **Actions → Run Minecraft Name Scout → Run workflow** to test it.

The scheduled workflow runs every five minutes, which is GitHub Actions' shortest
supported schedule interval. Each run selects and checks one name. GitHub may
delay scheduled workflows during periods of high load.

## Local use

```bash
python -m pip install -r requirements-dev.txt
python bot.py --dry-run --run-number 1
pytest
```

Do not put the webhook URL in source code, commits, logs, screenshots, or issues.
