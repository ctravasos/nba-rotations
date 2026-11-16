# ESPN labels seasons by the year they end: 2027 = the 2026-27 season
CURRENT_SEASON = 2027
SEASONS = list(range(CURRENT_SEASON - 5, CURRENT_SEASON + 1))  # current + prior 5

SLOTS = [(q, s) for q in (1, 2, 3, 4) for s in (1, 2)]

RECENT_GAMES = 10       # "last N games" window shown next to season rates
MIN_RATE_SHOWN = 0.15   # hide players who check in/out at a slot less often than this


def season_label(season: int) -> str:
    return f"{season - 1}-{str(season)[-2:]}"


def slot_label(period: int, slot: int) -> str:
    return f"Q{period} TO{slot}"
