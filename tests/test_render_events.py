from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from blendkeep import blender_setup, webhook
from blendkeep.render_events import (
    EventSpool,
    RenderEvent,
    default_events_dir,
    format_duration,
    parse_event,
    should_notify,
)


def event_json(**over) -> str:
    data = {
        "kind": "complete",
        "blend": "C:/p/scene.blend",
        "scene": "Scene",
        "output": "//out_",
        "started": time.time() - 100,
        "finished": time.time(),
        "duration": 100.0,
        "frames": 10,
    }
    data.update(over)
    return json.dumps(data)


def test_parse_and_describe() -> None:
    ev = parse_event(event_json())
    assert ev is not None and ev.kind == "complete"
    assert "scene.blend" in ev.describe() and "10フレーム" in ev.describe()
    assert "1分40秒" in ev.describe()


@pytest.mark.parametrize("text", ["", "not json", "{}", '{"kind": "x", "finished": 1}', "[]"])
def test_parse_rejects_bad_input(text: str) -> None:
    assert parse_event(text) is None


def test_format_duration() -> None:
    assert format_duration(5) == "5秒"
    assert format_duration(125) == "2分5秒"
    assert format_duration(3 * 3600 + 120) == "3時間2分"


def test_should_notify_filters_short_and_cancel() -> None:
    long = parse_event(event_json(duration=40))
    short = parse_event(event_json(duration=5))
    cancel = parse_event(event_json(kind="cancel", duration=400))
    assert should_notify(long, 30) and not should_notify(short, 30)
    assert not should_notify(cancel, 30)
    assert should_notify(short, 0)


def test_spool_delivers_and_removes_files(tmp_path: Path) -> None:
    got: list[RenderEvent] = []
    spool = EventSpool(tmp_path, got.append)
    (tmp_path / "a.json").write_text(event_json(), encoding="utf-8")
    (tmp_path / "bad.json").write_text("zzz", encoding="utf-8")
    (tmp_path / "half.tmp").write_text(event_json(), encoding="utf-8")
    assert spool.poll_once() == 1
    assert len(got) == 1
    assert [p.name for p in tmp_path.glob("*.json")] == ["bad.json"]  # 新しい壊れたファイルは残す
    old = time.time() - 600
    os.utime(tmp_path / "bad.json", (old, old))
    spool.poll_once()
    assert not list(tmp_path.glob("*.json"))
    assert (tmp_path / "half.tmp").exists()  # 書き込み途中のファイルには触らない


def test_spool_drops_stale_events(tmp_path: Path) -> None:
    got: list[RenderEvent] = []
    spool = EventSpool(tmp_path, got.append, stale_seconds=60)
    (tmp_path / "old.json").write_text(event_json(finished=time.time() - 3600), encoding="utf-8")
    assert spool.poll_once() == 0 and not got and not list(tmp_path.glob("*.json"))


def test_spool_survives_callback_error(tmp_path: Path) -> None:
    calls: list[int] = []

    def boom(_ev: RenderEvent) -> None:
        calls.append(1)
        raise RuntimeError("x")

    spool = EventSpool(tmp_path, boom)
    for n in range(2):
        (tmp_path / f"{n}.json").write_text(event_json(), encoding="utf-8")
    assert spool.poll_once() == 2 and len(calls) == 2


def test_spool_thread_picks_up_new_file(tmp_path: Path) -> None:
    got: list[RenderEvent] = []
    spool = EventSpool(tmp_path / "ev", got.append, interval=0.05)
    spool.start()
    try:
        (tmp_path / "ev" / "n.json").write_text(event_json(), encoding="utf-8")
        deadline = time.time() + 5
        while not got and time.time() < deadline:
            time.sleep(0.05)
    finally:
        spool.stop()
    assert len(got) == 1


def test_default_events_dir_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BLENDKEEP_EVENTS", str(tmp_path))
    assert default_events_dir() == tmp_path


# --- Webhook -----------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Handler.received.append(json.loads(body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


def test_webhook_posts_json() -> None:
    _Handler.received = []
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        assert webhook.is_valid_url(url)
        ev = parse_event(event_json())
        webhook.send_async(url, webhook.build_payload(ev)).join(5)
    finally:
        server.shutdown()
    assert len(_Handler.received) == 1
    assert "scene.blend" in _Handler.received[0]["content"]


def test_webhook_failure_does_not_raise_in_async() -> None:
    webhook.send_async("http://127.0.0.1:9/none", {"content": "x"}).join(10)


@pytest.mark.parametrize("url", ["", "ftp://x/y", "http://example.com/x", "https://"])
def test_webhook_url_validation(url: str) -> None:
    assert not webhook.is_valid_url(url)


# --- Blender への導入 -----------------------------------------------------------


def test_find_targets_sorted_and_filtered(tmp_path: Path) -> None:
    for name in ("4.2", "5.2", "3.6", "config", "5.2.2"):
        (tmp_path / name).mkdir()
    (tmp_path / "file.txt").write_text("x")
    versions = [t.version for t in blender_setup.find_targets(tmp_path)]
    assert versions == ["5.2", "4.2", "3.6"]


def test_find_targets_missing_root(tmp_path: Path) -> None:
    assert blender_setup.find_targets(tmp_path / "none") == []


def test_install_and_uninstall(tmp_path: Path) -> None:
    (tmp_path / "5.2").mkdir()
    (tmp_path / "4.2").mkdir()
    targets = blender_setup.find_targets(tmp_path)
    other = tmp_path / "5.2" / "scripts" / "startup" / "mine.py"
    other.parent.mkdir(parents=True)
    other.write_text("# 自分のスクリプト", encoding="utf-8")
    assert len(blender_setup.install(targets)) == 2
    targets = blender_setup.find_targets(tmp_path)
    assert all(t.installed and blender_setup.is_up_to_date(t) for t in targets)
    assert "render_complete" in targets[0].script_path.read_text(encoding="utf-8")
    assert len(blender_setup.uninstall(targets)) == 2
    assert not any(t.installed for t in blender_setup.find_targets(tmp_path))
    assert other.read_text(encoding="utf-8") == "# 自分のスクリプト"  # 他のスクリプトは消さない


def test_cli_blender_setup(monkeypatch, tmp_path: Path, capsys) -> None:
    from blendkeep.cli import main

    monkeypatch.setenv("BLENDKEEP_BLENDER_CONFIG", str(tmp_path))
    assert main(["blender-setup", "status"]) == 1  # 見つからない
    (tmp_path / "5.2").mkdir()
    assert main(["blender-setup", "install"]) == 0
    assert main(["blender-setup", "status"]) == 0
    assert "導入済み" in capsys.readouterr().out
    assert main(["blender-setup", "uninstall"]) == 0
