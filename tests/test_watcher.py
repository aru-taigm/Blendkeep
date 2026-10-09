from __future__ import annotations

import time
from pathlib import Path

from conftest import write_blend

from blendkeep.config import Config
from blendkeep.store import SnapshotStore
from blendkeep.watcher import BlendWatcher


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _make(tmp_path: Path, **overrides: float) -> tuple[Config, SnapshotStore, BlendWatcher, Path]:
    watched = tmp_path / "projects"
    watched.mkdir()
    cfg = Config(
        watch_dirs=[str(watched)],
        store_dir=str(tmp_path / "store"),
        debounce_seconds=0.3,
        min_interval_seconds=0.0,
        **overrides,
    )
    store = SnapshotStore(cfg.store_dir)
    return cfg, store, BlendWatcher(cfg, store, poll_interval=0.05), watched


def test_snapshot_on_save(tmp_path: Path) -> None:
    _, store, watcher, watched = _make(tmp_path)
    watcher.start()
    try:
        f = write_blend(watched / "sub" / "scene.blend", b"v1")
        assert _wait_for(lambda: len(store.list(f)) == 1)
        write_blend(f, b"v2 is longer")
        assert _wait_for(lambda: len(store.list(f)) == 2)
    finally:
        watcher.stop()


def test_rename_into_place_is_detected(tmp_path: Path) -> None:
    """一時ファイルに書いてからリネームする保存方式でも検知できる。"""
    _, store, watcher, watched = _make(tmp_path)
    watcher.start()
    try:
        tmp = write_blend(watched / "scene.blend@", b"saved via temp")
        final = watched / "scene.blend"
        tmp.rename(final)
        assert _wait_for(lambda: len(store.list(final)) == 1)
    finally:
        watcher.stop()


def test_non_blend_and_backups_are_ignored(tmp_path: Path) -> None:
    _, store, watcher, watched = _make(tmp_path)
    watcher.start()
    try:
        write_blend(watched / "scene.blend1", b"backup")
        (watched / "notes.txt").write_text("hello")
        (watched / "fake.blend").write_bytes(b"not a blender file at all")
        time.sleep(1.2)
        assert store.list() == []
    finally:
        watcher.stop()


def test_rapid_saves_are_merged(tmp_path: Path) -> None:
    _, store, watcher, watched = _make(tmp_path)
    watcher.start()
    try:
        f = watched / "scene.blend"
        for i in range(5):
            write_blend(f, f"v{i}".encode())
            time.sleep(0.05)
        assert _wait_for(lambda: len(store.list(f)) >= 1)
        time.sleep(0.8)
        snaps = store.list(f)
        assert len(snaps) == 1
        assert store.read_object_bytes(snaps[0].sha256).endswith(b"v4")
    finally:
        watcher.stop()


def test_min_interval_defers_but_keeps_last_state(tmp_path: Path) -> None:
    cfg, store, watcher, watched = _make(tmp_path)
    cfg.min_interval_seconds = 1.5
    watcher.start()
    try:
        f = write_blend(watched / "scene.blend", b"first")
        assert _wait_for(lambda: len(store.list(f)) == 1)
        write_blend(f, b"second")
        time.sleep(0.8)
        assert len(store.list(f)) == 1  # まだ間隔が空いていない
        assert _wait_for(lambda: len(store.list(f)) == 2, timeout=5)
    finally:
        watcher.stop()


def test_store_dir_inside_watch_dir_is_ignored(tmp_path: Path) -> None:
    watched = tmp_path / "projects"
    watched.mkdir()
    cfg = Config(
        watch_dirs=[str(watched)],
        store_dir=str(watched / ".blendkeep"),
        debounce_seconds=0.2,
        min_interval_seconds=0.0,
    )
    store = SnapshotStore(cfg.store_dir)
    watcher = BlendWatcher(cfg, store, poll_interval=0.05)
    watcher.start()
    try:
        f = write_blend(watched / "scene.blend", b"data")
        assert _wait_for(lambda: len(store.list(f)) == 1)
        time.sleep(1.0)
        assert len(store.list()) == 1  # 保存先の中のコピーを再度履歴にしない
    finally:
        watcher.stop()


def test_save_during_processing_is_not_lost(tmp_path: Path) -> None:
    """履歴を作っている最中に次の保存が来ても、取りこぼさない（CI で見つかった競合の再現）。"""
    _, store, watcher, watched = _make(tmp_path)
    f = watched / "scene.blend"
    write_blend(f, b"v1")
    watcher.notice(str(f))

    def save_again(_snapshot) -> None:
        # on_snapshot は、履歴を作った直後・待ち行列から外す直前に呼ばれる。
        write_blend(f, b"v2 is longer")
        watcher.notice(str(f))
        watcher.on_snapshot = None

    watcher.on_snapshot = save_again
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and len(store.list(f)) < 2:
        watcher.process_pending()
        time.sleep(0.05)
    assert len(store.list(f)) == 2
