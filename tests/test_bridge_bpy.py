"""本物の Blender（bpy モジュール）でレンダーして、起動スクリプトのイベントを確かめる。

bpy が入っていない環境（CI など）では飛ばす。起動フォルダから読み込まれること自体は
bpy モジュールでは試せない（Blender 本体のマニュアルに書かれた仕様に従う）。
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

bpy = pytest.importorskip("bpy")

from blendkeep import blender_setup  # noqa: E402
from blendkeep.render_events import EventSpool  # noqa: E402


@pytest.mark.timeout(120)
def test_real_render_produces_events(monkeypatch, tmp_path: Path) -> None:
    events = tmp_path / "events"
    monkeypatch.setenv("BLENDKEEP_EVENTS", str(events))
    spec = importlib.util.spec_from_file_location(
        "blendkeep_bridge", Path(blender_setup.__file__).parent / "blender" / "blendkeep_bridge.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["blendkeep_bridge"] = module
    spec.loader.exec_module(module)
    try:
        bpy.ops.wm.read_factory_settings(use_empty=False)
        scene = bpy.context.scene
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
        scene.cycles.device = "CPU"
        scene.render.resolution_x = scene.render.resolution_y = 32
        scene.render.filepath = str(tmp_path / "out_")
        scene.frame_start, scene.frame_end = 1, 3
        before = time.time()
        bpy.ops.render.render(animation=True)
        got = []
        EventSpool(events, got.append).poll_once()
    finally:
        module.unregister()
    assert len(got) == 1
    event = got[0]
    assert event.kind == "complete" and event.frames == 3
    assert before <= event.started <= event.finished and event.duration > 0
