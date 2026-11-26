"""Turn extracted phase events into per-team / per-player rates for the site."""
import json
from collections import Counter
from functools import lru_cache

import pandas as pd

from .config import CURRENT_SEASON, RECENT_GAMES, SEASONS
from .data import PLAY_IN, PLAYOFFS, PROCESSED, RAW, REGULAR_SEASON

PHASES = ["start", "pre1", "to1", "mid", "to2", "post"]
PHASE_LABELS = {"start": "Start", "pre1": "Before Timeout 1", "to1": "Timeout 1",
                "mid": "Between Timeouts", "to2": "Timeout 2", "post": "After Timeout 2"}

# Rotation map columns. 1Q start is just the starters.
COLUMNS = [(1, p) for p in PHASES[1:]] + [(q, p) for q in (2, 3, 4) for p in PHASES]


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
                      (3, "to1"), (3, "to2"), (4, "start"), (4, "to1"), (4, "to2")]]
SLOT_KEYS = [(s["period"], s["phase"]) for s in SLOTS]

# ESPN -> standard NBA abbreviations
ABBR = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS"}


def two_three(fgm: float, fg3m: float, gp: int) -> str | None:
    """Made 2s / made 3s per game, e.g. "4.7/1.9"."""
    if not gp:
        return None
    return f"{(fgm - fg3m) / gp:.1f}/{fg3m / gp:.1f}"


def short_name(name: str) -> str:
    first, _, rest = name.partition(" ")
    return f"{first[0]}. {rest}" if rest else name


@lru_cache(maxsize=None)
def _read(season: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Every game of the season (regular, play-in, playoffs), All-Star exhibitions removed."""
    slots = pd.read_parquet(PROCESSED / f"slots_{season}.parquet")
    players = pd.read_parquet(PROCESSED / f"players_{season}.parquet")
    teams = pd.read_parquet(PROCESSED / f"teams_{season}.parquet")
    # All-Star / Rising Stars games are logged as regular season under one-off team codes
    regular = slots[slots.season_type == REGULAR_SEASON]
    team_games = regular.groupby("team").game_id.nunique()
    exhibition = team_games[team_games < 0.2 * team_games.max()].index
    slots = slots[~slots.team.isin(exhibition) & ~slots.opp.isin(exhibition)].copy()
    players = players[players.game_id.isin(slots.game_id)].copy()
    for df, cols in ((slots, ["team", "opp"]), (players, ["team"])):
        for c in cols:
            df[c] = df[c].replace(ABBR)
    teams["abbr"] = teams.team_abbreviation.replace(ABBR)
    teams = teams[teams.abbr.isin(regular.team.replace(ABBR))]
    return slots, players, teams


@lru_cache(maxsize=None)
def load(season: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Regular season only (NBA Cup games included, as they count in the standings)."""
    slots, players, teams = _read(season)
    slots = slots[slots.season_type == REGULAR_SEASON]
    return slots, players[players.game_id.isin(slots.game_id)], teams


def cup_ids(season: int) -> set[int]:
    path = RAW / f"cup_{season}.json"
    return set(json.loads(path.read_text())) if path.exists() else set()


@lru_cache(maxsize=None)
def load_big(season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """High-leverage games: playoffs, play-in and NBA Cup (group play through the final)."""
    slots, players, _ = _read(season)
    big = slots.season_type.isin([PLAYOFFS, PLAY_IN]) | slots.game_id.isin(cup_ids(season))
    slots = slots[big]
    return slots, players[players.game_id.isin(slots.game_id)]


def available_seasons() -> list[int]:
    return [s for s in SEASONS if (PROCESSED / f"slots_{s}.parquet").exists()]


def _rates(events: pd.DataFrame, gp: pd.Series, key: str, only: dict[str, set] | None = None) -> dict:
    """{player: {(period, phase): share of games}} for players listed in events[key].
    Counts games, not events, so a player is counted at most once per game and phase.
    `only` limits each player to their own set of games (e.g. their last 10 played)."""
    out: dict[str, dict[tuple, set]] = {}
    for gid, period, phase, ids in zip(events.game_id, events.period, events.phase, events[key]):
        for pid in ids:
            if only is not None and gid not in only.get(pid, ()):
                continue
            out.setdefault(pid, {}).setdefault((period, phase), set()).add(gid)
    return {pid: {col: len(g) / gp[pid] for col, g in cols.items()}
            for pid, cols in out.items() if pid in gp.index}


def _team_game_order(ev: pd.DataFrame) -> list:
    return sorted(ev.game_id.unique(), key=lambda g: ev.loc[ev.game_id == g, "game_date"].iloc[0])


def _active(season: int, roster: pd.DataFrame, game_ids: list) -> set:
    """Players to list. Current season: only those who played in one of the team's last 10
    games (drops injured, traded and out-of-rotation players). Past seasons: everyone."""
    if season != CURRENT_SEASON:
        return set(roster.player_id)
    return set(roster.loc[roster.game_id.isin(game_ids[-RECENT_GAMES:]), "player_id"])


@lru_cache(maxsize=None)  # team pages and game pages share these
def team_season(season: int, abbr: str) -> dict:
    slots, players, teams = load(season)
    ev = slots[slots.team == abbr]
    roster = players[players.team == abbr]
    game_ids = _team_game_order(ev)
    active = _active(season, roster, game_ids)
    # L10 = each player's own last 10 games played, skipping games they missed
    order = {gid: i for i, gid in enumerate(game_ids)}
    last_played = (roster.assign(order=roster.game_id.map(order)).sort_values("order")
                   .groupby("player_id").game_id.agg(lambda g: set(g.iloc[-RECENT_GAMES:])).to_dict())

    info = roster.groupby("player_id").agg(
        name=("name", "first"), pos=("pos", "first"), headshot=("headshot", "first"),
        gp=("game_id", "nunique"), gs=("starter", "sum"), mpg=("minutes", "mean"),
        ppg=("points", "mean"), fgm=("fgm", "sum"), fg3m=("fg3m", "sum"))
    info["two_three"] = [two_three(a, b, g) for a, b, g in zip(info.fgm, info.fg3m, info.gp)]
    info["gp_recent"] = pd.Series({pid: len(g) for pid, g in last_played.items()}).reindex(info.index)
    info = info.sort_values(["gs", "mpg"], ascending=False)

    big_slots, big_players = load_big(season)
    big_ev = big_slots[big_slots.team == abbr]
    info["gp_big"] = (big_players[big_players.team == abbr].groupby("player_id").game_id.nunique()
                      .reindex(info.index).fillna(0).astype(int))
    rates = {
        "season": {"in": _rates(ev, info.gp, "ins"), "out": _rates(ev, info.gp, "outs")},
        "recent": {"in": _rates(ev, info.gp_recent, "ins", only=last_played),
                   "out": _rates(ev, info.gp_recent, "outs", only=last_played)},
        "big": {"in": _rates(big_ev, info.gp_big[info.gp_big > 0], "ins"),
                "out": _rates(big_ev, info.gp_big[info.gp_big > 0], "outs")},
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
            big_rates = rates["big"]["in" if key == "ins" else "out"]
            counts = Counter(p for ids in rows[key] for p in ids)
            out = [{"id": pid, "name": display[pid], "n": c, "gp": int(info.gp[pid]),
                    "ppg": info.ppg[pid], "two_three": info.two_three[pid],
                    # made 2s / 3s per game, summed by The Diff
                    "m2": (info.fgm[pid] - info.fg3m[pid]) / info.gp[pid], "m3": info.fg3m[pid] / info.gp[pid],
                    "rate": season_rates[pid][(period, phase)],
                    "recent": recent_rates.get(pid, {}).get((period, phase), 0),
                    # None = no playoff / play-in / Cup games with this team yet
                    "big": big_rates.get(pid, {}).get((period, phase), 0) if info.gp_big[pid] else None,
                    "big_gp": int(info.gp_big[pid])}
                   for pid, c in counts.items() if pid in active]
            return sorted(out, key=lambda r: -r["rate"])

        clocks = rows.clock.dropna().map(_clock_secs)
        cards.append({
            **slot, "n": n, "no_sub": n - int((rows.ins.str.len() > 0).sum()),
            "median_clock": _fmt_clock(clocks.median()) if len(clocks) else None,
            "ins": top("ins"), "outs": top("outs"),
        })
        # One list for the card: check-ins and check-outs together, tagged by direction
        cards[-1]["rows"] = ([{**r, "kind": "in"} for r in cards[-1]["ins"]]
                             + [{**r, "kind": "out"} for r in cards[-1]["outs"]])

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
            "games": len(game_ids), "big_games": int(big_ev.game_id.nunique()),
            "players": info[info.index.isin(active)].reset_index().to_dict("records"),
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
        active = _active(season, roster, _team_game_order(ev))
        roster = roster[roster.player_id.isin(active)]
        gp = roster.groupby("player_id").game_id.nunique()
        ins, outs = _rates(ev, gp, "ins"), _rates(ev, gp, "outs")
        info = roster.groupby("player_id").agg(name=("name", "first"), gs=("starter", "sum"),
                                               mpg=("minutes", "mean"), ppg=("points", "mean"),
                                               fgm=("fgm", "sum"), fg3m=("fg3m", "sum"))
        for pid, r in info.iterrows():
            rows.append({"id": pid, "name": r["name"], "team": abbr, "gp": int(gp[pid]),
                         "gs": int(r.gs), "mpg": r.mpg, "ppg": r.ppg,
                         "two_three": two_three(r.fgm, r.fg3m, gp[pid]),
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


def key_players(*team_stats: dict) -> list[dict]:
    """Both teams' listed players with their rotation-map rates, highest PPG first
    (for the game page's Key Players picker)."""
    rows = []
    for t in team_stats:
        r = t["rates"]
        for p in t["players"]:
            pid = p["player_id"]
            rows.append({**p, "team": t["abbr"],
                         "season_in": r["season"]["in"].get(pid, {}), "season_out": r["season"]["out"].get(pid, {}),
                         "recent_in": r["recent"]["in"].get(pid, {}), "recent_out": r["recent"]["out"].get(pid, {})})
    return sorted(rows, key=lambda p: -p["ppg"])


def vs_opponent_cards(t: dict, season: int, opp: str) -> list[dict]:
    """Copy of a team's slot cards with each row's rate in this season's games against `opp`
    (all game types). Used for the "vs OPP" column on game pages."""
    slots, players, _ = _read(season)
    ev = slots[(slots.team == t["abbr"]) & (slots.opp == opp)]
    games = players[(players.team == t["abbr"]) & players.game_id.isin(ev.game_id)]
    gp = games.groupby("player_id").game_id.nunique()
    rates = {"in": _rates(ev, gp, "ins"), "out": _rates(ev, gp, "outs")}
    cards = []
    for c in t["cards"]:
        key = (c["period"], c["phase"])
        rows = [{**r, "vs_gp": int(gp.get(r["id"], 0)),
                 "vs": rates[r["kind"]].get(r["id"], {}).get(key, 0) if r["id"] in gp.index else None}
                for r in c["rows"]]
        cards.append({**c, "rows": rows})
    return cards


def the_diff(rows: list[dict]) -> dict | None:
    """Subheader of a slot card: the listed players coming in vs going out, per game.
    ppg   = total PPG in - total PPG out
    split = made 2s in - out / made 3s in - out, e.g. "−5.3/−1.4"."""
    if not rows:
        return None
    # Sum the rounded values shown on the card so the rows visibly add up to the total
    net = lambda field: (sum(round(r[field], 1) for r in rows if r["kind"] == "in")
                         - sum(round(r[field], 1) for r in rows if r["kind"] == "out"))
    fmt = lambda x: "0.0" if round(x, 1) == 0 else f"{x:+.1f}".replace("-", "−")
    return {"ppg": net("ppg"), "split": f"{fmt(net('m2'))}/{fmt(net('m3'))}"}
