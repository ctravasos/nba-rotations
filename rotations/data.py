"""Download ESPN play-by-play / box scores and extract substitutions at each quarter's
1st and 2nd timeout.

NBA rules make the first two timeouts of every quarter the mandatory (TV) timeouts: if
no team has called one by 6:59 (and a second by 2:59) the officials take it at the next
dead ball and charge it to a team. ESPN logs those charged timeouts like any other, so
"TO1" and "TO2" are simply the first two timeout stoppages in each quarter.

Only ~25% of mid-quarter subs happen at TO1/TO2 themselves, so each quarter is split into
phases anchored on them and every substitution lands in exactly one:
    start | pre1 (before TO1) | to1 | mid (TO1 -> TO2) | to2 | post (after TO2)
"""
from pathlib import Path

import pandas as pd
import requests

from .config import CURRENT_SEASON, SEASONS

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"

RELEASES = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download"
SOURCES = {
    "play_by_play": f"{RELEASES}/espn_nba_pbp/play_by_play_{{season}}.parquet",
    "player_box": f"{RELEASES}/espn_nba_player_boxscores/player_box_{{season}}.parquet",
}

REGULAR_SEASON = 2
PLAYOFFS = 3


def download(season: int, force: bool = False) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for kind, url in SOURCES.items():
        dest = RAW / f"{kind}_{season}.parquet"
        if dest.exists() and not force:
            continue
        print(f"  downloading {dest.name} ...")
        resp = requests.get(url.format(season=season), timeout=300)
        resp.raise_for_status()
        dest.write_bytes(resp.content)


def net_changes(subs: pd.DataFrame) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """Resolve a dead ball's substitutions (in order) into net ins, net outs and pairs.

    Chains collapse: "A in for B" then "C in for A" -> C in, B out, pair (C, B).
    """
    replaced: dict[str, str] = {}  # player on court now -> who they replaced at this stoppage
    for p_in, p_out in zip(subs.athlete_id_1, subs.athlete_id_2):
        orig = replaced.pop(p_out, p_out)
        if orig != p_in:  # re-entering for whoever just replaced them is no net change
            replaced[p_in] = orig
    # A player can be both a key and a value (went out, came back in for someone else)
    ins = set(replaced) - set(replaced.values())
    outs = set(replaced.values()) - set(replaced)
    pairs = []
    for p in ins:
        orig = replaced[p]
        while orig in replaced:
            orig = replaced[orig]
        pairs.append((p, orig))
    return sorted(ins), sorted(outs), sorted(pairs)


QUARTER_START = 720.0


def phase_of(secs: float, to1: float, to2: float) -> str:
    """Place a stoppage (seconds left in quarter) relative to that quarter's TO1/TO2.
    to1/to2 are NaN when the quarter had fewer timeouts (comparisons are then False)."""
    if secs == QUARTER_START:
        return "start"
    if secs == to1:
        return "to1"
    if secs == to2:
        return "to2"
    if secs < to2:
        return "post"
    if secs < to1:
        return "mid"
    return "pre1"


def extract_season(season: int) -> None:
    """Write slots_{season}.parquet (one row per team/game/quarter/phase with changes, plus
    every TO1/TO2 even when nobody subbed) and players_{season}.parquet (who played)."""
    pbp = pd.read_parquet(RAW / f"play_by_play_{season}.parquet")
    box = pd.read_parquet(RAW / f"player_box_{season}.parquet")

    pbp = pbp[pbp.season_type.isin([REGULAR_SEASON, PLAYOFFS]) & pbp.period_number.between(1, 4)]
    pbp = pbp.sort_values(["game_id", "period_number", "sequence_number"])
    pbp["secs"] = pbp.start_quarter_seconds_remaining

    games = (
        pbp.drop_duplicates("game_id")
        .set_index("game_id")[["game_date", "season_type", "home_team_id", "home_team_abbrev",
                               "away_team_id", "away_team_abbrev"]]
    )
    timeouts = pbp[pbp.type_text.str.contains("timeout", case=False, na=False)]
    # One stoppage = one (game, quarter, clock); both teams calling at the same dead ball counts once
    stoppages = timeouts.drop_duplicates(["game_id", "period_number", "secs"]).copy()
    stoppages["slot"] = stoppages.groupby(["game_id", "period_number"]).cumcount() + 1
    stoppages = stoppages[stoppages.slot <= 2]

    to_secs = stoppages.pivot_table(index=["game_id", "period_number"], columns="slot",
                                    values="secs", aggfunc="first")
    to_info = stoppages.set_index(["game_id", "period_number", "secs"])

    subs = pbp[pbp.type_text == "Substitution"].dropna(subset=["athlete_id_1", "athlete_id_2", "team_id"]).copy()
    subs["athlete_id_1"] = subs.athlete_id_1.astype("Int64").astype(str)
    subs["athlete_id_2"] = subs.athlete_id_2.astype("Int64").astype(str)
    subs["team_id"] = subs.team_id.astype("Int64")

    # Net changes per stoppage, then pooled into the phase that stoppage falls in
    phases: dict[tuple, dict] = {}
    for (gid, period, secs, team_id), grp in subs.groupby(["game_id", "period_number", "secs", "team_id"]):
        to1 = to2 = float("nan")
        if (gid, period) in to_secs.index:
            to1, to2 = to_secs.loc[(gid, period)].reindex([1, 2])
        ins, outs, pairs = net_changes(grp)
        if not ins:
            continue
        rec = phases.setdefault((gid, period, phase_of(secs, to1, to2), int(team_id)),
                                {"ins": [], "outs": [], "pairs": []})
        rec["ins"] += ins
        rec["outs"] += outs
        rec["pairs"] += [list(p) for p in pairs]
    for st in stoppages.itertuples():  # keep TO1/TO2 rows even when nobody subbed
        for side in ("home", "away"):
            team_id = int(games.at[st.game_id, f"{side}_team_id"])
            phases.setdefault((st.game_id, st.period_number, f"to{st.slot}", team_id),
                              {"ins": [], "outs": [], "pairs": []})

    rows = []
    for (gid, period, phase, team_id), rec in phases.items():
        g = games.loc[gid]
        side = "home" if team_id == int(g.home_team_id) else "away"
        opp = "away" if side == "home" else "home"
        clock, called_by = None, None
        if phase in ("to1", "to2"):
            st = to_info.loc[(gid, period, to_secs.at[(gid, period), int(phase[-1])])]
            clock = st.clock_display_value
            caller = int(st.team_id) if pd.notna(st.team_id) else None
            called_by = ("own" if caller == team_id else "opp") if caller else "official"
        rows.append({
            "season": season,
            "season_type": int(g.season_type),
            "game_id": int(gid),
            "game_date": str(g.game_date)[:10],
            "team_id": team_id,
            "team": g[f"{side}_team_abbrev"],
            "opp": g[f"{opp}_team_abbrev"],
            "home": side == "home",
            "period": int(period),
            "phase": phase,
            "clock": clock,
            "called_by": called_by,
            # A player can enter twice in a window phase (in, out, in again)
            "ins": sorted(set(rec["ins"])),
            "outs": sorted(set(rec["outs"])),
            "pairs": rec["pairs"],
        })
    PROCESSED.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(PROCESSED / f"slots_{season}.parquet", index=False)

    box = box[box.game_id.isin(games.index)]
    players = pd.DataFrame({
        "season": season,
        "game_id": box.game_id.astype(int),
        "player_id": box.athlete_id.astype("Int64").astype(str),
        "name": box.athlete_display_name,
        "pos": box.athlete_position_abbreviation,
        "headshot": box.athlete_headshot_href,
        "team_id": box.team_id.astype(int),
        "team": box.team_abbreviation,
        "starter": box.starter.fillna(False).astype(bool),
        "minutes": pd.to_numeric(box.minutes, errors="coerce").fillna(0),
        "points": pd.to_numeric(box.points, errors="coerce").fillna(0),
        "fgm": pd.to_numeric(box.field_goals_made, errors="coerce").fillna(0),
        "fg3m": pd.to_numeric(box.three_point_field_goals_made, errors="coerce").fillna(0),
    })
    players = players[players.minutes > 0]
    players.to_parquet(PROCESSED / f"players_{season}.parquet", index=False)

    teams = box.drop_duplicates("team_id")[["team_id", "team_abbreviation", "team_display_name",
                                            "team_color", "team_alternate_color", "team_logo"]]
    teams.to_parquet(PROCESSED / f"teams_{season}.parquet", index=False)
    print(f"  {season}: {games.shape[0]} games, {len(stoppages)} timeout slots")


def update(all_seasons: bool = False, force: bool = False) -> None:
    seasons = SEASONS if all_seasons else [CURRENT_SEASON]
    for season in seasons:
        # Current season changes daily; past seasons only need downloading once
        download(season, force=force or season == CURRENT_SEASON)
        extract_season(season)
