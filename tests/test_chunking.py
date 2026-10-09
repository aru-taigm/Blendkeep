from __future__ import annotations

import hashlib
import io
import random
import struct

import pytest

from blendkeep import chunking
from blendkeep.chunking import iter_chunks


def new_header() -> bytes:
    return b"BLENDER17-01v0502"


def new_block(code: bytes, data: bytes) -> bytes:
    """Blender 5.x 形式のブロック（先頭 32 バイト + 中身）。"""
    return code + struct.pack("<iQqq", 0, 0x10, len(data), 1) + data


def legacy_block(code: bytes, data: bytes) -> bytes:
    return code + struct.pack("<iQii", len(data), 0x10, 0, 1) + data


def make_blend(blocks: list[tuple[bytes, bytes]], legacy: bool = False) -> bytes:
    header = b"BLENDER-v300" if legacy else new_header()
    block = legacy_block if legacy else new_block
    body = b"".join(block(code, data) for code, data in blocks)
    return header + body + block(b"ENDB", b"")


def rand(seed: int, size: int) -> bytes:
    return random.Random(seed).randbytes(size)


def chunks_of(data: bytes) -> tuple[list[bytes], str]:
    digest = hashlib.sha256()
    out = list(iter_chunks(io.BytesIO(data), digest))
    return out, digest.hexdigest()


def new_fraction(old: list[bytes], new: list[bytes]) -> float:
    known = {hashlib.sha256(c).digest() for c in old}
    fresh = sum(len(c) for c in new if hashlib.sha256(c).digest() not in known)
    return fresh / sum(len(c) for c in new)


def scene(extra_small_blocks: int = 0) -> list[tuple[bytes, bytes]]:
    blocks: list[tuple[bytes, bytes]] = [(b"REND", b"\0" * 8), (b"TEST", rand(1, 65544))]
    blocks += [(b"DATA", rand(10 + i, 700 + i)) for i in range(300)]
    blocks.append((b"DATA", rand(2, 600_000)))  # 大きなメッシュ
    blocks += [(b"DATA", rand(500 + i, 300 + i)) for i in range(200)]
    blocks.append((b"DATA", rand(3, 400_000)))  # もう1つの大きなブロック
    blocks += [(b"DATA", rand(900 + i, 250)) for i in range(extra_small_blocks)]
    blocks.append((b"GLOB", rand(4, 1200)))
    return blocks


def test_chunks_concatenate_to_the_original_file() -> None:
    data = make_blend(scene())
    out, sha = chunks_of(data)
    assert b"".join(out) == data
    assert sha == hashlib.sha256(data).hexdigest()
    assert len(out) > 10


def test_chunking_is_deterministic() -> None:
    data = make_blend(scene())
    assert chunks_of(data)[0] == chunks_of(data)[0]


def test_unchanged_file_has_no_new_chunks() -> None:
    data = make_blend(scene())
    a, _ = chunks_of(data)
    assert new_fraction(a, chunks_of(data)[0]) == 0


def test_editing_one_big_block_in_place_changes_only_that_part() -> None:
    blocks = scene()
    big = bytearray(blocks[302][1])  # 600,000 バイトのブロック
    big[300_000] ^= 0xFF
    edited = list(blocks)
    edited[303] = (b"DATA", bytes(big))
    a, _ = chunks_of(make_blend(blocks))
    b, _ = chunks_of(make_blend(edited))
    assert 0 < new_fraction(a, b) < 0.1


def test_inserting_a_block_does_not_shift_the_rest() -> None:
    """途中にブロックが増えても、あとのバイト位置がずれた大きなブロックは、同じチャンクになる。"""
    blocks = scene()
    inserted = list(blocks)
    inserted.insert(100, (b"DATA", rand(777, 1234)))
    a, _ = chunks_of(make_blend(blocks))
    b, _ = chunks_of(make_blend(inserted))
    assert new_fraction(a, b) < 0.1
    # 先頭からの固定サイズ分割なら、ほぼ全部が変わってしまう
    fixed_a = [make_blend(blocks)[i : i + 65536] for i in range(0, len(make_blend(blocks)), 65536)]
    data_b = make_blend(inserted)
    fixed_b = [data_b[i : i + 65536] for i in range(0, len(data_b), 65536)]
    assert new_fraction(fixed_a, fixed_b) > 0.8


def test_adding_small_blocks_at_the_end_only_changes_the_tail() -> None:
    a, _ = chunks_of(make_blend(scene()))
    b, _ = chunks_of(make_blend(scene(extra_small_blocks=40)))
    assert new_fraction(a, b) < 0.1


def test_legacy_header_format() -> None:
    data = make_blend(scene(), legacy=True)
    out, sha = chunks_of(data)
    assert b"".join(out) == data and sha == hashlib.sha256(data).hexdigest()
    assert len(out) > 10


def test_truncated_file_still_round_trips() -> None:
    data = make_blend(scene())
    for cut in (5, 20, 100, 5_000, 300_000, len(data) - 10):
        out, sha = chunks_of(data[:cut])
        assert b"".join(out) == data[:cut]
        assert sha == hashlib.sha256(data[:cut]).hexdigest()


def test_trailing_bytes_after_endb_are_kept() -> None:
    data = make_blend(scene()) + rand(9, 700_000)
    out, _ = chunks_of(data)
    assert b"".join(out) == data


def test_corrupt_block_length_does_not_crash() -> None:
    data = bytearray(make_blend(scene()))
    data[17 + 16 : 17 + 24] = struct.pack("<q", 1 << 50)  # 先頭ブロックの長さを異常にする
    out, _ = chunks_of(bytes(data))
    assert b"".join(out) == bytes(data)


def test_negative_length_does_not_crash() -> None:
    data = bytearray(make_blend(scene()))
    data[17 + 16 : 17 + 24] = struct.pack("<q", -5)
    out, _ = chunks_of(bytes(data))
    assert b"".join(out) == bytes(data)


def test_non_blender_data_falls_back_to_fixed_pieces() -> None:
    data = rand(5, 300_000)
    out, _ = chunks_of(data)
    assert b"".join(out) == data
    assert all(len(c) <= chunking.PIECE_SIZE for c in out)


@pytest.mark.parametrize("data", [b"", b"BLENDER", b"BLENDER17-01v0502"])
def test_tiny_inputs(data: bytes) -> None:
    out, sha = chunks_of(data)
    assert b"".join(out) == data
    assert sha == hashlib.sha256(data).hexdigest()


def test_can_chunk() -> None:
    assert chunking.can_chunk(io.BytesIO(make_blend(scene())))
    assert chunking.can_chunk(io.BytesIO(make_blend(scene(), legacy=True)))
    assert not chunking.can_chunk(io.BytesIO(b"not a blend file at all"))
    assert not chunking.can_chunk(io.BytesIO(b""))
