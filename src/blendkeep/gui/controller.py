"""画面と、監視・履歴ストアをつなぐ。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import webhook
from ..blendfile import Thumbnail
from ..config import Config
from ..render_events import EventSpool, RenderEvent, default_events_dir, should_notify
from ..store import (
    DeleteResult,
    MigrationResult,
    ProgressCallback,
    Snapshot,
    SnapshotStore,
    StoreStats,
    remove_store_files,
)
from ..watcher import BlendWatcher


class AppController(QObject):
    snapshotCreated = Signal(object)  # Snapshot（監視スレッドから発行される）
    stateChanged = Signal()
    renderFinished = Signal(object)  # RenderEvent（通知する条件を満たしたものだけ）

    def __init__(
        self,
        config: Config,
        config_path: Path | None = None,
        events_dir: Path | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.config_path = config_path
        self.store = self._open_store(config.store_dir, config)
        self.watcher: BlendWatcher | None = None
        self.paused = False
        self.previous_store_dir: Path | None = None
        self._thumbnails: dict[str, Thumbnail | None] = {}
        self.events = EventSpool(events_dir or default_events_dir(), self._on_render_event)

    @staticmethod
    def _open_store(store_dir, config: Config) -> SnapshotStore:
        return SnapshotStore(
            store_dir,
            compress=config.compress_history,
            chunk_large_files=config.chunk_large_files,
        )

    # --- 監視 ---------------------------------------------------------------

    @property
    def watching(self) -> bool:
        return self.watcher is not None

    def start(self) -> None:
        self.events.start()
        if self.watcher is not None or self.paused or not self.config.watch_dirs:
            return
        self.watcher = BlendWatcher(self.config, self.store, on_snapshot=self.snapshotCreated.emit)
        self.watcher.start()
        self.stateChanged.emit()

    def _on_render_event(self, event: RenderEvent) -> None:
        """レンダーのイベント（取り込みスレッドから呼ばれる）。"""
        if not should_notify(event, self.config.render_min_seconds):
            return
        if self.config.notify_on_render:
            self.renderFinished.emit(event)
        url = self.config.discord_webhook_url.strip()
        if url and webhook.is_valid_url(url):
            webhook.send_async(url, webhook.build_payload(event))

    def send_test_webhook(self, url: str) -> None:
        """設定画面の「テスト送信」。送れなければ例外を投げる。"""
        if not webhook.is_valid_url(url):
            raise ValueError("Webhook の URL が正しくありません")
        webhook.post(url, {"content": "BlendKeep からのテストです", "username": "BlendKeep"})

    def stop(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
            self.stateChanged.emit()

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        if paused:
            self.stop()
        else:
            self.start()
        self.stateChanged.emit()

    def apply_config(self, new_config: Config) -> None:
        """設定を保存して反映する。保存先の変更は move_store で行う（ここでは無視する）。"""
        self.stop()
        self.config = replace(new_config, store_dir=self.config.store_dir)
        self.store.compress = self.config.compress_history
        self.store.chunk_large_files = self.config.chunk_large_files
        self.config.save(self.config_path)
        self.start()
        self.stateChanged.emit()

    def move_store(self, new_dir: str, progress: ProgressCallback | None = None) -> MigrationResult:
        """履歴を別のフォルダへ移して、保存先を切り替える。元の履歴は消さない。"""
        self.stop()
        try:
            result = self.store.copy_to(new_dir, progress)
        except BaseException:
            self.start()
            raise
        old = self.store
        self.previous_store_dir = old.root
        self.store = self._open_store(new_dir, self.config)
        old.close()
        self.config = replace(self.config, store_dir=str(Path(new_dir)))
        self.config.save(self.config_path)
        self.start()
        self.stateChanged.emit()
        return result

    def delete_store_files(self, directory: Path) -> int:
        return remove_store_files(directory)

    def compact(self, progress: ProgressCallback | None = None) -> int:
        return self.store.compact(progress)

    def stats(self) -> StoreStats:
        return self.store.stats()

    def shutdown(self) -> None:
        self.stop()
        self.events.stop()
        self.store.close()

    # --- 履歴 ---------------------------------------------------------------

    def snapshots(self, path: str | None = None) -> list[Snapshot]:
        return self.store.list(path)

    def thumbnail(self, snapshot: Snapshot) -> Thumbnail | None:
        if snapshot.sha256 not in self._thumbnails:
            self._thumbnails[snapshot.sha256] = self.store.read_thumbnail(snapshot.sha256)
        return self._thumbnails[snapshot.sha256]

    def set_note(self, snapshot_id: int, note: str | None) -> bool:
        return self.store.set_note(snapshot_id, note)

    def delete_snapshots(self, snapshot_ids: list[int]) -> DeleteResult:
        return self.store.delete_snapshots(snapshot_ids)

    def delete_file_history(self, path: str) -> DeleteResult:
        return self.store.delete_file_history(path)

    def restore(self, snapshot_id: int, dest: str) -> Path:
        return self.store.restore(snapshot_id, dest)
