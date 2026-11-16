# ESPN labels seasons by the year they end: 2027 = the 2026-27 season
CURRENT_SEASON = 2027
SEASONS = list(range(CURRENT_SEASON - 5, CURRENT_SEASON + 1))  # current + prior 5

RECENT_GAMES = 10       # "last N games" window shown next to season rates
MIN_RATE_SHOWN = 0.50   # cards hide rows below this L10 rate (season rate for past seasons)
MIN_MPG = 24            # Players tab only lists players averaging more than this


def season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[-2:]}"
