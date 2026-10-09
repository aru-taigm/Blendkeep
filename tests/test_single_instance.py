from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from blendkeep.gui.single_instance import SingleInstance  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QCoreApplication:
    return QCoreApplication.instance() or QCoreApplication([])


def _child(code: str, name: str, lock_dir: Path) -> subprocess.Popen[str]:
    script = textwrap.dedent(
        f"""
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication
        from blendkeep.gui.single_instance import SingleInstance
        app = QCoreApplication([])
        si = SingleInstance({name!r}, Path({str(lock_dir)!r}))
        {textwrap.indent(textwrap.dedent(code), "        ").strip()}
        """
    )
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait(qapp: QCoreApplication, proc: subprocess.Popen[str], timeout: float = 30.0) -> str:
    """子プロセスが終わるまで、こちらのイベントを処理し続ける（受け付ける側として動く）。"""
    deadline = time.monotonic() + timeout
    while proc.poll() is None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert proc.poll() is not None, "子プロセスが終わりません"
    out = proc.stdout.read() if proc.stdout else ""
    return out.strip()


@pytest.fixture
def name() -> str:
    return f"BlendKeepTest-{uuid.uuid4().hex[:12]}"


def test_first_acquires_and_second_is_refused(
    qapp: QCoreApplication, tmp_path: Path, name: str
) -> None:
    first = SingleInstance(name, tmp_path)
    assert first.acquire()
    received: list[str] = []
    first.messageReceived.connect(received.append)
    try:
        proc = _child('print("SECONDARY" if not si.acquire() else "PRIMARY")', name, tmp_path)
        assert _wait(qapp, proc).splitlines()[-1] == "SECONDARY"
    finally:
        first.release()


def test_second_instance_can_ask_first_to_show(
    qapp: QCoreApplication, tmp_path: Path, name: str
) -> None:
    first = SingleInstance(name, tmp_path)
    assert first.acquire()
    received: list[str] = []
    first.messageReceived.connect(received.append)
    try:
        code = 'print("OK" if (not si.acquire()) and si.notify_primary("show") else "NG")'
        proc = _child(code, name, tmp_path)
        assert _wait(qapp, proc).splitlines()[-1] == "OK"
        deadline = time.monotonic() + 5
        while not received and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.02)
        assert received == ["show"]
    finally:
        first.release()


def test_lock_is_released_and_can_be_taken_again(
    qapp: QCoreApplication, tmp_path: Path, name: str
) -> None:
    first = SingleInstance(name, tmp_path)
    assert first.acquire()
    first.release()
    second = SingleInstance(name, tmp_path)
    assert second.acquire()
    second.release()


def test_stale_lock_from_a_crashed_process_is_taken_over(
    qapp: QCoreApplication, tmp_path: Path, name: str
) -> None:
    # 子プロセスがロックを取ったまま、後片づけなしで終了する（異常終了の再現）。
    code = 'print("LOCKED" if si.acquire() else "FAILED", flush=True)\nimport os\nos._exit(0)'
    proc = _child(code, name, tmp_path)
    assert _wait(qapp, proc).splitlines()[-1] == "LOCKED"
    mine = SingleInstance(name, tmp_path)
    deadline = time.monotonic() + 10
    acquired = False
    while time.monotonic() < deadline and not acquired:
        acquired = mine.acquire()
        if not acquired:
            time.sleep(0.2)
    try:
        assert acquired
    finally:
        mine.release()


def test_notify_without_primary_fails_quickly(
    qapp: QCoreApplication, tmp_path: Path, name: str
) -> None:
    lone = SingleInstance(name, tmp_path)
    started = time.monotonic()
    assert not lone.notify_primary("show", timeout_ms=400)
    assert time.monotonic() - started < 3
