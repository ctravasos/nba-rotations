# NBA Sub Patterns

Shows who checks in and out at each quarter's 1st and 2nd timeout (TO1 / TO2), for every team and
player, for the current season and the prior five. Styled after nba.fast-stats.com.

## Setup

```bash
pip install -r requirements.txt
python run.py update --all   # first time: downloads ~6 seasons of ESPN play-by-play (~150 MB)
python run.py build          # renders the static site into site/
python run.py serve          # preview at http://localhost:8000
```

Daily during the season: `python run.py update && python run.py build`, then redeploy `site/`.

## How it works

- **Data**: ESPN play-by-play + box scores from the sportsdataverse daily releases (updated each
  morning). The official NBA stats endpoints are blocked on the corporate network.
- **TO1 / TO2**: the first and second timeout stoppage of each quarter. Those are the mandatory TV
  timeouts: officials force one at the first dead ball under 6:59 and 2:59 if no team has called one.
- **Phases**: each quarter is split into Start / Pre TO1 / TO1 / TO1–TO2 / TO2 / Post TO2, and each
  sub lands in exactly one phase. Only ~25% of mid-quarter subs happen at TO1/TO2 themselves.
- **Rates**: share of the player's games (for that team and season) with a check-in or check-out in
  that phase. Subs made at the same stoppage are netted (A in for B, then C in for A = C in for B).

## Code

| File | What it does |
|---|---|
| `rotations/config.py` | seasons, recent-game window, display thresholds |
| `rotations/data.py` | download + extract subs per team/game/quarter/phase into `data/processed/` |
| `rotations/stats.py` | team, player and leaderboard aggregations |
| `rotations/build.py` | renders `templates/` into `site/` |

## Deploying

**GitHub Pages (automatic daily updates):** push this repo to GitHub, then in the repo go to
Settings → Pages → Source: **GitHub Actions**. `.github/workflows/deploy.yml` rebuilds and publishes
every morning at 14:00 UTC, on every push to `main`, and on demand from the Actions tab
("Run workflow"). Links are prefixed with `/<repo-name>` automatically (`SITE_BASE`).

`site/` is a plain static site, so any static host also works (build without `SITE_BASE`):

- **Netlify**: drag the `site/` folder onto https://app.netlify.com/drop, or `npx netlify deploy --prod --dir site`
- **Vercel**: `npx vercel deploy site --prod`
- **Cloudflare Pages**: `npx wrangler pages deploy site`
