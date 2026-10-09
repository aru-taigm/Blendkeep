from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import zstandard

from blendkeep.blendfile import read_thumbnail
from blendkeep.store import (
    MigrationCancelled,
    SnapshotStore,
    StoreCorruptionError,
    remove_store_files,
)

REAL = Path(__file__).parent / "data" / "blender-5.2.2-compressed.blend"


def _plain_blend() -> bytes:
    """Blender 5.2.2 が保存した本物のファイルを、非圧縮の形に戻したもの。"""
    with REAL.open("rb") as f:
        return zstandard.ZstdDecompressor().stream_reader(f).read()


@pytest.fixture
def plain_file(tmp_path: Path) -> Path:
    path = tmp_path / "proj" / "scene.blend"
    path.parent.mkdir()
    path.write_bytes(_plain_blend())
    return path


def test_plain_blend_is_compressed(plain_file: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    original = plain_file.read_bytes()
    snap = store.add(plain_file)
    assert snap is not None
    assert snap.sha256 == hashlib.sha256(original).hexdigest()  # 識別は元の内容で行う
    stored = store.object_path(snap.sha256)
    assert stored.name.endswith(".blend.zst")
    assert stored.stat().st_size < len(original) * 0.4
    assert store.read_object_bytes(snap.sha256) == original


def test_restore_returns_exact_original_bytes(
    plain_file: Path, store_dir: Path, tmp_path: Path
) -> None:
    store = SnapshotStore(store_dir)
    snap = store.add(plain_file)
    assert snap is not None
    restored = store.restore(snap.id, tmp_path / "back.blend")
    assert restored.read_bytes() == plain_file.read_bytes()


def test_thumbnail_is_readable_from_compressed_object(plain_file: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    snap = store.add(plain_file)
    assert snap is not None
    thumb = read_thumbnail(store.object_path(snap.sha256))
    assert thumb is not None and (thumb.width, thumb.height) == (128, 128)


def test_blender_compressed_file_is_stored_as_is(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    snap = store.add(REAL)
    assert snap is not None
    stored = store.object_path(snap.sha256)
    assert stored.name.endswith(".blend") and not stored.name.endswith(".zst")
    assert stored.read_bytes() == REAL.read_bytes()


def test_compression_can_be_turned_off(plain_file: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir, compress=False)
    snap = store.add(plain_file)
    assert snap is not None
    assert store.object_path(snap.sha256).stat().st_size == plain_file.stat().st_size


def test_same_content_is_stored_once_even_if_setting_changes(
    plain_file: Path, tmp_path: Path, store_dir: Path
) -> None:
    store = SnapshotStore(store_dir, compress=False)
    other = tmp_path / "proj" / "copy.blend"
    other.write_bytes(plain_file.read_bytes())
    assert store.add(plain_file) is not None
    store.compress = True
    assert store.add(other) is not None
    assert store.stats().unique_objects == 1


def test_compact_shrinks_existing_raw_objects(
    plain_file: Path, store_dir: Path, tmp_path: Path
) -> None:
    store = SnapshotStore(store_dir, compress=False)
    original = plain_file.read_bytes()
    snaps = []
    for i in range(3):
        plain_file.write_bytes(original + bytes([i]) * 1000)
        snap = store.add(plain_file)
        assert snap is not None
        snaps.append(snap)
    store.add(REAL)  # すでに圧縮済みのファイルは、そのまま
    before = store.stats()

    steps: list[tuple[int, int]] = []
    saved = store.compact(lambda done, total, _name: steps.append((done, total)))
    after = store.stats()
    assert saved > 0
    assert after.stored_bytes == before.stored_bytes - saved
    assert after.original_bytes == before.original_bytes
    assert steps and steps[-1][0] == steps[-1][1]
    for i, snap in enumerate(snaps):
        assert store.object_path(snap.sha256).name.endswith(".zst")
        restored = store.restore(snap.id, tmp_path / f"r{i}.blend")
        assert restored.read_bytes() == original + bytes([i]) * 1000
    assert store.compact() == 0  # 2回目は何も変わらない


def test_corrupted_object_is_detected_on_restore(
    plain_file: Path, store_dir: Path, tmp_path: Path
) -> None:
    store = SnapshotStore(store_dir, compress=False)
    snap = store.add(plain_file)
    assert snap is not None
    path = store.object_path(snap.sha256)
    data = bytearray(path.read_bytes())
    data[100] ^= 0xFF
    path.write_bytes(bytes(data))
    dest = tmp_path / "out.blend"
    with pytest.raises(StoreCorruptionError):
        store.restore(snap.id, dest)
    assert not dest.exists() and not dest.with_name("out.blend.tmp").exists()


def test_prune_removes_compressed_objects(plain_file: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    original = plain_file.read_bytes()
    first = store.add(plain_file)
    plain_file.write_bytes(original + b"x")
    assert store.add(plain_file) is not None
    assert first is not None
    store.prune(plain_file, keep=1)
    assert not store.has_object(first.sha256)


def test_stats(plain_file: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    assert store.stats().snapshots == 0
    store.add(plain_file)
    stats = store.stats()
    assert (stats.snapshots, stats.unique_objects) == (1, 1)
    assert stats.original_bytes == plain_file.stat().st_size
    assert 0 < stats.stored_bytes < stats.original_bytes


# --- 保存先の移動 ---------------------------------------------------------------


def _filled_store(plain_file: Path, store_dir: Path, versions: int = 3) -> SnapshotStore:
    store = SnapshotStore(store_dir)
    original = plain_file.read_bytes()
    for i in range(versions):
        plain_file.write_bytes(original + bytes([i + 1]) * 500)
        store.add(plain_file, note=f"v{i}")
    return store


def test_copy_to_new_folder(plain_file: Path, store_dir: Path, tmp_path: Path) -> None:
    store = _filled_store(plain_file, store_dir)
    new_dir = tmp_path / "elsewhere" / "store"
    steps: list[tuple[int, int]] = []
    result = store.copy_to(new_dir, lambda done, total, _n: steps.append((done, total)))
    assert result.objects == 3 and not result.adopted
    assert steps[-1][0] == steps[-1][1] > 0

    moved = SnapshotStore(new_dir)
    assert [(s.id, s.note) for s in moved.list()] == [(s.id, s.note) for s in store.list()]
    for snap in moved.list():
        out = moved.restore(snap.id, tmp_path / f"m{snap.id}.blend")
        assert hashlib.sha256(out.read_bytes()).hexdigest() == snap.sha256
    # 元の履歴は消えていない
    assert store.stats().snapshots == 3


def test_copy_to_existing_store_adopts_it(
    plain_file: Path, store_dir: Path, tmp_path: Path
) -> None:
    store = _filled_store(plain_file, store_dir)
    new_dir = tmp_path / "other"
    store.copy_to(new_dir)
    again = store.copy_to(new_dir)
    assert again.adopted and again.bytes_copied == 0


def test_copy_to_rejects_nested_folders(plain_file: Path, store_dir: Path) -> None:
    store = _filled_store(plain_file, store_dir)
    with pytest.raises(ValueError):
        store.copy_to(store_dir)
    with pytest.raises(ValueError):
        store.copy_to(store_dir / "inside")
    with pytest.raises(ValueError):
        store.copy_to(store_dir.parent)


def test_copy_to_can_be_cancelled_without_leftovers(
    plain_file: Path, store_dir: Path, tmp_path: Path
) -> None:
    store = _filled_store(plain_file, store_dir)
    new_dir = tmp_path / "cancelled"

    def cancel(done: int, total: int, name: str) -> None:
        raise MigrationCancelled

    with pytest.raises(MigrationCancelled):
        store.copy_to(new_dir, cancel)
    leftovers = [p for p in new_dir.rglob("*") if p.is_file()] if new_dir.exists() else []
    assert leftovers == []
    assert store.stats().snapshots == 3
    assert store.restore(1, tmp_path / "still-works.blend").exists()


def test_remove_store_files_only_touches_known_files(
    plain_file: Path, store_dir: Path, tmp_path: Path
) -> None:
    store = _filled_store(plain_file, store_dir)
    other = tmp_path / "unrelated.txt"
    other.write_text("keep me")
    (store_dir / "notes.txt").write_text("keep me too")
    store.close()
    freed = remove_store_files(store_dir)
    assert freed > 0
    assert not (store_dir / "objects").exists() and not (store_dir / "index.db").exists()
    assert (store_dir / "notes.txt").read_text() == "keep me too"
    assert other.exists()


def test_remove_store_files_deletes_empty_folder(plain_file: Path, store_dir: Path) -> None:
    store = _filled_store(plain_file, store_dir)
    store.close()
    remove_store_files(store_dir)
    assert not store_dir.exists()


def test_open_object_missing(store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    with pytest.raises(FileNotFoundError), store.open_object("0" * 64) as f:
        f.read()
