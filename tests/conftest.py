from __future__ import annotations

from pathlib import Path

import pytest

FAKE_HEADER = b"BLENDER-v300"


def write_blend(path: Path, payload: bytes = b"") -> Path:
    """テスト用の疑似 .blend（先頭だけ本物らしい）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FAKE_HEADER + payload)
    return path


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "store"


@pytest.fixture(autouse=True)
def _isolated_app_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """テストが、実際のイベントフォルダや Blender の設定フォルダを触らないようにする。"""
    monkeypatch.setenv("BLENDKEEP_EVENTS", str(tmp_path / "_events"))
    monkeypatch.setenv("BLENDKEEP_BLENDER_CONFIG", str(tmp_path / "_blender"))
