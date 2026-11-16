"""Turn extracted phase events into per-team / per-player rates for the site."""
from collections import Counter
from functools import lru_cache

import pandas as pd

from .config import RECENT_GAMES, SEASONS
from .data import PROCESSED, REGULAR_SEASON

PHASES = ["start", "pre1", "to1", "mid", "to2", "post"]
PHASE_LABELS = {"start": "Start", "pre1": "Before Timeout 1", "to1": "Timeout 1",
                "mid": "Between Timeouts", "to2": "Timeout 2", "post": "After Timeout 2",
                "after1": "After Timeout 1"}
Q4_AFTER_TO1 = ("mid", "to2", "post")  # 4Q TO2 isn't tracked: everything after 4Q TO1 is one phase

# Rotation map columns. 1Q start is just the starters; 4Q collapses after TO1.
COLUMNS = ([(1, p) for p in PHASES[1:]]
           + [(q, p) for q in (2, 3) for p in PHASES]
           + [(4, "start"), (4, "pre1"), (4, "to1"), (4, "after1")])


def column_label(period: int, phase: str) -> str:
    if phase == "start":
        return "Halftime" if period == 3 else "Break"
    return PHASE_LABELS[phase]


def ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def quarter_label(period: int, phase: str) -> str:
    """Top line of a slot: "1st Quarter", or "1st/2nd Quarter" for a break."""
    if phase == "start":
        return f"{ordinal(period - 1)}/{ordinal(period)} Quarter"
    return f"{ordinal(period)} Quarter"


def slot_label(period: int, phase: str) -> str:
    if phase == "start":
        return f"{quarter_label(period, phase)} Break"
    return f"{quarter_label(period, phase)} – {PHASE_LABELS[phase]}"


# The sub windows tracked on cards, the Players table and game logs, in game order.
# The 1Q/2Q and 3Q/4Q breaks are logged as the start of 2Q / 4Q.
SLOTS = [{"period": q, "phase": p, "label": slot_label(q, p), "is_break": p == "start",
          # two-line form for narrow table headers
          "top": quarter_label(q, p), "bottom": "Break" if p == "start" else PHASE_LABELS[p]}
         for q, p in [(1, "to1"), (1, "to2"), (2, "start"), (2, "to1"), (2, "to2"),
                      (3, "to1"), (3, "to2"), (4, "start"), (4, "to1")]]
SLOT_KEYS = [(s["period"], s["phase"]) for s in SLOTS]

# ESPN -> standard NBA abbreviations
ABBR = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}


def two_three(fgm: float, fg3m: float) -> str | None:
    """Share of made field goals that are 2s vs 3s, e.g. "65/35"."""
    if not fgm:
        return None
    threes = round(100 * fg3m / fgm)
    return f"{100 - threes}/{threes}"


def short_name(name: str) -> str:
    first, _, rest = name.partition(" ")
    return f"{first[0]}. {rest}" if rest else name


@lru_cache(maxsize=None)
def load(season: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    slots = pd.read_parquet(PROCESSED / f"slots_{season}.parquet")
    players = pd.read_parquet(PROCESSED / f"players_{season}.parquet")
    teams = pd.read_parquet(PROCESSED / f"teams_{season}.parquet")
    slots = slots[slots.season_type == REGULAR_SEASON]
    # All-Star / Rising Stars games are logged as regular season under one-off team codes
    team_games = slots.groupby("team").game_id.nunique()
    exhibition = team_games[team_games < 0.2 * team_games.max()].index
    slots = slots[~slots.team.isin(exhibition) & ~slots.opp.isin(exhibition)].copy()
    players = players[players.game_id.isin(slots.game_id)].copy()
    for df, cols in ((slots, ["team", "opp"]), (players, ["team"])):
        for c in cols:
            df[c] = df[c].replace(ABBR)
    teams["abbr"] = teams.team_abbreviation.replace(ABBR)
    teams = teams[teams.abbr.isin(slots.team)]
    slots.loc[(slots.period == 4) & slots.phase.isin(Q4_AFTER_TO1), "phase"] = "after1"
    return slots, players, teams


def available_seasons() -> list[int]:
    return [s for s in SEASONS if (PROCESSED / f"slots_{s}.parquet").exists()]


def _rates(events: pd.DataFrame, gp: pd.Series, key: str) -> dict:
    """{player: {(period, phase): share of games}} for players listed in events[key].
    Counts games, not events: a merged phase (4Q After TO1) can hold several rows per game."""
    out: dict[str, dict[tuple, set]] = {}
    for gid, period, phase, ids in zip(events.game_id, events.period, events.phase, events[key]):
        for pid in ids:
            out.setdefault(pid, {}).setdefault((period, phase), set()).add(gid)
    return {pid: {col: len(g) / gp[pid] for col, g in cols.items()}
            for pid, cols in out.items() if pid in gp.index}


@lru_cache(maxsize=None)  # team pages and game pages share these
def team_season(season: int, abbr: str) -> dict:
    slots, players, teams = load(season)
    ev = slots[slots.team == abbr]
    roster = players[players.team == abbr]
    game_ids = sorted(ev.game_id.unique(), key=lambda g: ev.loc[ev.game_id == g, "game_date"].iloc[0])
    recent_ids = set(game_ids[-RECENT_GAMES:])

    info = roster.groupby("player_id").agg(
        name=("name", "first"), pos=("pos", "first"), headshot=("headshot", "first"),
        gp=("game_id", "nunique"), gs=("starter", "sum"), mpg=("minutes", "mean"),
        ppg=("points", "mean"), fgm=("fgm", "sum"), fg3m=("fg3m", "sum"))
    info["two_three"] = [two_three(a, b) for a, b in zip(info.fgm, info.fg3m)]
    recent = roster[roster.game_id.isin(recent_ids)]
    info["gp_recent"] = recent.groupby("player_id").game_id.nunique().reindex(info.index).fillna(0)
    info = info.sort_values(["gs", "mpg"], ascending=False)

    rates = {
        "season": {"in": _rates(ev, info.gp, "ins"), "out": _rates(ev, info.gp, "outs")},
        "recent": {"in": _rates(ev[ev.game_id.isin(recent_ids)], info.gp_recent[info.gp_recent > 0], "ins"),
                   "out": _rates(ev[ev.game_id.isin(recent_ids)], info.gp_recent[info.gp_recent > 0], "outs")},
    }
    names = info.name.to_dict()
    # Short names ("S. Curry") unless two teammates share one (Stephen and Seth Curry)
    shorts = Counter(short_name(n) for n in names.values())
    display = {pid: short_name(n) if shorts[short_name(n)] == 1 else n for pid, n in names.items()}

    # Slot cards: who comes in / goes out at each timeout and quarter break
    cards = []
    for slot in SLOTS:
        period, phase = slot["period"], slot["phase"]
        rows = ev[(ev.period == period) & (ev.phase == phase)]
        # Every timeout gets a row even with no subs; breaks only have rows when someone subbed
        n = len(game_ids) if slot["is_break"] else len(rows)

        def top(key):
            season_rates = rates["season"]["in" if key == "ins" else "out"]
            recent_rates = rates["recent"]["in" if key == "ins" else "out"]
            counts = Counter(p for ids in rows[key] for p in ids)
            out = [{"id": pid, "name": display[pid], "n": c, "gp": int(info.gp[pid]),
                    "ppg": info.ppg[pid], "two_three": info.two_three[pid],
                    "rate": season_rates[pid][(period, phase)],
                    "recent": recent_rates.get(pid, {}).get((period, phase), 0)}
                   for pid, c in counts.items() if pid in info.index]
            return sorted(out, key=lambda r: -r["rate"])

        clocks = rows.clock.dropna().map(_clock_secs)
        cards.append({
            **slot, "n": n, "no_sub": n - int((rows.ins.str.len() > 0).sum()),
            "median_clock": _fmt_clock(clocks.median()) if len(clocks) else None,
            "ins": top("ins"), "outs": top("outs"),
        })

    # Game log: changes at each slot for every game, newest first
    log = []
    by_game = ev.set_index(["game_id", "period", "phase"])
    for gid in reversed(game_ids):
        g = ev[ev.game_id == gid].iloc[0]
        cells = []
        for slot in SLOTS:
            key = (gid, slot["period"], slot["phase"])
            if key in by_game.index:
                r = by_game.loc[key]
                cells.append({"clock": r.clock if pd.notna(r.clock) else None,  # breaks have no clock
                              "ins": [display.get(p, "?") for p in r.ins],
                              "outs": [display.get(p, "?") for p in r.outs]})
            elif slot["is_break"]:  # the break always happens; nobody subbed
                cells.append({"clock": None, "ins": [], "outs": []})
            else:  # quarter had fewer than two timeouts
                cells.append(None)
        log.append({"date": g.game_date, "opp": g.opp, "home": bool(g.home), "cells": cells})

    t = teams[teams.abbr == abbr].iloc[0]
    return {"abbr": abbr, "name": t.team_display_name, "logo": t.team_logo,
            "color": f"#{t.team_color}" if t.team_color else None,
            "games": len(game_ids), "players": info.reset_index().to_dict("records"),
            "rates": rates, "cards": cards, "log": log}


def _clock_secs(clock: str) -> float:
    """ESPN clocks are "7:39", or "45.2" under a minute."""
    minutes, _, secs = clock.rpartition(":")
    return int(minutes or 0) * 60 + float(secs)


def _fmt_clock(secs: float) -> str:
    secs = int(secs)
    return f"{secs // 60}:{secs % 60:02d}"


def all_players(season: int) -> list[dict]:
    """One row per player-team with in/out rates at each slot (for the Players table)."""
    slots, players, _ = load(season)
    rows = []
    for abbr, roster in players.groupby("team"):
        ev = slots[slots.team == abbr]
        gp = roster.groupby("player_id").game_id.nunique()
        ins, outs = _rates(ev, gp, "ins"), _rates(ev, gp, "outs")
        info = roster.groupby("player_id").agg(name=("name", "first"), gs=("starter", "sum"),
                                               mpg=("minutes", "mean"), ppg=("points", "mean"),
                                               fgm=("fgm", "sum"), fg3m=("fg3m", "sum"))
        for pid, r in info.iterrows():
            rows.append({"id": pid, "name": r["name"], "team": abbr, "gp": int(gp[pid]),
                         "gs": int(r.gs), "mpg": r.mpg, "ppg": r.ppg,
                         "two_three": two_three(r.fgm, r.fg3m),
                         "ins": [ins.get(pid, {}).get(c, 0) for c in SLOT_KEYS],
                         "outs": [outs.get(pid, {}).get(c, 0) for c in SLOT_KEYS]})
    return sorted(rows, key=lambda r: -r["mpg"] * r["gp"])


def player_history(player_id: str) -> dict | None:
    """Every season/team stint for one player with full phase rates."""
    stints = []
    meta = None
    for season in reversed(available_seasons()):
        slots, players, _ = load(season)
        mine = players[players.player_id == player_id]
        for abbr, games in mine.groupby("team"):
            if meta is None:  # seasons run newest first, so this is the latest info
                meta = games.iloc[0]
            ev = slots[(slots.team == abbr) & slots.game_id.isin(games.game_id)]
            gp = pd.Series({player_id: games.game_id.nunique()})
            stints.append({
                "season": season, "team": abbr, "gp": int(gp[player_id]),
                "gs": int(games.starter.sum()), "mpg": games.minutes.mean(),
                "in": _rates(ev, gp, "ins").get(player_id, {}),
                "out": _rates(ev, gp, "outs").get(player_id, {}),
            })
    if meta is None:
        return None
    return {"id": player_id, "name": meta["name"], "pos": meta.pos, "headshot": meta.headshot,
            "stints": stints}
