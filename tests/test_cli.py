from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from blendkeep.cli import main
from blendkeep.config import Config

FIXTURE = Path(__file__).parent / "data" / "blender-5.2.2-compressed.blend"


@pytest.fixture
def env(tmp_path: Path) -> tuple[Path, Path]:
    config_path = tmp_path / "config.json"
    Config(store_dir=str(tmp_path / "store")).save(config_path)
    project = tmp_path / "proj"
    project.mkdir()
    shutil.copy(FIXTURE, project / "scene.blend")
    return config_path, project / "scene.blend"


def test_snapshot_list_restore_thumbnail(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config, blend = env
    assert main(["--config", str(config), "snapshot", str(blend), "--note", "初回"]) == 0
    assert main(["--config", str(config), "snapshot", str(blend)]) == 0
    assert "同じ内容" in capsys.readouterr().out

    assert main(["--config", str(config), "list", str(blend)]) == 0
    listing = capsys.readouterr().out
    assert "scene.blend" in listing and "初回" in listing

    restored = tmp_path / "back.blend"
    assert main(["--config", str(config), "restore", "1", "--to", str(restored)]) == 0
    assert restored.read_bytes() == blend.read_bytes()
    assert main(["--config", str(config), "restore", "1", "--to", str(restored)]) == 1

    png = tmp_path / "thumb.png"
    assert main(["--config", str(config), "thumbnail", "1", "--out", str(png)]) == 0
    assert png.read_bytes().startswith(b"\x89PNG")


def test_unknown_ids(env: tuple[Path, Path], tmp_path: Path) -> None:
    config, _ = env
    assert main(["--config", str(config), "restore", "99"]) == 1
    assert main(["--config", str(config), "thumbnail", "99", "--out", str(tmp_path / "x.png")]) == 1


def test_add_dir_and_config(env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    config, blend = env
    assert main(["--config", str(config), "add-dir", str(blend.parent)]) == 0
    capsys.readouterr()
    assert main(["--config", str(config), "config"]) == 0
    assert str(blend.parent.resolve()) in capsys.readouterr().out
    assert Config.load(config).watch_dirs == [str(blend.parent.resolve())]


def test_watch_without_dirs_exits_with_error(env: tuple[Path, Path]) -> None:
    config, _ = env
    assert main(["--config", str(config), "watch"]) == 2


def test_compact_and_stats(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config, blend = env
    # Blender が圧縮していない形のファイルを、圧縮なしの設定で履歴にする。
    import zstandard

    with FIXTURE.open("rb") as f:
        plain = zstandard.ZstdDecompressor().stream_reader(f).read()
    blend.write_bytes(plain)
    cfg = Config.load(config)
    cfg.compress_history = False
    cfg.save(config)
    assert main(["--config", str(config), "snapshot", str(blend)]) == 0
    capsys.readouterr()

    assert main(["--config", str(config), "compact"]) == 0
    assert "節約" in capsys.readouterr().out
    assert main(["--config", str(config), "config"]) == 0
    out = capsys.readouterr().out
    assert "履歴: 1 件" in out and "圧縮して保存: しない" in out
    restored = tmp_path / "r.blend"
    assert main(["--config", str(config), "restore", "1", "--to", str(restored)]) == 0
    assert restored.read_bytes() == plain


def test_move_store(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config, blend = env
    assert main(["--config", str(config), "snapshot", str(blend)]) == 0
    old_dir = Path(Config.load(config).store_dir)
    new_dir = tmp_path / "moved"
    assert main(["--config", str(config), "move-store", str(new_dir)]) == 0
    assert Config.load(config).store_dir == str(new_dir.resolve())
    assert old_dir.exists()  # 既定では元を消さない
    capsys.readouterr()
    assert main(["--config", str(config), "list"]) == 0
    assert "scene.blend" in capsys.readouterr().out

    again = tmp_path / "moved2"
    assert main(["--config", str(config), "move-store", str(again), "--delete-old"]) == 0
    assert not new_dir.exists()
    assert main(["--config", str(config), "move-store", str(again)]) == 0  # 同じ場所
    assert "すでに" in capsys.readouterr().out


def test_move_store_rejects_nested(env: tuple[Path, Path]) -> None:
    config, blend = env
    assert main(["--config", str(config), "snapshot", str(blend)]) == 0
    inside = Path(Config.load(config).store_dir) / "inside"
    assert main(["--config", str(config), "move-store", str(inside)]) == 1


def test_note_and_delete(
    env: tuple[Path, Path], capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config, blend = env
    assert main(["--config", str(config), "snapshot", str(blend)]) == 0
    capsys.readouterr()

    assert main(["--config", str(config), "note", "1", "納品版"]) == 0
    assert main(["--config", str(config), "list"]) == 0
    assert "納品版" in capsys.readouterr().out
    assert main(["--config", str(config), "note", "1", "--clear"]) == 0
    assert main(["--config", str(config), "list"]) == 0
    assert "#" not in capsys.readouterr().out
    assert main(["--config", str(config), "note", "99", "なし"]) == 1

    assert main(["--config", str(config), "delete", "1"]) == 1  # --yes がないと消さない
    assert main(["--config", str(config), "list"]) == 0
    assert "scene.blend" in capsys.readouterr().out
    assert main(["--config", str(config), "delete", "1", "--yes"]) == 0
    assert main(["--config", str(config), "delete", "1", "--yes"]) == 1  # もう無い
    assert main(["--config", str(config), "list"]) == 0
    assert "履歴はありません" in capsys.readouterr().out
