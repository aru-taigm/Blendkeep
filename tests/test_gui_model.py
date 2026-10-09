from __future__ import annotations

from datetime import datetime

from blendkeep.formatting import format_size, format_when
from blendkeep.store import Snapshot


def _snap(i: int, path: str, taken_at: float) -> Snapshot:
    return Snapshot(i, path, taken_at, 100, f"{i:064d}", None)


def test_group_files_orders_by_latest_and_counts() -> None:
    from blendkeep.gui.model import group_files

    snaps = [
        _snap(1, "/p/a.blend", 100.0),
        _snap(2, "/p/b.blend", 300.0),
        _snap(3, "/p/a.blend", 200.0),
    ]
    entries = group_files(snaps)
    assert [e.name for e in entries] == ["b.blend", "a.blend"]
    assert entries[1].count == 2
    assert entries[1].latest == 200.0
    assert entries[1].folder == "/p"


def test_group_files_empty() -> None:
    from blendkeep.gui.model import group_files

    assert group_files([]) == []


def test_default_restore_path() -> None:
    from blendkeep.gui.model import default_restore_path

    path = default_restore_path(_snap(7, "/p/scene.blend", 1.0)).replace("\\", "/")
    assert path == "/p/scene.restored-7.blend"


def test_format_size() -> None:
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.0 KB"
    assert format_size(1536) == "1.5 KB"
    assert format_size(5 * 1024 * 1024) == "5.0 MB"
    assert format_size(3 * 1024**3) == "3.0 GB"


def test_format_when() -> None:
    now = datetime(2026, 10, 8, 12, 0, 0)

    def ts(*args: int) -> float:
        return datetime(*args).timestamp()

    assert format_when(ts(2026, 10, 8, 9, 5), now) == "今日 09:05"
    assert format_when(ts(2026, 10, 7, 23, 59), now) == "昨日 23:59"
    assert format_when(ts(2026, 9, 18, 1, 28), now) == "09/18 01:28"
    assert format_when(ts(2025, 12, 31, 10, 0), now) == "2025/12/31 10:00"


def test_describe_stats() -> None:
    from blendkeep.gui.model import describe_stats
    from blendkeep.store import StoreStats

    assert describe_stats(StoreStats(0, 0, 0, 0)) == "履歴 0 件・保存サイズ 0 B"
    text = describe_stats(StoreStats(5, 3, 10 * 1024 * 1024, 4 * 1024 * 1024))
    assert "履歴 5 件" in text and "4.0 MB" in text and "10.0 MB" in text and "60%" in text
    # 圧縮の効果がないとき（Blender が圧縮済みのファイルだけ）は、節約の表示を出さない
    assert "節約" not in describe_stats(StoreStats(1, 1, 100, 100))
