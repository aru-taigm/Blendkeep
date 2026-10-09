from __future__ import annotations

import gzip
import struct
import zlib
from pathlib import Path

import pytest
import zstandard

from blendkeep.blendfile import Thumbnail, looks_like_blend, read_thumbnail

# Blender 5.2.2 が実際に保存したファイル（zstd 圧縮、空のシーン）。
REAL_FIXTURE = Path(__file__).parent / "data" / "blender-5.2.2-compressed.blend"


def _plain_bytes_of_fixture() -> bytes:
    with REAL_FIXTURE.open("rb") as f:
        return zstandard.ZstdDecompressor().stream_reader(f).read()


# --- 疑似データの組み立て（仕様どおり） --------------------------------------


def _thumb_payload(width: int, height: int, bottom_up_rows: list[bytes], order: str) -> bytes:
    return struct.pack(order + "ii", width, height) + b"".join(bottom_up_rows)


def _new_format(payload: bytes, version: bytes = b"01") -> bytes:
    """Blender 5.x の新形式: BLENDER17-01v0502、BHead は 32 バイト。"""

    def block(code: bytes, data: bytes) -> bytes:
        return code + struct.pack("<iQqq", 0, 0x10, len(data), 1) + data

    header = b"BLENDER17-" + version + b"v0502"
    return header + block(b"REND", b"\0" * 8) + block(b"TEST", payload) + block(b"GLOB", b"\0" * 8)


def _legacy_format(payload: bytes, pointer: bytes, endian: bytes) -> bytes:
    """従来形式: BHead は code, len, old, sdna, nr。"""
    order = "<" if endian == b"v" else ">"
    ptr_fmt = "I" if pointer == b"_" else "Q"

    def block(code: bytes, data: bytes) -> bytes:
        return code + struct.pack(order + "i" + ptr_fmt + "ii", len(data), 0x10, 0, 1) + data

    header = b"BLENDER" + pointer + endian + b"300"
    return header + block(b"REND", b"\0" * 8) + block(b"TEST", payload) + block(b"GLOB", b"\0" * 8)


def _pattern(width: int = 2, height: int = 3) -> tuple[list[bytes], bytes]:
    """行ごとに色が違う画像。(下から順の行, 上から順の期待値) を返す。"""
    rows_bottom_up = [
        bytes([y * 10 + 1, y * 10 + 2, y * 10 + 3, 255]) * width for y in range(height)
    ]
    expected_top_down = b"".join(reversed(rows_bottom_up))
    return rows_bottom_up, expected_top_down


# --- 実ファイル -----------------------------------------------------------------


def test_real_blender_file_zstd() -> None:
    thumb = read_thumbnail(REAL_FIXTURE)
    assert thumb is not None
    assert (thumb.width, thumb.height) == (128, 128)
    assert len(thumb.rgba) == 128 * 128 * 4
    assert any(thumb.rgba)  # 真っ黒（空）ではない


def test_real_blender_file_plain_and_gzip(tmp_path: Path) -> None:
    plain = _plain_bytes_of_fixture()
    plain_path = tmp_path / "plain.blend"
    plain_path.write_bytes(plain)
    gz_path = tmp_path / "gz.blend"
    gz_path.write_bytes(gzip.compress(plain))
    expected = read_thumbnail(REAL_FIXTURE)
    assert read_thumbnail(plain_path) == expected
    assert read_thumbnail(gz_path) == expected


def test_png_output_is_valid() -> None:
    thumb = read_thumbnail(REAL_FIXTURE)
    assert thumb is not None
    png = thumb.to_png()
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    # チャンクを順にたどって IDAT を展開し、元の画素に戻ることを確認する。
    pos, idat = 8, b""
    while pos < len(png):
        (length,) = struct.unpack_from(">I", png, pos)
        kind = png[pos + 4 : pos + 8]
        body = png[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack_from(">I", png, pos + 8 + length)
        assert crc == zlib.crc32(kind + body)
        if kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = thumb.width * 4
    rebuilt = b"".join(
        raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)] for y in range(thumb.height)
    )
    assert rebuilt == thumb.rgba


# --- 疑似データ -----------------------------------------------------------------


def test_new_format_rows_are_flipped_to_top_down(tmp_path: Path) -> None:
    rows, expected = _pattern()
    path = tmp_path / "new.blend"
    path.write_bytes(_new_format(_thumb_payload(2, 3, rows, "<")))
    assert read_thumbnail(path) == Thumbnail(2, 3, expected)


@pytest.mark.parametrize(
    ("pointer", "endian"), [(b"-", b"v"), (b"_", b"v"), (b"-", b"V"), (b"_", b"V")]
)
def test_legacy_format(tmp_path: Path, pointer: bytes, endian: bytes) -> None:
    order = "<" if endian == b"v" else ">"
    rows, expected = _pattern()
    path = tmp_path / "old.blend"
    path.write_bytes(_legacy_format(_thumb_payload(2, 3, rows, order), pointer, endian))
    assert read_thumbnail(path) == Thumbnail(2, 3, expected)


def test_legacy_format_gzip(tmp_path: Path) -> None:
    rows, expected = _pattern()
    path = tmp_path / "old-gz.blend"
    path.write_bytes(gzip.compress(_legacy_format(_thumb_payload(2, 3, rows, "<"), b"-", b"v")))
    assert read_thumbnail(path) == Thumbnail(2, 3, expected)


# --- 読めないファイル -------------------------------------------------------------


def test_file_without_thumbnail_block(tmp_path: Path) -> None:
    data = b"BLENDER17-01v0502" + b"GLOB" + struct.pack("<iQqq", 0, 0, 0, 1)
    path = tmp_path / "none.blend"
    path.write_bytes(data)
    assert read_thumbnail(path) is None


def test_unknown_format_version_is_not_read(tmp_path: Path) -> None:
    rows, _ = _pattern()
    path = tmp_path / "future.blend"
    path.write_bytes(_new_format(_thumb_payload(2, 3, rows, "<"), version=b"99"))
    assert read_thumbnail(path) is None


def test_inconsistent_size_is_rejected(tmp_path: Path) -> None:
    payload = struct.pack("<ii", 1000, 1000) + b"\0" * 16  # 宣言した大きさに足りない
    path = tmp_path / "bad.blend"
    path.write_bytes(_new_format(payload))
    assert read_thumbnail(path) is None


@pytest.mark.parametrize("content", [b"", b"BLEN", b"hello world", b"BLENDER", b"\x28\xb5\x2f\xfd"])
def test_garbage_and_truncated(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "x.blend"
    path.write_bytes(content)
    assert read_thumbnail(path) is None


def test_truncated_real_file(tmp_path: Path) -> None:
    path = tmp_path / "cut.blend"
    path.write_bytes(_plain_bytes_of_fixture()[:100])
    assert read_thumbnail(path) is None


def test_missing_file(tmp_path: Path) -> None:
    assert read_thumbnail(tmp_path / "nope.blend") is None
    assert not looks_like_blend(tmp_path / "nope.blend")


def test_looks_like_blend(tmp_path: Path) -> None:
    assert looks_like_blend(REAL_FIXTURE)
    gz = tmp_path / "g.blend"
    gz.write_bytes(gzip.compress(b"BLENDER-v300"))
    assert looks_like_blend(gz)
    other = tmp_path / "o.blend"
    other.write_bytes(b"PK\x03\x04")
    assert not looks_like_blend(other)
