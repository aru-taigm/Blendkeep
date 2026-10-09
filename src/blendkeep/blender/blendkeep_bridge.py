"""BlendKeep: レンダーの開始・完了・中止を BlendKeep に伝える（Blender の起動スクリプト）。

scripts/startup に置くと、Blender の起動のたびに読み込まれる。アドオンの登録は要らない。
やることは、レンダーのイベントを JSON ファイルにして、受け渡し用のフォルダに置くだけ。
BlendKeep が動いていなくても、Blender の動作には影響しない。
"""

import json
import os
import sys
import time

import bpy
from bpy.app.handlers import persistent

_state = {"started": None, "frames": 0}


def _events_dir():
    override = os.environ.get("BLENDKEEP_EVENTS")
    if override:
        return override
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "BlendKeep", "events")
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "blendkeep", "events")


def _emit(kind):
    try:
        now = time.time()
        started = _state["started"] or now
        scene = bpy.context.scene
        event = {
            "kind": kind,
            "blend": bpy.data.filepath,
            "scene": scene.name if scene else "",
            "output": scene.render.filepath if scene else "",
            "started": started,
            "finished": now,
            "duration": now - started,
            "frames": _state["frames"],
            "pid": os.getpid(),
        }
        folder = _events_dir()
        os.makedirs(folder, exist_ok=True)
        name = f"{int(now * 1000)}-{os.getpid()}-{kind}"
        tmp = os.path.join(folder, name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(event, f)
        os.replace(tmp, os.path.join(folder, name + ".json"))
    except Exception:  # Blender の邪魔をしない
        pass
    finally:
        _state["started"] = None
        _state["frames"] = 0


@persistent
def _on_init(*_args):
    _state["started"] = time.time()
    _state["frames"] = 0


@persistent
def _on_frame(*_args):
    _state["frames"] += 1


@persistent
def _on_complete(*_args):
    _emit("complete")


@persistent
def _on_cancel(*_args):
    _emit("cancel")


_HANDLERS = (
    (bpy.app.handlers.render_init, _on_init),
    (bpy.app.handlers.render_post, _on_frame),
    (bpy.app.handlers.render_complete, _on_complete),
    (bpy.app.handlers.render_cancel, _on_cancel),
)


def register():
    unregister()
    for handlers, func in _HANDLERS:
        handlers.append(func)


def unregister():
    for handlers, func in _HANDLERS:
        # 再読み込みで別の関数オブジェクトになるので、名前で探して外す
        for existing in list(handlers):
            if getattr(existing, "__name__", "") == func.__name__ and getattr(
                existing, "__module__", ""
            ) == getattr(func, "__module__", ""):
                handlers.remove(existing)


register()
