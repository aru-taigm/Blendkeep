"""Blender の起動スクリプトの置き場所を探して、導入・削除する。

Blender のユーザー設定フォルダ（バージョンごと）の scripts/startup に置いたスクリプトは、
起動のたびに自動で実行される。アドオンの有効化は要らない。
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

SCRIPT_NAME = "blendkeep_bridge.py"
_VERSION = re.compile(r"^\d+\.\d+$")


def blender_config_root() -> Path:
    override = os.environ.get("BLENDKEEP_BLENDER_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "Blender Foundation" / "Blender"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Blender"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "blender"


@dataclass(frozen=True)
class BlenderTarget:
    version: str
    startup_dir: Path

    @property
    def script_path(self) -> Path:
        return self.startup_dir / SCRIPT_NAME

    @property
    def installed(self) -> bool:
        return self.script_path.is_file()


def _version_key(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in name.split("."))


def find_targets(root: Path | None = None) -> list[BlenderTarget]:
    """設定フォルダが見つかった Blender のバージョンを、新しい順に返す。"""
    root = root or blender_config_root()
    try:
        names = [p.name for p in root.iterdir() if p.is_dir() and _VERSION.match(p.name)]
    except OSError:
        return []
    return [
        BlenderTarget(name, root / name / "scripts" / "startup")
        for name in sorted(names, key=_version_key, reverse=True)
    ]


def bridge_source() -> str:
    return resources.files("blendkeep.blender").joinpath(SCRIPT_NAME).read_text(encoding="utf-8")


def install(targets: list[BlenderTarget] | None = None) -> list[BlenderTarget]:
    """見つかった（または指定した）すべてのバージョンに入れる。入れた先を返す。"""
    targets = find_targets() if targets is None else targets
    source = bridge_source()
    done = []
    for target in targets:
        target.startup_dir.mkdir(parents=True, exist_ok=True)
        tmp = target.script_path.with_suffix(".py.tmp")
        tmp.write_text(source, encoding="utf-8")
        os.replace(tmp, target.script_path)
        done.append(target)
    return done


def uninstall(targets: list[BlenderTarget] | None = None) -> list[BlenderTarget]:
    """入れたスクリプトだけを消す。消した先を返す。"""
    targets = find_targets() if targets is None else targets
    done = []
    for target in targets:
        if target.installed:
            target.script_path.unlink()
            done.append(target)
    return done


def is_up_to_date(target: BlenderTarget) -> bool:
    try:
        return target.script_path.read_text(encoding="utf-8") == bridge_source()
    except OSError:
        return False
