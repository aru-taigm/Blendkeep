"""Blender ファイル（.blend）の判定と、埋め込みサムネイルの取り出し。

対応する形式:

* 非圧縮、gzip（Blender 2.x）、zstd（Blender 3.0 以降の「圧縮」保存）
* ヘッダー: Blender 5.x の新形式（``BLENDER17-01v0502``）と、従来形式
  （``BLENDER-v300`` / ``BLENDER_v280`` など）

新形式は Blender 5.2.2 が実際に保存したファイルで確認した。従来形式は仕様どおりに
組み立てた疑似データでテストしている。
"""

from __future__ import annotations

import gzip
import io
import os
import struct
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO

import zstandard

_MAGIC_PLAIN = b"BLENDER"
_MAGIC_GZIP = b"\x1f\x8b"
_MAGIC_ZSTD = b"\x28\xb5\x2f\xfd"

# サムネイルは GLOB ブロックより前にある。念のため読むブロック数に上限を設ける。
_MAX_BLOCKS = 64
_MAX_THUMBNAIL_BYTES = 16 * 1024 * 1024


def looks_like_blend(path: str | os.PathLike[str]) -> bool:
    """先頭のバイト列が、Blender のファイル（非圧縮・gzip・zstd）かどうかを調べる。"""
    try:
        with open(path, "rb") as f:
            head = f.read(7)
    except OSError:
        return False
    return head.startswith((_MAGIC_PLAIN, _MAGIC_GZIP, _MAGIC_ZSTD))


@dataclass(frozen=True)
class Thumbnail:
    width: int
    height: int
    rgba: bytes
    """上の行から順に並んだ RGBA（1画素 4 バイト）。"""

    def to_png(self) -> bytes:
        """標準ライブラリだけで PNG に変換する。"""
        stride = self.width * 4
        rows = b"".join(
            b"\x00" + self.rgba[y * stride : (y + 1) * stride] for y in range(self.height)
        )

        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 6, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b"")
        )


class _RawAdapter(io.RawIOBase):
    """read() しかないストリームを、peek できる BufferedReader にするための部品。"""

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[no-untyped-def]
        data = self._stream.read(len(buffer))
        buffer[: len(data)] = data
        return len(data)


@contextmanager
def _open_stream(source: str | os.PathLike[str] | BinaryIO) -> Iterator[BinaryIO]:
    """パス、または読み取りだけできるストリームを開き、圧縮されていれば展開して返す。"""
    if hasattr(source, "read"):
        buffered = io.BufferedReader(_RawAdapter(source))  # type: ignore[arg-type]
        yield from _decompressed(buffered)
    else:
        with open(source, "rb") as raw:  # type: ignore[arg-type]
            yield from _decompressed(raw)


def _decompressed(raw: BinaryIO) -> Iterator[BinaryIO]:
    head = raw.peek(4)[:4] if hasattr(raw, "peek") else _peek_seekable(raw)
    if head.startswith(_MAGIC_ZSTD):
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        try:
            yield reader  # type: ignore[misc]
        finally:
            reader.close()
    elif head.startswith(_MAGIC_GZIP):
        with gzip.GzipFile(fileobj=raw) as gz:
            yield gz  # type: ignore[misc]
    else:
        yield raw


def _peek_seekable(raw: BinaryIO) -> bytes:
    head = raw.read(4)
    raw.seek(0)
    return head


def read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        part = stream.read(remaining)
        if not part:
            raise EOFError
        chunks.append(part)
        remaining -= len(part)
    return b"".join(chunks)


def _skip(stream: BinaryIO, size: int) -> None:
    remaining = size
    while remaining > 0:
        part = stream.read(min(remaining, 1 << 20))
        if not part:
            raise EOFError
        remaining -= len(part)


@dataclass(frozen=True)
class Layout:
    """ヘッダーと、各ブロックの先頭（BHead）の構造。"""

    header_size: int
    bhead_size: int
    len_offset: int  # BHead の中で「データ長」がある位置
    len_format: str  # データ長の struct 形式（バイトオーダー込み）
    int_order: str  # サムネイルの幅・高さのバイトオーダー


def parse_header(stream: BinaryIO) -> Layout | None:
    head = read_exact(stream, 12)
    if head[:7] != _MAGIC_PLAIN:
        return None
    if head[7:9].isdigit():
        # 新形式（Blender 5.x）: BLENDER{ヘッダー長 2桁}-{形式 2桁}v{版 4桁}
        header_size = int(head[7:9])
        if header_size < 12 or head[9:10] != b"-":
            return None
        full = head + read_exact(stream, header_size - 12)
        if full[10:12] != b"01":
            return None  # 未知の形式は読まない
        # BHead: code(4) sdna(i32) old(u64) len(i64) nr(i64)
        return Layout(header_size, 32, 16, "<q", "<")
    # 従来形式: BLENDER{_ か -}{v か V}{版 3桁}
    pointer_char, endian_char = head[7:8], head[8:9]
    if pointer_char not in (b"_", b"-") or endian_char not in (b"v", b"V"):
        return None
    pointer_size = 4 if pointer_char == b"_" else 8
    order = "<" if endian_char == b"v" else ">"
    # BHead: code(4) len(i32) old(ptr) sdna(i32) nr(i32)
    return Layout(12, 4 + 4 + pointer_size + 4 + 4, 4, order + "i", order)


def read_thumbnail(source: str | os.PathLike[str] | BinaryIO) -> Thumbnail | None:
    """.blend に埋め込まれたサムネイルを取り出す。無い・読めないときは None。

    source は、ファイルのパスか、読み取りができるストリーム（圧縮は自動で判別して展開する）。
    """
    try:
        with _open_stream(source) as stream:
            layout = parse_header(stream)
            if layout is None:
                return None
            for _ in range(_MAX_BLOCKS):
                bhead = read_exact(stream, layout.bhead_size)
                code = bhead[:4]
                (length,) = struct.unpack_from(layout.len_format, bhead, layout.len_offset)
                if code in (b"GLOB", b"ENDB"):
                    return None
                if code != b"TEST":
                    _skip(stream, length)
                    continue
                return _decode_thumbnail(stream, length, layout)
    except (OSError, EOFError, zstandard.ZstdError, struct.error):
        return None
    return None


def _decode_thumbnail(stream: BinaryIO, length: int, layout: Layout) -> Thumbnail | None:
    if not 8 <= length <= _MAX_THUMBNAIL_BYTES:
        return None
    data = read_exact(stream, length)
    width, height = struct.unpack_from(layout.int_order + "ii", data, 0)
    if width <= 0 or height <= 0 or length < 8 + width * height * 4:
        return None
    pixels = data[8 : 8 + width * height * 4]
    # Blender は下の行から順に保存している。上から順に並べ替える。
    stride = width * 4
    rows = [pixels[y * stride : (y + 1) * stride] for y in range(height)]
    return Thumbnail(width, height, b"".join(reversed(rows)))


__all__ = ["Thumbnail", "looks_like_blend", "read_thumbnail"]
