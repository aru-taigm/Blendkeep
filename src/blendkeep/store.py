"""スナップショットの保存先。

履歴の本体は、内容の SHA-256 で名前を付けて保存する（同じ内容は1つしか持たない）。

* 小さいファイル: ``objects/`` に、zstd で圧縮した ``.blend.zst`` として保存する。
  Blender が圧縮済みのファイルは、そのまま ``.blend`` で保存する。
* 大きなファイル（既定は 1 MiB 以上で、圧縮していない .blend）: ``chunking`` で塊（チャンク）に
  分け、``chunks/`` に、塊ごとに圧縮して保存する。版どうしで共通する塊は、1回しか保存されない。
  どの塊をどの順に並べると元のファイルになるかは、SQLite に持つ。

履歴の一覧も SQLite（``index.db``）に持つ。
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import zstandard

from . import chunking
from .blendfile import Thumbnail, read_thumbnail

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key  TEXT NOT NULL,
    source_path TEXT NOT NULL,
    taken_at    REAL NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_source ON snapshots(source_key, taken_at);
CREATE TABLE IF NOT EXISTS chunked_objects (
    sha256 TEXT PRIMARY KEY,
    size   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS object_chunks (
    object_sha TEXT NOT NULL,
    idx        INTEGER NOT NULL,
    chunk_sha  TEXT NOT NULL,
    PRIMARY KEY (object_sha, idx)
);
CREATE TABLE IF NOT EXISTS chunks (
    sha256 TEXT PRIMARY KEY,
    size   INTEGER NOT NULL,
    stored INTEGER NOT NULL,
    refs   INTEGER NOT NULL
);
"""

_CHUNK = 1024 * 1024
_PLAIN_MAGIC = b"BLENDER"
_ZSTD_LEVEL = 3
_RAW_SUFFIX = ".blend"
_ZSTD_SUFFIX = ".blend.zst"
_CHUNK_SUFFIX = ".chunk"
CHUNK_MIN_BYTES = 1024 * 1024  # これ未満のファイルは、分けずに丸ごと保存する

# progress(処理済みバイト数, 全体のバイト数, 説明)
ProgressCallback = Callable[[int, int, str], None]


class FileChangedError(RuntimeError):
    """コピー中に元ファイルが変更された。"""


class StoreCorruptionError(RuntimeError):
    """保存されている履歴の内容が、記録と一致しない。"""


class MigrationCancelled(Exception):  # noqa: N818  中断は異常ではない
    """移動の途中で中止された。"""


@dataclass(frozen=True)
class Snapshot:
    id: int
    source_path: str
    taken_at: float
    size: int
    sha256: str
    note: str | None


@dataclass(frozen=True)
class StoreStats:
    snapshots: int
    unique_objects: int
    original_bytes: int  # 圧縮する前の合計（同じ内容は1回だけ数える）
    stored_bytes: int  # ディスク上の合計


@dataclass(frozen=True)
class DeleteResult:
    count: int  # 削除した履歴の件数
    freed_bytes: int  # 空いた容量（ほかの履歴が使っている内容は消さない）


@dataclass(frozen=True)
class MigrationResult:
    objects: int
    bytes_copied: int
    adopted: bool = False  # 移動先にすでにあった履歴をそのまま使った


def source_key(path: str | os.PathLike[str]) -> str:
    """同じファイルを同じキーにする（Windows は大文字小文字を区別しない）。"""
    return os.path.normcase(os.path.abspath(path))


def _is_plain_blend(head: bytes) -> bool:
    return head.startswith(_PLAIN_MAGIC)


class _Prefixed(io.RawIOBase):
    """すでに読んだ先頭のバイトを、ストリームの前に付け直して、先頭から読めるようにする。"""

    def __init__(self, head: bytes, stream: BinaryIO) -> None:
        self._head = head
        self._stream = stream

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def]
        if self._head:
            take = min(len(buffer), len(self._head))
            buffer[:take] = self._head[:take]
            self._head = self._head[take:]
            return take
        data = self._stream.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


class _ChunkedReader(io.RawIOBase):
    """チャンクに分けて保存した履歴を、元の1つのファイルのように、先頭から順に読む。"""

    def __init__(self, store: SnapshotStore, chunks: list[tuple[str, int]]) -> None:
        self._store = store
        self._chunks = iter(chunks)
        self._current = b""
        self._offset = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def]
        want = len(buffer)
        filled = 0
        while filled < want:
            if self._offset >= len(self._current):
                nxt = next(self._chunks, None)
                if nxt is None:
                    break
                self._current = self._store._read_chunk(*nxt)
                self._offset = 0
            take = min(want - filled, len(self._current) - self._offset)
            buffer[filled : filled + take] = self._current[self._offset : self._offset + take]
            self._offset += take
            filled += take
        return filled

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            parts = []
            while data := self.read(_CHUNK):
                parts.append(data)
            return b"".join(parts)
        buf = bytearray(size)
        count = self.readinto(buf)
        return bytes(buf[:count])


class SnapshotStore:
    def __init__(
        self,
        store_dir: str | os.PathLike[str],
        compress: bool = True,
        chunk_large_files: bool = True,
        chunk_min_bytes: int = CHUNK_MIN_BYTES,
    ) -> None:
        self.root = Path(store_dir)
        self.objects = self.root / "objects"
        self.chunks_dir = self.root / "chunks"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.compress = compress
        self.chunk_large_files = chunk_large_files
        self.chunk_min_bytes = chunk_min_bytes
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.root / "index.db", check_same_thread=False)
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- 保存場所 -------------------------------------------------------------

    def _paths(self, sha256: str) -> tuple[Path, Path]:
        base = self.objects / sha256[:2]
        return base / f"{sha256}{_RAW_SUFFIX}", base / f"{sha256}{_ZSTD_SUFFIX}"

    def _chunk_path(self, chunk_sha: str) -> Path:
        return self.chunks_dir / chunk_sha[:2] / f"{chunk_sha}{_CHUNK_SUFFIX}"

    def object_path(self, sha256: str) -> Path:
        """丸ごと保存されているファイル。まだ無ければ、圧縮なしの場合の場所を返す。

        チャンクに分けて保存した履歴には、1つのファイルがない（その場合も、同じ形の場所を返す）。
        中身を読むときは open_object を使う。
        """
        raw, zst = self._paths(sha256)
        return zst if zst.exists() and not raw.exists() else raw

    def is_chunked(self, sha256: str) -> bool:
        with self._lock:
            return self._is_chunked_locked(sha256)

    def _is_chunked_locked(self, sha256: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM chunked_objects WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return row is not None

    def has_object(self, sha256: str) -> bool:
        return any(p.exists() for p in self._paths(sha256)) or self.is_chunked(sha256)

    @contextmanager
    def open_object(self, sha256: str) -> Iterator[BinaryIO]:
        """保存されている内容を、元のバイト列として読む（圧縮は自動で展開）。"""
        raw, zst = self._paths(sha256)
        if raw.exists():
            with open(raw, "rb") as f:
                yield f
        elif zst.exists():
            with open(zst, "rb") as f:
                reader = zstandard.ZstdDecompressor().stream_reader(f)
                try:
                    yield reader  # type: ignore[misc]
                finally:
                    reader.close()
        elif self.is_chunked(sha256):
            yield _ChunkedReader(self, self._chunk_list(sha256))  # type: ignore[misc]
        else:
            raise FileNotFoundError(f"履歴の本体が見つかりません: {sha256}")

    def read_object_bytes(self, sha256: str) -> bytes:
        with self.open_object(sha256) as f:
            return f.read()

    def read_thumbnail(self, sha256: str) -> Thumbnail | None:
        """履歴に埋め込まれているサムネイル（保存の形を問わない）。"""
        try:
            with self.open_object(sha256) as stream:
                return read_thumbnail(stream)
        except (FileNotFoundError, StoreCorruptionError):
            return None

    def _chunk_list(self, object_sha: str) -> list[tuple[str, int]]:
        with self._lock:
            return [
                (row[0], row[1])
                for row in self._db.execute(
                    "SELECT oc.chunk_sha, c.size FROM object_chunks oc"
                    " JOIN chunks c ON c.sha256 = oc.chunk_sha"
                    " WHERE oc.object_sha = ? ORDER BY oc.idx",
                    (object_sha,),
                )
            ]

    def _read_chunk(self, chunk_sha: str, size: int) -> bytes:
        try:
            with open(self._chunk_path(chunk_sha), "rb") as f:
                data = zstandard.ZstdDecompressor().decompress(f.read(), max_output_size=size)
        except (OSError, zstandard.ZstdError) as error:
            raise StoreCorruptionError(f"チャンクを読めません: {chunk_sha[:12]}") from error
        if hashlib.sha256(data).hexdigest() != chunk_sha:
            raise StoreCorruptionError(f"チャンクの内容が壊れています: {chunk_sha[:12]}")
        return data

    # --- 追加 ---------------------------------------------------------------

    def _should_chunk(self, src: Path, size: int) -> bool:
        if not self.chunk_large_files or size < self.chunk_min_bytes:
            return False
        with open(src, "rb") as f:
            return f.read(len(_PLAIN_MAGIC)).startswith(_PLAIN_MAGIC) and (
                f.seek(0) == 0 and chunking.can_chunk(f)
            )

    def add(self, path: str | os.PathLike[str], note: str | None = None) -> Snapshot | None:
        """ファイルを履歴に追加する。直前の履歴と同じ内容なら None を返す。"""
        src = Path(path)
        before = src.stat()
        if self._should_chunk(src, before.st_size):
            return self._add_chunked(src, before, note)
        return self._add_whole(src, before, note)

    def _add_chunked(self, src: Path, before: os.stat_result, note: str | None) -> Snapshot | None:
        digest = hashlib.sha256()
        order: list[str] = []
        new_chunks: dict[str, tuple[int, int]] = {}  # sha -> (size, 保存サイズ)
        cctx = zstandard.ZstdCompressor(level=_ZSTD_LEVEL)
        try:
            with open(src, "rb") as f:
                for data in chunking.iter_chunks(f, digest):
                    chunk_sha = hashlib.sha256(data).hexdigest()
                    order.append(chunk_sha)
                    if chunk_sha in new_chunks or self._chunk_known(chunk_sha):
                        continue
                    new_chunks[chunk_sha] = (len(data), self._write_chunk(cctx, chunk_sha, data))
            after = src.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise FileChangedError(f"コピー中に変更されました: {src}")
            sha = digest.hexdigest()
            key = source_key(src)
            with self._lock:
                latest = self._latest_locked(key)
                if latest is not None and latest.sha256 == sha:
                    self._discard_unreferenced_locked(new_chunks)
                    return None
                if self._has_object_locked(sha):
                    self._discard_unreferenced_locked(new_chunks)
                else:
                    self._register_chunked_locked(sha, after.st_size, order, new_chunks)
                cur = self._db.execute(
                    "INSERT INTO snapshots(source_key, source_path, taken_at, size, sha256, note)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (key, str(src.absolute()), time.time(), after.st_size, sha, note),
                )
                self._db.commit()
                return self._get_locked(cur.lastrowid)
        except BaseException:
            with self._lock:
                self._discard_unreferenced_locked(new_chunks)
            raise

    def _has_object_locked(self, sha256: str) -> bool:
        return any(p.exists() for p in self._paths(sha256)) or self._is_chunked_locked(sha256)

    def _chunk_known(self, chunk_sha: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM chunks WHERE sha256 = ?", (chunk_sha,)).fetchone()
        return row is not None and self._chunk_path(chunk_sha).exists()

    def _write_chunk(self, cctx: zstandard.ZstdCompressor, chunk_sha: str, data: bytes) -> int:
        packed = cctx.compress(data)
        dest = self._chunk_path(chunk_sha)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(packed)
        os.replace(tmp, dest)
        return len(packed)

    def _register_chunked_locked(
        self,
        sha: str,
        size: int,
        order: list[str],
        new_chunks: dict[str, tuple[int, int]],
    ) -> None:
        self._db.execute("INSERT INTO chunked_objects(sha256, size) VALUES (?, ?)", (sha, size))
        self._db.executemany(
            "INSERT INTO object_chunks(object_sha, idx, chunk_sha) VALUES (?, ?, ?)",
            [(sha, i, c) for i, c in enumerate(order)],
        )
        counts: dict[str, int] = {}
        for chunk_sha in order:
            counts[chunk_sha] = counts.get(chunk_sha, 0) + 1
        for chunk_sha, count in counts.items():
            if chunk_sha in new_chunks:
                chunk_size, stored = new_chunks[chunk_sha]
                self._db.execute(
                    "INSERT OR IGNORE INTO chunks(sha256, size, stored, refs) VALUES (?, ?, ?, 0)",
                    (chunk_sha, chunk_size, stored),
                )
            self._db.execute(
                "UPDATE chunks SET refs = refs + ? WHERE sha256 = ?", (count, chunk_sha)
            )

    def _discard_unreferenced_locked(self, new_chunks: dict[str, tuple[int, int]]) -> None:
        """この追加で書いたが、どこからも使われないチャンクを消す。"""
        for chunk_sha in new_chunks:
            row = self._db.execute("SELECT 1 FROM chunks WHERE sha256 = ?", (chunk_sha,)).fetchone()
            if row is None:
                self._chunk_path(chunk_sha).unlink(missing_ok=True)

    def _add_whole(self, src: Path, before: os.stat_result, note: str | None) -> Snapshot | None:
        self.objects.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(dir=self.root, prefix="incoming-", suffix=".tmp")
        digest = hashlib.sha256()
        compressed = False
        try:
            with os.fdopen(tmp_fd, "wb") as out, open(src, "rb") as f:
                compressed = self.compress and _is_plain_blend(f.read(len(_PLAIN_MAGIC)))
                f.seek(0)
                if compressed:
                    cctx = zstandard.ZstdCompressor(level=_ZSTD_LEVEL, threads=-1)
                    with cctx.stream_writer(out, closefd=False) as writer:
                        while chunk := f.read(_CHUNK):
                            digest.update(chunk)
                            writer.write(chunk)
                else:
                    while chunk := f.read(_CHUNK):
                        digest.update(chunk)
                        out.write(chunk)
            after = src.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise FileChangedError(f"コピー中に変更されました: {src}")
            sha = digest.hexdigest()
            key = source_key(src)
            with self._lock:
                latest = self._latest_locked(key)
                if latest is not None and latest.sha256 == sha:
                    return None
                if not self._has_object_locked(sha):
                    raw, zst = self._paths(sha)
                    dest = zst if compressed else raw
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(tmp_name, dest)
                cur = self._db.execute(
                    "INSERT INTO snapshots(source_key, source_path, taken_at, size, sha256, note)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (key, str(src.absolute()), time.time(), after.st_size, sha, note),
                )
                self._db.commit()
                return self._get_locked(cur.lastrowid)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    # --- 参照 ---------------------------------------------------------------

    def latest(self, path: str | os.PathLike[str]) -> Snapshot | None:
        with self._lock:
            return self._latest_locked(source_key(path))

    def get(self, snapshot_id: int) -> Snapshot | None:
        with self._lock:
            return self._get_locked(snapshot_id)

    def list(
        self, path: str | os.PathLike[str] | None = None, limit: int | None = None
    ) -> list[Snapshot]:
        sql = "SELECT id, source_path, taken_at, size, sha256, note FROM snapshots"
        args: list[object] = []
        if path is not None:
            sql += " WHERE source_key = ?"
            args.append(source_key(path))
        sql += " ORDER BY taken_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self._lock:
            return [Snapshot(*row) for row in self._db.execute(sql, args)]

    def stats(self) -> StoreStats:
        with self._lock:
            snapshots = self._db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            unique, original = self._db.execute(
                "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM"
                " (SELECT sha256, MAX(size) AS size FROM snapshots GROUP BY sha256)"
            ).fetchone()
        return StoreStats(snapshots, unique, original, self._disk_bytes())

    # --- 復元 ---------------------------------------------------------------

    def restore(self, snapshot_id: int, dest: str | os.PathLike[str] | None = None) -> Path:
        """履歴を取り出す。既存のファイルは上書きしない。内容は記録と照合する。"""
        snap = self.get(snapshot_id)
        if snap is None:
            raise KeyError(f"履歴 {snapshot_id} は見つかりません")
        if dest is None:
            original = Path(snap.source_path)
            dest = original.with_name(f"{original.stem}.restored-{snap.id}{original.suffix}")
        dest = Path(dest)
        if dest.exists():
            raise FileExistsError(f"すでに存在します: {dest}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        digest = hashlib.sha256()
        try:
            with self.open_object(snap.sha256) as src, open(tmp, "wb") as out:
                while chunk := src.read(_CHUNK):
                    digest.update(chunk)
                    out.write(chunk)
            if digest.hexdigest() != snap.sha256:
                raise StoreCorruptionError(f"履歴 {snapshot_id} の内容が壊れています")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()
        return dest

    # --- 整理 ---------------------------------------------------------------

    def prune(self, path: str | os.PathLike[str], keep: int) -> int:
        """新しい順に keep 件だけ残し、古い履歴を削除する。削除した件数を返す。

        メモを付けた履歴は、古くなっても削除しない（残す件数には数えない）。
        """
        if keep < 1:
            raise ValueError("keep は 1 以上にしてください")
        key = source_key(path)
        with self._lock:
            rows = self._db.execute(
                "SELECT id, note FROM snapshots WHERE source_key = ?"
                " ORDER BY taken_at DESC, id DESC",
                (key,),
            ).fetchall()
            stale = [snap_id for snap_id, note in rows[keep:] if not note]
            return self._delete_locked(stale).count

    def set_note(self, snapshot_id: int, note: str | None) -> bool:
        """メモを付ける・書き換える。空にすると消える。履歴がなければ False。"""
        note = (note or "").strip() or None
        with self._lock:
            cur = self._db.execute(
                "UPDATE snapshots SET note = ? WHERE id = ?", (note, snapshot_id)
            )
            self._db.commit()
            return cur.rowcount > 0

    def delete_snapshots(self, snapshot_ids: Iterable[int]) -> DeleteResult:
        """指定した履歴を削除する。元の .blend ファイルには影響しない。"""
        with self._lock:
            return self._delete_locked(list(snapshot_ids))

    def delete_file_history(self, path: str | os.PathLike[str]) -> DeleteResult:
        """1つのファイルの履歴を、すべて削除する。"""
        key = source_key(path)
        with self._lock:
            ids = [
                row[0]
                for row in self._db.execute("SELECT id FROM snapshots WHERE source_key = ?", (key,))
            ]
            return self._delete_locked(ids)

    def _delete_locked(self, snapshot_ids: list[int]) -> DeleteResult:
        if not snapshot_ids:
            return DeleteResult(0, 0)
        marks = ",".join("?" * len(snapshot_ids))
        rows = self._db.execute(
            f"SELECT id, sha256 FROM snapshots WHERE id IN ({marks})", snapshot_ids
        ).fetchall()
        for snap_id, _ in rows:
            self._db.execute("DELETE FROM snapshots WHERE id = ?", (snap_id,))
        self._db.commit()
        freed = 0
        for sha in {sha for _, sha in rows}:
            still_used = self._db.execute(
                "SELECT 1 FROM snapshots WHERE sha256 = ? LIMIT 1", (sha,)
            ).fetchone()
            if still_used is not None:
                continue
            if self._is_chunked_locked(sha):
                freed += self._release_chunked_locked(sha)
            for p in self._paths(sha):
                if p.exists():
                    freed += p.stat().st_size
                    p.unlink()
        return DeleteResult(len(rows), freed)

    def _release_chunked_locked(self, sha: str) -> int:
        """チャンクに分けた履歴を消す。ほかの履歴が使っていないチャンクだけを削除する。"""
        counts = self._db.execute(
            "SELECT chunk_sha, COUNT(*) FROM object_chunks WHERE object_sha = ? GROUP BY chunk_sha",
            (sha,),
        ).fetchall()
        freed = 0
        for chunk_sha, count in counts:
            self._db.execute(
                "UPDATE chunks SET refs = refs - ? WHERE sha256 = ?", (count, chunk_sha)
            )
            row = self._db.execute(
                "SELECT refs, stored FROM chunks WHERE sha256 = ?", (chunk_sha,)
            ).fetchone()
            if row is not None and row[0] <= 0:
                self._db.execute("DELETE FROM chunks WHERE sha256 = ?", (chunk_sha,))
                path = self._chunk_path(chunk_sha)
                if path.exists():
                    freed += path.stat().st_size
                    path.unlink()
        self._db.execute("DELETE FROM object_chunks WHERE object_sha = ?", (sha,))
        self._db.execute("DELETE FROM chunked_objects WHERE sha256 = ?", (sha,))
        self._db.commit()
        return freed

    def compact(self, progress: ProgressCallback | None = None) -> int:
        """履歴を圧縮し、大きなファイルは、版どうしで共通する部分を1つにまとめる。

        減ったバイト数を返す。使われていないチャンクのファイルも、ここで片づける。
        """
        before = self._disk_bytes()
        targets = [p for p in self._object_files() if self._worth_compacting(p)]
        total = sum(p.stat().st_size for p in targets)
        done = 0
        for path in targets:
            size = path.stat().st_size
            sha = path.name.split(".")[0]
            self._compact_one(path, sha)
            done += size
            if progress is not None:
                progress(done, total, path.name)
        self._collect_orphan_chunks()
        return max(0, before - self._disk_bytes())

    def _disk_bytes(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(SUM(stored), 0) FROM chunks").fetchone()
        return sum(p.stat().st_size for p in self._object_files()) + row[0]

    def _worth_compacting(self, path: Path) -> bool:
        sha = path.name.split(".")[0]
        if path.name.endswith(_ZSTD_SUFFIX):
            # すでに圧縮してある。大きければ、チャンクに分けて共通部分をまとめる。
            return self.chunk_large_files and self._original_size(path, sha) >= self.chunk_min_bytes
        with open(path, "rb") as f:
            return _is_plain_blend(f.read(len(_PLAIN_MAGIC)))

    def _compact_one(self, path: Path, sha: str) -> None:
        """圧縮は、設定にかかわらず行う。チャンクへの変換は、設定がオンのときだけ行う。"""
        size = self._original_size(path, sha)
        if self.chunk_large_files and size >= self.chunk_min_bytes:
            if self._convert_to_chunks(sha, size):
                return
        if path.name.endswith(_RAW_SUFFIX):
            self._compress_object(path, sha)

    def _original_size(self, path: Path, sha: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(size) FROM snapshots WHERE sha256 = ?", (sha,)
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else path.stat().st_size

    def _convert_to_chunks(self, sha: str, original_size: int) -> bool:
        """丸ごと保存してある履歴を、チャンクに分けて保存し直す。できたら True。"""
        digest = hashlib.sha256()
        order: list[str] = []
        new_chunks: dict[str, tuple[int, int]] = {}
        cctx = zstandard.ZstdCompressor(level=_ZSTD_LEVEL)
        try:
            with self.open_object(sha) as stream:
                head = stream.read(64)  # ヘッダーと最初のブロックを見分けられる長さ
                if not _is_plain_blend(head):
                    return False  # Blender が圧縮したファイルは、分けても共通部分が出ない
                buffered = io.BufferedReader(_Prefixed(head, stream), buffer_size=_CHUNK)
                if not chunking.can_chunk(io.BytesIO(head)):
                    return False
                for data in chunking.iter_chunks(buffered, digest):
                    chunk_sha = hashlib.sha256(data).hexdigest()
                    order.append(chunk_sha)
                    if chunk_sha in new_chunks or self._chunk_known(chunk_sha):
                        continue
                    new_chunks[chunk_sha] = (len(data), self._write_chunk(cctx, chunk_sha, data))
            if digest.hexdigest() != sha:
                raise StoreCorruptionError(f"履歴の内容が壊れています: {sha[:12]}")
            with self._lock:
                if self._is_chunked_locked(sha):
                    self._discard_unreferenced_locked(new_chunks)
                else:
                    self._register_chunked_locked(sha, original_size, order, new_chunks)
                    self._db.commit()
        except BaseException:
            with self._lock:
                self._discard_unreferenced_locked(new_chunks)
            raise
        for p in self._paths(sha):
            p.unlink(missing_ok=True)
        return True

    def _collect_orphan_chunks(self) -> None:
        """追加の途中で終了したときなどに残った、使われていないチャンクを消す。"""
        if not self.chunks_dir.is_dir():
            return
        with self._lock:
            known = {row[0] for row in self._db.execute("SELECT sha256 FROM chunks")}
        for path in self._chunk_files():
            if path.name[: -len(_CHUNK_SUFFIX)] not in known:
                path.unlink(missing_ok=True)
        for tmp in self.chunks_dir.rglob("*.tmp"):
            tmp.unlink(missing_ok=True)

    def _compress_object(self, raw_path: Path, sha: str) -> int:
        """1つの履歴を圧縮して置き換える。圧縮後の大きさを返す。"""
        zst_path = raw_path.with_name(f"{sha}{_ZSTD_SUFFIX}")
        tmp = raw_path.with_name(f"{sha}.compact.tmp")
        digest = hashlib.sha256()
        try:
            cctx = zstandard.ZstdCompressor(level=_ZSTD_LEVEL, threads=-1)
            with open(raw_path, "rb") as f, open(tmp, "wb") as out:
                with cctx.stream_writer(out, closefd=False) as writer:
                    while chunk := f.read(_CHUNK):
                        digest.update(chunk)
                        writer.write(chunk)
            if digest.hexdigest() != sha:
                raise StoreCorruptionError(f"履歴の内容が壊れています: {sha[:12]}")
            os.replace(tmp, zst_path)  # 先に圧縮版を置いてから、元を消す
            raw_path.unlink()
            return zst_path.stat().st_size
        finally:
            tmp.unlink(missing_ok=True)

    # --- 保存先の移動 ----------------------------------------------------------

    def copy_to(
        self, new_dir: str | os.PathLike[str], progress: ProgressCallback | None = None
    ) -> MigrationResult:
        """履歴をすべて別のフォルダへコピーする。元の履歴は消さない。

        移動先にすでに BlendKeep の履歴（index.db）がある場合は、コピーせずにそれを使う。
        progress の中で MigrationCancelled を送出すると、途中で中止できる。
        """
        old = self.root.resolve()
        new = Path(new_dir).resolve()
        if new == old or old in new.parents or new in old.parents:
            raise ValueError("移動先は、いまの保存先と重なっていないフォルダにしてください")
        if (new / "index.db").exists():
            _check_store_opens(new)
            return MigrationResult(objects=0, bytes_copied=0, adopted=True)

        files = self._object_files() + self._chunk_files()
        total = sum(p.stat().st_size for p in files)
        created: list[Path] = []
        done = 0
        try:
            for src in files:
                dest = new / src.resolve().relative_to(old)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not (dest.exists() and dest.stat().st_size == src.stat().st_size):
                    tmp = dest.with_name(dest.name + ".tmp")
                    created.append(tmp)
                    shutil.copyfile(src, tmp)
                    os.replace(tmp, dest)
                    created.append(dest)
                done += src.stat().st_size
                if progress is not None:
                    progress(done, total, src.name)
            index_tmp = new / "index.db.tmp"
            created.append(index_tmp)
            with self._lock:
                target = sqlite3.connect(index_tmp)
                try:
                    self._db.backup(target)
                finally:
                    target.close()
            os.replace(index_tmp, new / "index.db")
            created.append(new / "index.db")
            self._verify_copy(new)
        except BaseException:
            for path in created:
                path.unlink(missing_ok=True)
            raise
        return MigrationResult(objects=len(files), bytes_copied=total)

    def _verify_copy(self, new: Path) -> None:
        """コピーした先に、すべての履歴の本体がそろっているかを確かめる。"""
        with self._lock:
            shas = [r[0] for r in self._db.execute("SELECT DISTINCT sha256 FROM snapshots")]
        for sha in shas:
            if self.is_chunked(sha):
                needed = [self._chunk_path(c) for c, _ in self._chunk_list(sha)]
            else:
                needed = [p for p in self._paths(sha) if p.exists()]
            if not needed:
                raise StoreCorruptionError(f"コピーした履歴が一致しません: {sha[:12]}")
            for old_file in needed:
                copied = new / old_file.resolve().relative_to(self.root.resolve())
                if not copied.exists() or copied.stat().st_size != old_file.stat().st_size:
                    raise StoreCorruptionError(f"コピーした履歴が一致しません: {sha[:12]}")

    # --- 内部 ---------------------------------------------------------------

    def _chunk_files(self) -> list[Path]:
        if not self.chunks_dir.is_dir():
            return []
        return sorted(
            p for p in self.chunks_dir.rglob("*") if p.is_file() and p.name.endswith(_CHUNK_SUFFIX)
        )

    def _object_files(self) -> list[Path]:
        return sorted(
            p
            for p in self.objects.rglob("*")
            if p.is_file() and p.name.endswith((_RAW_SUFFIX, _ZSTD_SUFFIX))
        )

    def _latest_locked(self, key: str) -> Snapshot | None:
        row = self._db.execute(
            "SELECT id, source_path, taken_at, size, sha256, note FROM snapshots"
            " WHERE source_key = ? ORDER BY taken_at DESC, id DESC LIMIT 1",
            (key,),
        ).fetchone()
        return Snapshot(*row) if row else None

    def _get_locked(self, snapshot_id: int | None) -> Snapshot | None:
        row = self._db.execute(
            "SELECT id, source_path, taken_at, size, sha256, note FROM snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
        return Snapshot(*row) if row else None


def _check_store_opens(directory: Path) -> None:
    conn = sqlite3.connect(directory / "index.db")
    try:
        conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()
    except sqlite3.DatabaseError as error:
        raise StoreCorruptionError(f"移動先の履歴を開けません: {directory}") from error
    finally:
        conn.close()


def remove_store_files(directory: str | os.PathLike[str]) -> int:
    """BlendKeep が作ったファイルだけを削除する。ほかのファイルには触れない。削除した容量を返す。"""
    root = Path(directory)
    freed = 0
    for name in ("objects", "chunks"):
        folder = root / name
        if folder.is_dir():
            freed += sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
            shutil.rmtree(folder)
    for pattern in ("index.db", "index.db-journal", "incoming-*.tmp"):
        for path in root.glob(pattern):
            freed += path.stat().st_size
            path.unlink()
    try:
        root.rmdir()  # 空になっていたら、フォルダも消す
    except OSError:
        pass
    return freed
