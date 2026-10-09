"""設定の読み書き。設定は JSON で保存する。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "BlendKeep"


def default_config_path() -> Path:
    override = os.environ.get("BLENDKEEP_CONFIG")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME / "config.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME.lower() / "config.json"


def default_store_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME / "store"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME.lower() / "store"


@dataclass
class Config:
    watch_dirs: list[str] = field(default_factory=list)
    store_dir: str = field(default_factory=lambda: str(default_store_dir()))
    # 最後のイベントからこの秒数、変化がなければ「保存完了」とみなす。
    debounce_seconds: float = 3.0
    # 同じファイルの履歴を作る最短間隔（秒）。短い間隔の保存は最後の状態だけ残す。
    min_interval_seconds: float = 30.0
    # 1ファイルあたりに残す履歴の最大数。
    max_versions_per_file: int = 50
    # 履歴を作ったときに、タスクトレイから知らせる。
    notify_on_snapshot: bool = True
    # 圧縮していない .blend を、圧縮して保存する（容量の節約）。
    compress_history: bool = True
    # 大きな .blend は、変わった部分だけを保存する（差分保存。容量が大きく減る）。
    chunk_large_files: bool = True
    # レンダーが終わったら、タスクトレイから知らせる（Blender 側に起動スクリプトの導入が必要）。
    notify_on_render: bool = True
    # これより短いレンダー（プレビュー確認など）は知らせない。
    render_min_seconds: float = 30.0
    # Discord の Webhook URL。空なら送らない。
    discord_webhook_url: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or default_config_path()
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path | None = None) -> Path:
        path = path or default_config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        return path
