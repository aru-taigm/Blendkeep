from __future__ import annotations

from pathlib import Path

from blendkeep.config import Config


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "config.json"
    cfg = Config(watch_dirs=["D:/Blender"], max_versions_per_file=7)
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded.watch_dirs == ["D:/Blender"]
    assert loaded.max_versions_per_file == 7


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert Config.load(tmp_path / "none.json").watch_dirs == []


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"watch_dirs": ["x"], "future_option": 1}', encoding="utf-8")
    assert Config.load(path).watch_dirs == ["x"]
