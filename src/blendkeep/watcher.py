"""フォルダを監視して、.blend が保存されたら履歴を作る。

Blender の保存の仕組み（一時ファイルを経由するかなど）には依存しない。
.blend への作成・更新・リネームのイベントを集め、ファイルの大きさと更新時刻が
一定時間変わらなくなったら「保存完了」とみなす。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .blendfile import looks_like_blend
from .config import Config
from .store import FileChangedError, Snapshot, SnapshotStore

log = logging.getLogger(__name__)


@dataclass
class _Pending:
    last_event: float
    last_stat: tuple[int, int] | None = None
    not_before: float = 0.0
    # イベントが届くたびに増える。処理中に新しい保存があったかを見分けるのに使う。
    generation: int = 0


def _is_blend_path(path: str) -> bool:
    return path.lower().endswith(".blend")


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: BlendWatcher) -> None:
        self._watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        self._notify(event.src_path, event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._notify(event.src_path, event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._notify(event.dest_path, event)

    def _notify(self, path: str | bytes, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = os.fsdecode(path)
        if _is_blend_path(path):
            self._watcher.notice(path)


class BlendWatcher:
    def __init__(
        self,
        config: Config,
        store: SnapshotStore,
        on_snapshot: Callable[[Snapshot], None] | None = None,
        poll_interval: float = 0.25,
    ) -> None:
        self.config = config
        self.store = store
        self.on_snapshot = on_snapshot
        self.poll_interval = poll_interval
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = Observer()
        self._store_root = Path(store.root).resolve()

    # --- 起動・停止 ---------------------------------------------------------

    def start(self) -> None:
        handler = _Handler(self)
        watched = 0
        for directory in self.config.watch_dirs:
            if not os.path.isdir(directory):
                log.warning("監視フォルダが見つかりません: %s", directory)
                continue
            self._observer.schedule(handler, directory, recursive=True)
            watched += 1
        if watched == 0:
            log.warning("監視できるフォルダがありません")
        self._observer.start()
        self._thread = threading.Thread(target=self._run, name="blendkeep-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._observer.stop()
        self._observer.join(timeout=5)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # --- イベント -----------------------------------------------------------

    def notice(self, path: str) -> None:
        resolved = Path(path).resolve()
        if self._store_root in resolved.parents:
            return
        with self._lock:
            entry = self._pending.get(path)
            if entry is None:
                self._pending[path] = _Pending(last_event=time.monotonic(), generation=1)
            else:
                entry.last_event = time.monotonic()
                entry.generation += 1

    # --- 処理 ---------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop.wait(self.poll_interval):
            try:
                self.process_pending()
            except Exception:  # noqa: BLE001  監視を止めない
                log.exception("履歴の作成中にエラーが発生しました")

    def process_pending(self) -> None:
        now = time.monotonic()
        with self._lock:
            items = list(self._pending.items())
        for path, entry in items:
            if now < entry.not_before or now - entry.last_event < self.config.debounce_seconds:
                continue
            seen = entry.generation
            if self._ready(path, entry, now):
                with self._lock:
                    # 処理している間に新しい保存があった場合は、残して次の周回で扱う。
                    if self._pending.get(path) is entry and entry.generation == seen:
                        del self._pending[path]

    def _ready(self, path: str, entry: _Pending, now: float) -> bool:
        """処理が終わった（または諦めた）ら True を返す。まだ待つなら False。"""
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return True
        stat_key = (st.st_size, st.st_mtime_ns)
        if entry.last_stat != stat_key:
            entry.last_stat = stat_key
            entry.last_event = now
            return False
        if not looks_like_blend(path):
            log.info("Blender のファイルではないため無視しました: %s", path)
            return True
        latest = self.store.latest(path)
        if latest is not None:
            wait = latest.taken_at + self.config.min_interval_seconds - time.time()
            if wait > 0:
                entry.not_before = now + wait
                return False
        try:
            snap = self.store.add(path)
        except FileChangedError:
            entry.last_stat = None
            entry.last_event = now
            return False
        except OSError:
            log.exception("読み込めませんでした: %s", path)
            return True
        if snap is not None:
            self.store.prune(path, self.config.max_versions_per_file)
            log.info("履歴を作成: %s (id=%d)", path, snap.id)
            if self.on_snapshot is not None:
                self.on_snapshot(snap)
        return True
