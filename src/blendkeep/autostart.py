"""Windows のログイン時に自動で起動する設定（レジストリの Run キー）。"""

from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "BlendKeep"


def is_supported() -> bool:
    return sys.platform == "win32"


def build_command() -> str:
    """ログイン時に実行するコマンド。画面は出さずにトレイに常駐する。"""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return f'"{sys.executable}" --minimized'
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" -m blendkeep.gui --minimized'


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> None:
    if not is_supported():
        raise RuntimeError("自動起動の設定は Windows のみ対応しています")
    import winreg

    # Run キーが無い環境でも失敗しないよう、無ければ作る。
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, build_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
