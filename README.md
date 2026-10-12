# Minecraft Name Scout

Check a reviewed shortlist of Minehut server names, with the wishlist as the
style guide and PrisonEscape as the main use case.

The ranked pool starts with every exact name in `WATCHLIST_NAMES`, then prison
and escape words such as Escape, Outlaw, Warden, Breakout, and Refuge. Broader
names such as Beacon, Drift, Citadel, and Summit follow. Short, familiar words
rank higher within each group.

Names are added individually in `name_generator.py`. English word frequency
helps rank those names; it does not add arbitrary dictionary words. No suffixes
or compounds are generated. Explicit wishlist names such as PrisonRP and
PrisonEscape are retained.

The bot filters the saved retry queue against the current shortlist on startup.
Wishlist checks and watcher pings keep their hourly cadence. Ordinary taken
names retain their next-day retry, and pending deletions retain their three-hour
retry. If no names are due, the batch ends quietly and stops immediate workflow
chaining. The workflow is scheduled every 20 minutes to check again.

Preview a batch without Minehut lookups or Discord messages:

```sh
python -m pip install -r requirements.txt
python bot.py --dry-run --run-number 1 --checks-per-run 80
```

Run the tests:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
```
