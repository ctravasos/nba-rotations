"""Turn extracted phase events into per-team / per-player rates for the site."""
from collections import Counter
from functools import lru_cache

import pandas as pd

from .config import RECENT_GAMES, SEASONS
from .data import PROCESSED, REGULAR_SEASON

PHASES = ["start", "pre1", "to1", "mid", "to2", "post"]
PHASE_LABELS = {"start": "Start", "pre1": "Pre TO1", "to1": "TO1", "mid": "TO1–TO2",
                "to2": "TO2", "post": "Post TO2"}
# (period, phase) columns, skipping Q1 start (that's just the starters)
COLUMNS = [(q, p) for q in (1, 2, 3, 4) for p in PHASES if not (q == 1 and p == "start")]
TO_COLUMNS = [(q, p) for q in (1, 2, 3, 4) for p in ("to1", "to2")]

# ESPN -> standard NBA abbreviations
ABBR = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}


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
    return slots, players, teams


def available_seasons() -> list[int]:
    return [s for s in SEASONS if (PROCESSED / f"slots_{s}.parquet").exists()]


def _rates(events: pd.DataFrame, gp: pd.Series, key: str) -> dict:
    """{player: {(period, phase): count}} for players listed in events[key]."""
    out: dict[str, Counter] = {}
    for period, phase, ids in zip(events.period, events.phase, events[key]):
        for pid in ids:
            out.setdefault(pid, Counter())[(period, phase)] += 1
    return {pid: {col: c[col] / gp[pid] for col in c} for pid, c in out.items() if pid in gp.index}


def team_season(season: int, abbr: str) -> dict:
    slots, players, teams = load(season)
    ev = slots[slots.team == abbr]
    roster = players[players.team == abbr]
    game_ids = sorted(ev.game_id.unique(), key=lambda g: ev.loc[ev.game_id == g, "game_date"].iloc[0])
    recent_ids = set(game_ids[-RECENT_GAMES:])

    info = roster.groupby("player_id").agg(
        name=("name", "first"), pos=("pos", "first"), headshot=("headshot", "first"),
        gp=("game_id", "nunique"), gs=("starter", "sum"), mpg=("minutes", "mean"))
    recent = roster[roster.game_id.isin(recent_ids)]
    info["gp_recent"] = recent.groupby("player_id").game_id.nunique().reindex(info.index).fillna(0)
    info = info.sort_values(["gs", "mpg"], ascending=False)

    rates = {
        "season": {"in": _rates(ev, info.gp, "ins"), "out": _rates(ev, info.gp, "outs")},
        "recent": {"in": _rates(ev[ev.game_id.isin(recent_ids)], info.gp_recent[info.gp_recent > 0], "ins"),
                   "out": _rates(ev[ev.game_id.isin(recent_ids)], info.gp_recent[info.gp_recent > 0], "outs")},
    }
    names = info.name.to_dict()

    # Timeout cards: who comes in / goes out at each TO1/TO2, and who they usually swap with
    cards = []
    for period, phase in TO_COLUMNS:
        tos = ev[(ev.period == period) & (ev.phase == phase)]
        partner: dict[str, Counter] = {}
        for pairs in tos.pairs:
            for p_in, p_out in pairs:
                partner.setdefault(p_in, Counter())[p_out] += 1
                partner.setdefault(p_out, Counter())[p_in] += 1

        def top(key):
            recent_rates = rates["recent"]["in" if key == "ins" else "out"]
            counts = Counter(p for ids in tos[key] for p in ids)
            rows = []
            for pid, n in counts.most_common():
                if pid not in info.index:
                    continue
                swap = partner.get(pid, Counter()).most_common(1)
                rows.append({"id": pid, "name": names[pid], "n": n, "gp": int(info.gp[pid]),
                             "rate": n / info.gp[pid],
                             "recent": recent_rates.get(pid, {}).get((period, phase), 0),
                             "swap": short_name(names[swap[0][0]]) if swap and swap[0][0] in names else None,
                             "swap_n": swap[0][1] if swap else 0})
            return sorted(rows, key=lambda r: -r["rate"])

        clocks = tos.clock.dropna().map(_clock_secs)
        cards.append({
            "period": period, "phase": phase, "label": f"Q{period} {PHASE_LABELS[phase]}",
            "n": len(tos), "no_sub": int((tos.ins.str.len() == 0).sum()),
            "own": int((tos.called_by == "own").sum()),
            "median_clock": _fmt_clock(clocks.median()) if len(clocks) else "–",
            "ins": top("ins"), "outs": top("outs"),
        })

    # Game log: TO1/TO2 changes for every game, newest first
    log = []
    by_game = ev.set_index(["game_id", "period", "phase"])
    for gid in reversed(game_ids):
        g = ev[ev.game_id == gid].iloc[0]
        cells = []
        for period, phase in TO_COLUMNS:
            if (gid, period, phase) in by_game.index:
                r = by_game.loc[(gid, period, phase)]
                cells.append({"clock": r.clock,
                              "ins": [short_name(names.get(p, "?")) for p in r.ins],
                              "outs": [short_name(names.get(p, "?")) for p in r.outs]})
            else:
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
    """One row per player-team with in-rates at each TO slot (for the Players table)."""
    slots, players, _ = load(season)
    rows = []
    for abbr, roster in players.groupby("team"):
        ev = slots[slots.team == abbr]
        gp = roster.groupby("player_id").game_id.nunique()
        ins, outs = _rates(ev, gp, "ins"), _rates(ev, gp, "outs")
        info = roster.groupby("player_id").agg(name=("name", "first"), gs=("starter", "sum"),
                                               mpg=("minutes", "mean"))
        for pid, r in info.iterrows():
            rows.append({"id": pid, "name": r["name"], "team": abbr, "gp": int(gp[pid]),
                         "gs": int(r.gs), "mpg": r.mpg,
                         "ins": [ins.get(pid, {}).get(c, 0) for c in TO_COLUMNS],
                         "outs": [outs.get(pid, {}).get(c, 0) for c in TO_COLUMNS]})
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
