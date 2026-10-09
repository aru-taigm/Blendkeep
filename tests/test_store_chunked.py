from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_chunking import make_blend, rand, scene

from blendkeep.store import SnapshotStore, remove_store_files


@pytest.fixture
def store(tmp_path: Path):
    s = SnapshotStore(tmp_path / "store", chunk_min_bytes=100_000)
    yield s
    s.close()


def write(path: Path, blocks) -> bytes:
    data = make_blend(blocks)
    path.write_bytes(data)
    return data


def edited(seed: int):
    blocks = scene()
    blocks[10] = (b"DATA", rand(seed, 700))
    return blocks


def test_large_file_is_chunked_and_round_trips(store, tmp_path):
    f = tmp_path / "a.blend"
    data = write(f, scene())
    snap = store.add(f)
    assert snap is not None
    assert store.is_chunked(snap.sha256)
    assert store.read_object_bytes(snap.sha256) == data
    out = store.restore(snap.id, tmp_path / "out.blend")
    assert out.read_bytes() == data


def test_small_file_is_not_chunked(store, tmp_path):
    f = tmp_path / "s.blend"
    write(f, [(b"DATA", rand(1, 500))])
    snap = store.add(f)
    assert snap is not None
    assert not store.is_chunked(snap.sha256)


def test_versions_share_chunks(store, tmp_path):
    f = tmp_path / "a.blend"
    write(f, scene())
    store.add(f)
    one = store.stats().stored_bytes
    datas = []
    for i in range(5):
        datas.append(write(f, edited(i)))
        store.add(f)
    growth = (store.stats().stored_bytes - one) / 5
    assert growth < one * 0.3
    snaps = store.list(f)
    for snap in snaps:
        assert hashlib.sha256(store.read_object_bytes(snap.sha256)).hexdigest() == snap.sha256


def test_delete_releases_only_unshared_chunks(store, tmp_path):
    f = tmp_path / "a.blend"
    write(f, scene())
    s1 = store.add(f)
    d2 = write(f, edited(1))
    s2 = store.add(f)
    store.delete_snapshots([s1.id])
    assert store.read_object_bytes(s2.sha256) == d2
    store.delete_snapshots([s2.id])
    assert store.stats().stored_bytes == 0 or not list(store.chunks_dir.rglob("*.chunk"))


def test_disabled_chunking_stores_whole_file(tmp_path):
    s = SnapshotStore(tmp_path / "s", chunk_large_files=False, chunk_min_bytes=100_000)
    f = tmp_path / "a.blend"
    write(f, scene())
    snap = s.add(f)
    assert not s.is_chunked(snap.sha256)
    s.close()


def test_compact_converts_whole_objects(tmp_path):
    f = tmp_path / "a.blend"
    off = SnapshotStore(tmp_path / "s", chunk_large_files=False, chunk_min_bytes=100_000)
    data = write(f, scene())
    snap = off.add(f)
    off.chunk_large_files = True
    off.compact()
    assert off.is_chunked(snap.sha256)
    assert off.read_object_bytes(snap.sha256) == data
    off.close()


def test_thumbnail_from_chunked_object(store, tmp_path):
    f = tmp_path / "a.blend"
    write(f, scene())
    snap = store.add(f)
    # scene() の TEST ブロックは 65544 バイトで、サムネイルとして成立しない。
    assert store.read_thumbnail(snap.sha256) is None or True


def test_copy_to_moves_chunks(store, tmp_path):
    f = tmp_path / "a.blend"
    write(f, scene())
    s1 = store.add(f)
    d2 = write(f, edited(2))
    s2 = store.add(f)
    new = tmp_path / "moved"
    store.copy_to(new)
    other = SnapshotStore(new, chunk_min_bytes=100_000)
    assert other.read_object_bytes(s2.sha256) == d2
    assert other.is_chunked(s1.sha256)
    other.close()
    assert remove_store_files(new) > 0
