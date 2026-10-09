from __future__ import annotations

from pathlib import Path

import pytest
from conftest import write_blend

from blendkeep.store import SnapshotStore


def test_add_and_list(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = write_blend(tmp_path / "proj" / "a.blend", b"v1")
    snap = store.add(f, note="first")
    assert snap is not None
    assert snap.note == "first"
    assert snap.size == f.stat().st_size
    assert store.object_path(snap.sha256).exists()
    assert [s.id for s in store.list(f)] == [snap.id]


def test_duplicate_content_is_skipped(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = write_blend(tmp_path / "a.blend", b"same")
    assert store.add(f) is not None
    assert store.add(f) is None
    assert len(store.list(f)) == 1


def test_changed_content_makes_new_snapshot(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = write_blend(tmp_path / "a.blend", b"one")
    first = store.add(f)
    write_blend(f, b"two")
    second = store.add(f)
    assert first is not None and second is not None
    assert first.sha256 != second.sha256
    assert [s.id for s in store.list(f)] == [second.id, first.id]


def test_identical_content_in_two_files_shares_one_object(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    a = write_blend(tmp_path / "a.blend", b"x")
    b = write_blend(tmp_path / "b.blend", b"x")
    sa, sb = store.add(a), store.add(b)
    assert sa is not None and sb is not None
    assert sa.sha256 == sb.sha256
    assert store.stats().unique_objects == 1


def test_restore_never_overwrites(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = write_blend(tmp_path / "a.blend", b"old")
    snap = store.add(f)
    assert snap is not None
    write_blend(f, b"new")
    restored = store.restore(snap.id)
    assert restored.name == f"a.restored-{snap.id}.blend"
    assert restored.read_bytes().endswith(b"old")
    assert f.read_bytes().endswith(b"new")
    with pytest.raises(FileExistsError):
        store.restore(snap.id)


def test_restore_unknown_id(store_dir: Path) -> None:
    with pytest.raises(KeyError):
        SnapshotStore(store_dir).restore(999)


def test_prune_keeps_newest_and_removes_orphans(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = tmp_path / "a.blend"
    snaps = []
    for i in range(5):
        write_blend(f, f"v{i}".encode())
        s = store.add(f)
        assert s is not None
        snaps.append(s)
    assert store.prune(f, keep=2) == 3
    assert [s.id for s in store.list(f)] == [snaps[4].id, snaps[3].id]
    assert not store.object_path(snaps[0].sha256).exists()
    assert store.object_path(snaps[4].sha256).exists()


def test_prune_keeps_object_used_by_other_file(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    a = write_blend(tmp_path / "a.blend", b"shared")
    b = write_blend(tmp_path / "b.blend", b"shared")
    sa = store.add(a)
    store.add(b)
    write_blend(a, b"changed")
    store.add(a)
    store.prune(a, keep=1)
    assert sa is not None
    assert store.object_path(sa.sha256).exists()


def test_prune_rejects_zero(tmp_path: Path, store_dir: Path) -> None:
    with pytest.raises(ValueError):
        SnapshotStore(store_dir).prune(tmp_path / "a.blend", keep=0)


# --- メモ・削除 -----------------------------------------------------------------


def test_set_note_and_clear(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = write_blend(tmp_path / "a.blend", b"x")
    snap = store.add(f)
    assert snap is not None
    assert store.set_note(snap.id, "  レンダー前  ")
    assert store.get(snap.id).note == "レンダー前"  # type: ignore[union-attr]
    assert store.set_note(snap.id, "   ")  # 空にすると消える
    assert store.get(snap.id).note is None  # type: ignore[union-attr]
    assert not store.set_note(999, "なし")


def test_delete_snapshots_frees_only_unreferenced_objects(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    a = write_blend(tmp_path / "a.blend", b"shared")
    b = write_blend(tmp_path / "b.blend", b"shared")
    only = write_blend(tmp_path / "c.blend", b"only-c")
    sa, sb, sc = store.add(a), store.add(b), store.add(only)
    assert sa is not None and sb is not None and sc is not None

    result = store.delete_snapshots([sa.id])
    assert (result.count, result.freed_bytes) == (1, 0)  # b が同じ内容を使っている
    assert store.has_object(sa.sha256)

    result = store.delete_snapshots([sb.id, sc.id, 12345])
    assert result.count == 2 and result.freed_bytes > 0
    assert not store.has_object(sa.sha256) and not store.has_object(sc.sha256)
    assert store.list() == []


def test_delete_nothing(store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    assert store.delete_snapshots([]).count == 0
    assert store.delete_snapshots([1, 2]).count == 0


def test_delete_file_history(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    a = tmp_path / "a.blend"
    b = write_blend(tmp_path / "b.blend", b"keep")
    for i in range(3):
        write_blend(a, f"v{i}".encode())
        store.add(a)
    store.add(b)
    result = store.delete_file_history(a)
    assert result.count == 3
    assert store.list(a) == [] and len(store.list(b)) == 1


def test_prune_never_deletes_snapshots_with_notes(tmp_path: Path, store_dir: Path) -> None:
    store = SnapshotStore(store_dir)
    f = tmp_path / "a.blend"
    snaps = []
    for i in range(6):
        write_blend(f, f"v{i}".encode())
        s = store.add(f)
        assert s is not None
        snaps.append(s)
    store.set_note(snaps[0].id, "納品版")  # いちばん古いが、メモ付き
    removed = store.prune(f, keep=2)
    assert removed == 3  # 古い5件のうち、メモ付きの1件は残る
    assert sorted(s.id for s in store.list(f)) == sorted([snaps[0].id, snaps[4].id, snaps[5].id])
    assert store.has_object(snaps[0].sha256)
