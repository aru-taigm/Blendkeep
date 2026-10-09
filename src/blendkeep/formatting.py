"""表示用の整形（画面とコマンドラインで共通）。"""

from __future__ import annotations

from datetime import datetime


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    return f"{size} B"  # 到達しない


def format_when(timestamp: float, now: datetime | None = None) -> str:
    """「今日 13:21」「昨日 09:05」「10/05 18:40」「2025/12/31 10:00」の形式にする。"""
    now = now or datetime.now()
    when = datetime.fromtimestamp(timestamp)
    days = (now.date() - when.date()).days
    clock = when.strftime("%H:%M")
    if days == 0:
        return f"今日 {clock}"
    if days == 1:
        return f"昨日 {clock}"
    if when.year == now.year:
        return when.strftime("%m/%d ") + clock
    return when.strftime("%Y/%m/%d ") + clock
