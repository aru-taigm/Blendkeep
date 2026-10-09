"""画面に出す情報の組み立て（Qt に依存しない部分）。"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass

from ..formatting import format_size
from ..store import Snapshot, StoreStats, source_key


@dataclass(frozen=True)
class FileEntry:
    key: str
    path: str
    name: str
    folder: str
    count: int
    latest: float


def group_files(snapshots: Iterable[Snapshot]) -> list[FileEntry]:
    """履歴を .blend ファイルごとにまとめる。新しく保存したものを上にする。"""
    groups: dict[str, list[Snapshot]] = {}
    for snap in snapshots:
        groups.setdefault(source_key(snap.source_path), []).append(snap)
    entries = []
    for key, items in groups.items():
        newest = max(items, key=lambda s: (s.taken_at, s.id))
        entries.append(
            FileEntry(
                key=key,
                path=newest.source_path,
                name=os.path.basename(newest.source_path),
                folder=os.path.dirname(newest.source_path),
                count=len(items),
                latest=newest.taken_at,
            )
        )
    return sorted(entries, key=lambda e: e.latest, reverse=True)


def default_restore_path(snapshot: Snapshot) -> str:
    original = snapshot.source_path
    stem, ext = os.path.splitext(os.path.basename(original))
    return os.path.join(os.path.dirname(original), f"{stem}.restored-{snapshot.id}{ext}")


def describe_stats(stats: StoreStats) -> str:
    """履歴の件数と、使っている容量。"""
    text = f"履歴 {stats.snapshots} 件・保存サイズ {format_size(stats.stored_bytes)}"
    if stats.original_bytes > stats.stored_bytes > 0:
        saved = stats.original_bytes - stats.stored_bytes
        percent = round(saved / stats.original_bytes * 100)
        text += f"（圧縮前 {format_size(stats.original_bytes)} から {percent}% 節約）"
    return text
