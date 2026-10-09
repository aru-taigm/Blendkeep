"""大きな .blend を、版が変わっても同じになりやすい単位（チャンク）に分ける。

.blend は「ブロック」が並んだ形式で、編集すると、変わったブロックだけが書き換わる。
ところが、途中にブロックが増えると、それより後ろのバイト位置が全部ずれる。
そこで、チャンクの区切りは「ファイルの先頭からの位置」ではなく、ブロックを基準にして決める。

* 大きなブロック（既定は 96 KiB 以上）: そのブロックの先頭から 64 KiB ずつに分ける。
  ブロックの位置がずれても、中身が同じなら、同じチャンクになる。
* 小さなブロック: 連続するものをまとめ、ブロックの内容から決めた目印のところで区切る。

こうして分けたチャンクを、内容のハッシュで保存すれば、版どうしで共通する部分は1回しか保存されない。
Blender 5.2.2 で実測した結果は、README を参照。
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from collections.abc import Callable, Iterator
from typing import BinaryIO

from .blendfile import Layout, parse_header

PIECE_SIZE = 64 * 1024  # 大きなブロックを分ける単位
BIG_BLOCK = 96 * 1024  # これ以上のブロックは、単独で分ける
GROUP_MIN = 8 * 1024  # 小さなブロックをまとめるときの、最小の大きさ
GROUP_MAX = 128 * 1024  # 同、最大の大きさ
GROUP_MASK = 0x3F  # 区切りの目印（約 1/64 のブロックが目印になる）
_TRAILER_PIECE = 256 * 1024


def can_chunk(stream: BinaryIO) -> bool:
    """先頭のヘッダーを読んで、チャンクに分けられる形式か確かめる。"""
    try:
        return parse_header(stream) is not None
    except (EOFError, OSError, struct.error):
        return False


def iter_chunks(stream: BinaryIO, digest: hashlib._Hash | None = None) -> Iterator[bytes]:
    """ファイルを先頭から読み、チャンクを順に返す。

    読んだバイトはすべて、どれかのチャンクに入る（つなげると元のファイルになる）。
    digest を渡すと、ファイル全体のハッシュを、読みながら更新する。
    """

    def take(count: int) -> bytes:
        data = stream.read(count)
        while len(data) < count:
            more = stream.read(count - len(data))
            if not more:
                break
            data += more
        if digest is not None:
            digest.update(data)
        return data

    group = bytearray()
    header = _read_layout(take, group)
    if header is None:
        # ヘッダーが読めない: 通常は呼ばれないが、安全のため固定サイズで分ける。
        yield from _fixed_pieces(bytes(group), take)
        return
    layout = header

    while True:
        bhead = take(layout.bhead_size)
        if len(bhead) < layout.bhead_size:
            group += bhead
            break
        (length,) = struct.unpack_from(layout.len_format, bhead, layout.len_offset)
        if length < 0:
            group += bhead
            break
        code = bhead[:4]
        if length >= BIG_BLOCK:
            if group:
                yield bytes(group)
                group.clear()
            yield from _big_block(bhead, length, take)
        else:
            body = take(length)
            group += bhead
            group += body
            if len(body) < length:
                break  # ファイルが途中で切れている
            if len(group) >= GROUP_MIN and (len(group) >= GROUP_MAX or _is_boundary(body, length)):
                yield bytes(group)
                group.clear()
        if code == b"ENDB":
            break

    # ENDB のあとに続くバイト（通常はない）
    while True:
        rest = take(_TRAILER_PIECE)
        if not rest:
            break
        group += rest
        while len(group) >= _TRAILER_PIECE:
            yield bytes(group[:_TRAILER_PIECE])
            del group[:_TRAILER_PIECE]
    if group:
        yield bytes(group)


def _read_layout(take: Callable[[int], bytes], group: bytearray) -> Layout | None:
    """ヘッダーを読む。読んだバイトは group に入れる（先頭のチャンクの一部になる）。"""

    class _Tap:
        def read(self, count: int) -> bytes:
            data = take(count)
            group.extend(data)
            return data

    try:
        return parse_header(_Tap())  # type: ignore[arg-type]
    except (EOFError, struct.error):
        return None


def _is_boundary(body: bytes, length: int) -> bool:
    """小さなブロックのあとで区切るか。ブロックの内容と大きさだけで決める。"""
    value = zlib.crc32(body[:64]) ^ ((length * 2654435761) & 0xFFFFFFFF)
    return (value & GROUP_MASK) == 0


def _big_block(bhead: bytes, length: int, take: Callable[[int], bytes]) -> Iterator[bytes]:
    """大きなブロックを、ブロックの先頭を基準に PIECE_SIZE ずつに分ける。"""
    piece = bytearray(bhead)
    remaining = length
    while remaining > 0:
        count = min(PIECE_SIZE - len(piece), remaining)
        data = take(count)
        piece += data
        remaining -= len(data)
        if len(data) < count:
            break  # ファイルが途中で切れている
        if len(piece) >= PIECE_SIZE:
            yield bytes(piece)
            piece = bytearray()
    if piece:
        yield bytes(piece)


def _fixed_pieces(first: bytes, take: Callable[[int], bytes]) -> Iterator[bytes]:
    buffer = bytearray(first)
    while True:
        data = take(PIECE_SIZE)
        if not data:
            break
        buffer += data
        while len(buffer) >= PIECE_SIZE:
            yield bytes(buffer[:PIECE_SIZE])
            del buffer[:PIECE_SIZE]
    if buffer:
        yield bytes(buffer)
