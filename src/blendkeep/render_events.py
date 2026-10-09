"""Blender の起動スクリプトが置いたレンダーのイベントを受け取る。

受け渡しは、フォルダに置かれた JSON ファイル。ソケットや常駐の窓口は使わないので、
BlendKeep が止まっていてもイベントは残り、起動したときにまとめて処理できる。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import APP_NAME

log = logging.getLogger(__name__)

# これより古いイベントは、起動時に通知せず捨てる（昔のレンダーを今さら知らせない）。
STALE_SECONDS = 15 * 60
BROKEN_SECONDS = 60


def default_events_dir() -> Path:
    override = os.environ.get("BLENDKEEP_EVENTS")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APP_NAME / "events"
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME.lower() / "events"


@dataclass(frozen=True)
class RenderEvent:
    kind: str  # "complete" | "cancel"
    blend: str
    scene: str
    output: str
    started: float
    finished: float
    duration: float
    frames: int

    @property
    def title(self) -> str:
        return "レンダーが完了しました" if self.kind == "complete" else "レンダーを中止しました"

    @property
    def blend_name(self) -> str:
        return os.path.basename(self.blend) if self.blend else "（未保存のファイル）"

    def describe(self) -> str:
        frames = f"{self.frames}フレーム・" if self.frames > 1 else ""
        return f"{self.blend_name}（{frames}{format_duration(self.duration)}）"


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}時間{minutes}分"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def parse_event(text: str) -> RenderEvent | None:
    try:
        data = json.loads(text)
        kind = str(data["kind"])
        if kind not in ("complete", "cancel"):
            return None
        return RenderEvent(
            kind=kind,
            blend=str(data.get("blend", "")),
            scene=str(data.get("scene", "")),
            output=str(data.get("output", "")),
            started=float(data.get("started", 0)),
            finished=float(data["finished"]),
            duration=max(0.0, float(data.get("duration", 0))),
            frames=int(data.get("frames", 0)),
        )
    except (ValueError, KeyError, TypeError):
        return None


def should_notify(event: RenderEvent, min_seconds: float) -> bool:
    """完了したレンダーのうち、短すぎるもの（プレビュー確認など）は知らせない。"""
    return event.kind == "complete" and event.duration >= min_seconds


class EventSpool:
    """イベントのフォルダを一定間隔で見て、新しいものを順に渡す。"""

    def __init__(
        self,
        directory: Path,
        on_event: Callable[[RenderEvent], None],
        interval: float = 1.0,
        stale_seconds: float = STALE_SECONDS,
    ) -> None:
        self.directory = directory
        self.on_event = on_event
        self.interval = interval
        self.stale_seconds = stale_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="blendkeep-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    def poll_once(self) -> int:
        """1回分の取り込み。処理したイベントの数を返す。"""
        try:
            files = sorted(self.directory.glob("*.json"))
        except OSError:
            return 0
        handled = 0
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            event = parse_event(text)
            if event is None:
                # 書き込み途中かもしれないので、しばらく経った壊れたファイルだけ消す
                try:
                    if time.time() - path.stat().st_mtime > BROKEN_SECONDS:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            path.unlink(missing_ok=True)
            if time.time() - event.finished > self.stale_seconds:
                continue
            try:
                self.on_event(event)
            except Exception:  # noqa: BLE001  通知の失敗で取り込みを止めない
                log.exception("レンダーイベントの処理に失敗しました")
            handled += 1
        return handled
