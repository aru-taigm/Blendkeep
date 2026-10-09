"""Discord の Webhook へレンダー完了を送る（スマホにも通知したい人向け）。"""

from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request

from .render_events import RenderEvent

log = logging.getLogger(__name__)


def is_valid_url(url: str) -> bool:
    return url.startswith(("https://", "http://127.0.0.1", "http://localhost")) and len(url) > 12


def build_payload(event: RenderEvent) -> dict:
    lines = [f"**{event.title}**", event.describe()]
    if event.scene:
        lines.append(f"シーン: {event.scene}")
    return {"content": "\n".join(lines), "username": "BlendKeep"}


def post(url: str, payload: dict, timeout: float = 10.0) -> None:
    """送れなければ例外を投げる。"""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "BlendKeep"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310  URLは検証済み
        pass


def send_async(url: str, payload: dict) -> threading.Thread:
    def run() -> None:
        try:
            post(url, payload)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("Webhook の送信に失敗しました: %s", exc)

    thread = threading.Thread(target=run, name="blendkeep-webhook", daemon=True)
    thread.start()
    return thread
