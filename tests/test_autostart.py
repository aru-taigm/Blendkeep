from __future__ import annotations

import sys
import types

import pytest

from blendkeep import autostart


class _FakeWinreg(types.ModuleType):
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self) -> None:
        super().__init__("winreg")
        self.values: dict[str, str] = {}
        self.run_key_exists = True

    class _Key:
        def __enter__(self) -> _FakeWinreg._Key:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def OpenKey(self, *args: object) -> _Key:  # noqa: N802
        if not self.run_key_exists:
            raise FileNotFoundError("Run キーがありません")
        return self._Key()

    def CreateKeyEx(self, *args: object) -> _Key:  # noqa: N802
        self.run_key_exists = True
        return self._Key()

    def QueryValueEx(self, key: object, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key: object, name: str, _r: int, _t: int, value: str) -> None:  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key: object, name: str) -> None:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def fake_windows(monkeypatch: pytest.MonkeyPatch) -> _FakeWinreg:
    fake = _FakeWinreg()
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    return fake


def test_enable_and_disable(fake_windows: _FakeWinreg) -> None:
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert autostart.is_enabled()
    assert "--minimized" in fake_windows.values[autostart.VALUE_NAME]
    autostart.set_enabled(False)
    assert not autostart.is_enabled()
    autostart.set_enabled(False)  # 何度呼んでもエラーにならない


def test_enable_creates_missing_run_key(fake_windows: _FakeWinreg) -> None:
    fake_windows.run_key_exists = False
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert autostart.is_enabled()


def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert not autostart.is_supported()
    assert not autostart.is_enabled()
    with pytest.raises(RuntimeError):
        autostart.set_enabled(True)


def test_command_quotes_executable() -> None:
    command = autostart.build_command()
    assert command.startswith('"')
    assert "--minimized" in command
