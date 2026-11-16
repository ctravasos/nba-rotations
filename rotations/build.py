"""Render the static site into site/ (deploy that folder to Netlify / Vercel / Cloudflare Pages)."""
import os
import shutil
from datetime import datetime, timedelta, timezone

import requests
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import stats
from .config import CURRENT_SEASON, MIN_MPG, MIN_RATE_SHOWN, RECENT_GAMES, season_label
from .data import ROOT

SITE = ROOT / "site"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={date}"
EASTERN = timezone(timedelta(hours=-5))  # NBA dates are Eastern; DST shift doesn't matter for "today"

env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(),
                  trim_blocks=True, lstrip_blocks=True)
env.filters["pct"] = lambda x: f"{x * 100:.0f}%" if x else ""
env.globals.update(
    # URL prefix when hosted in a subfolder, e.g. "/nba-rotations" on GitHub Pages
    base=os.environ.get("SITE_BASE", "").rstrip("/"),
    COLUMNS=stats.COLUMNS, SLOTS=stats.SLOTS, column_label=stats.column_label,
    CURRENT_SEASON=CURRENT_SEASON, RECENT_GAMES=RECENT_GAMES, MIN_RATE_SHOWN=MIN_RATE_SHOWN,
    MIN_MPG=MIN_MPG,
    season_label=season_label, short_name=stats.short_name,
    is_slot=lambda period, phase: (period, phase) in stats.SLOT_KEYS,
)


def heat(rate: float, kind: str) -> str:
    """Inline background for a rate cell: green for check-ins, red for check-outs."""
    if not rate:
        return ""
    rgb = "34,168,74" if kind == "in" else "224,48,48"
    return f"background: rgba({rgb},{min(rate, 1) * 0.85:.2f})"


env.globals["heat"] = heat


def write(path: str, template: str, **ctx) -> None:
    out = SITE / path / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(env.get_template(template).render(**ctx), encoding="utf-8")


def season_path(season: int) -> str:
    return "" if season == CURRENT_SEASON else f"{season_label(season)}/"


env.globals["season_path"] = season_path


def todays_games() -> list[dict]:
    today = datetime.now(EASTERN).strftime("%Y%m%d")
    try:
        events = requests.get(SCOREBOARD.format(date=today), timeout=20).json().get("events", [])
    except requests.RequestException:
        return []
    games = []
    for e in events:
        comp = e["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        games.append({
            "id": e["id"],
            "status": e["status"]["type"]["shortDetail"],
            "started": e["status"]["type"]["state"] != "pre",
            **{side: {"abbr": stats.ABBR.get(t["team"]["abbreviation"], t["team"]["abbreviation"]),
                      "name": t["team"]["displayName"], "logo": t["team"].get("logo"),
                      "score": t.get("score"), "record": (t.get("records") or [{}])[0].get("summary")}
               for side, t in teams.items()},
        })
    return games


def build() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    shutil.copytree(ROOT / "static", SITE / "static")
    seasons = stats.available_seasons()
    updated = datetime.now(EASTERN).strftime("%b %d, %I:%M %p ET")
    common = {"seasons": seasons, "updated": updated}

    _, _, teams = stats.load(CURRENT_SEASON)
    team_list = sorted(teams.to_dict("records"), key=lambda t: t["team_display_name"])

    players_now = stats.all_players(CURRENT_SEASON)
    games = [g for g in todays_games() if {g["away"]["abbr"], g["home"]["abbr"]} <= set(teams.abbr)]
    write("", "index.html", games=games, active="home", **common)
    for g in games:
        write(f"games/{g['id']}", "game.html", g=g, active="home",
              away=stats.team_season(CURRENT_SEASON, g["away"]["abbr"]),
              home=stats.team_season(CURRENT_SEASON, g["home"]["abbr"]), **common)
    write("teams", "teams.html", teams=team_list, active="teams", **common)
    write("about", "about.html", active="about", **common)

    player_ids = set()
    for season in seasons:
        print(f"  {season_label(season)}")
        _, _, season_teams = stats.load(season)
        for abbr in sorted(season_teams.abbr):
            write(f"teams/{abbr}/{season_path(season)}", "team.html", season=season,
                  t=stats.team_season(season, abbr), active="teams", **common)
        roster = players_now if season == CURRENT_SEASON else stats.all_players(season)
        player_ids.update(p["id"] for p in roster)  # every player keeps a page (team pages link to them)
        write(f"players/{season_path(season)}", "players.html", season=season,
              players=[p for p in roster if p["mpg"] > MIN_MPG], active="players", **common)

    for pid in sorted(player_ids):
        if (h := stats.player_history(pid)) is not None:
            write(f"players/{pid}", "player.html", p=h, active="players", **common)
    print(f"  built {len(player_ids)} player pages -> {SITE}")
