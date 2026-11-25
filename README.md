# NBA Sub Patterns

**Live site: [ctravasos.github.io/nba-rotations](https://ctravasos.github.io/nba-rotations/)**

A static site that shows when every NBA player checks in and out of games. It tracks each quarter's
two mandatory TV timeouts and the quarter breaks, for every team and player, across the current season
and the prior five. It rebuilds automatically every morning from fresh play-by-play data.

## Why

Rotations are among the most predictable parts of an NBA game. Coaches tend to use the same windows
(the first TV timeout of the 1st quarter, the start of the 2nd) to make the same changes night after night. Knowing
who usually comes in, who goes out, and when, helps with in-game expectations, minutes projections
and player-prop analysis. Box scores don't show this; you have to rebuild it from play-by-play.

## What's on the site

- **Today**: the day's games, each linking to a matchup page.
- **Games**: per-game pages with a Key Players rotation map and a player picker.
- **Teams**: each team's sub patterns by quarter and phase, with the players involved.
- **Players**: how often each player checks in or out in each phase, by team and season.

## How it works

- **Timeout 1 / Timeout 2**: if neither team calls a timeout by 6:59 and 2:59, officials take one at
  the next dead ball. So Timeout 1 and 2 are simply the first and second timeouts of each quarter,
  whenever they happen (timeouts in the first 2:00 are ignored). A timeout counts every sub within
  1:00 of game clock of it.
- **Phases**: only about a quarter of mid-quarter subs happen at the timeouts themselves, so each
  quarter is split into six phases and every sub lands in exactly one:
  Break → Before Timeout 1 → Timeout 1 → Between Timeouts → Timeout 2 → After Timeout 2.
- **Rates**: the share of a player's games (with that team, that season) with a check-in or
  check-out in that phase. Subs within one stoppage are netted (A in for B, then C in for A = C in
  for B). Regular season only; overtime excluded.
- **Data**: ESPN play-by-play and box scores from the
  [sportsdataverse](https://github.com/sportsdataverse) daily releases.

## Tech

Python (pandas, pyarrow, requests) for the data pipeline, Jinja2 templates rendered to a plain
static site, vanilla JS/CSS, deployed with GitHub Actions to GitHub Pages on a daily schedule.

| File | What it does |
|---|---|
| [`rotations/data.py`](rotations/data.py) | downloads play-by-play and extracts subs per team/game/quarter/phase |
| [`rotations/stats.py`](rotations/stats.py) | team, player and leaderboard aggregations |
| [`rotations/build.py`](rotations/build.py) | renders `templates/` into `site/` |
| [`rotations/config.py`](rotations/config.py) | seasons, recent-game window, display thresholds |
| [`run.py`](run.py) | CLI: `update`, `build`, `serve` |
| [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) | daily rebuild and deploy |

## Running locally

```bash
pip install -r requirements.txt
python run.py update --all   # first run: downloads ~6 seasons of play-by-play (~150 MB)
python run.py build          # renders the static site into site/
python run.py serve          # preview at http://localhost:8000
```

During the season, `python run.py update && python run.py build` refreshes the data. On GitHub,
the Actions workflow does this every day at 14:00 UTC, on every push to `main`, and on demand.
