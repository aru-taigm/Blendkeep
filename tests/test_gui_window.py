from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from blendkeep.config import Config  # noqa: E402
from blendkeep.gui.controller import AppController  # noqa: E402
from blendkeep.gui.settings import SettingsDialog  # noqa: E402
from blendkeep.gui.window import MainWindow, thumbnail_pixmap  # noqa: E402

FIXTURE = Path(__file__).parent / "data" / "blender-5.2.2-compressed.blend"


@pytest.fixture(autouse=True)
def _no_real_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows でもテストが本物のレジストリ（ログイン時の自動起動）を触らないようにする。"""
    from blendkeep import autostart

    monkeypatch.setattr(autostart, "is_supported", lambda: False)


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(tmp_path: Path, qapp: QApplication):
    watched = tmp_path / "projects"
    watched.mkdir()
    config = Config(
        watch_dirs=[str(watched)],
        store_dir=str(tmp_path / "store"),
        debounce_seconds=0.3,
        min_interval_seconds=0.0,
    )
    ctl = AppController(config, tmp_path / "config.json")
    yield ctl
    ctl.shutdown()


def _pump(qapp: QApplication, condition, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.05)
    return False


def _save_version(path: Path, version: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(FIXTURE.read_bytes() + b"\0" * version * 512)


def test_empty_state(controller: AppController, qapp: QApplication) -> None:
    window = MainWindow(controller)
    assert window.stack.currentIndex() == 1
    assert not window.restore_button.isEnabled()
    assert controller.config.watch_dirs[0] in window.empty.text()


def test_lists_files_and_history(controller: AppController, qapp: QApplication) -> None:
    project = Path(controller.config.watch_dirs[0])
    for name in ("a.blend", "b.blend"):
        for version in (1, 2):
            _save_version(project / name, version)
            controller.store.add(project / name, note="メモ" if version == 2 else None)
    window = MainWindow(controller)
    assert window.stack.currentIndex() == 0
    assert window.files.count() == 2
    assert window.history.count() == 2
    assert window.restore_button.isEnabled()
    assert window.title.text() in {"a.blend", "b.blend"}


def test_thumbnail_pixmap_shapes(qapp: QApplication) -> None:
    from blendkeep.blendfile import read_thumbnail

    thumb = read_thumbnail(FIXTURE)
    assert thumb is not None
    pixmap = thumbnail_pixmap(thumb, 96, badge="レンダー前")
    assert (pixmap.width(), pixmap.height()) == (96, 96)
    assert thumbnail_pixmap(None, 96).width() == 96


def test_restore_via_dialog(
    controller: AppController,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = Path(controller.config.watch_dirs[0])
    _save_version(project / "a.blend", 1)
    controller.store.add(project / "a.blend")
    window = MainWindow(controller)
    dest = tmp_path / "out" / "back.blend"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), ""))
    monkeypatch.setattr("blendkeep.gui.window.reveal_in_file_manager", lambda _p: None)
    window.restore_selected()
    assert dest.read_bytes() == (project / "a.blend").read_bytes()

    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[1]))
    window.restore_selected()  # 同じ場所には上書きしない
    assert warnings == ["取り出せません"]
    assert dest.read_bytes() == (project / "a.blend").read_bytes()


def test_cancelled_dialog_does_nothing(
    controller: AppController, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Path(controller.config.watch_dirs[0])
    _save_version(project / "a.blend", 1)
    controller.store.add(project / "a.blend")
    window = MainWindow(controller)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    window.restore_selected()
    assert not list(project.glob("*.restored-*"))


def test_end_to_end_save_updates_window(controller: AppController, qapp: QApplication) -> None:
    window = MainWindow(controller)
    controller.start()
    assert controller.watching
    project = Path(controller.config.watch_dirs[0])
    _save_version(project / "scene.blend", 1)
    assert _pump(qapp, lambda: window.files.count() == 1)
    assert window.stack.currentIndex() == 0
    _save_version(project / "scene.blend", 2)
    assert _pump(qapp, lambda: window.history.count() == 2)


def test_pause_and_resume(controller: AppController, qapp: QApplication) -> None:
    window = MainWindow(controller)
    controller.start()
    assert controller.watching
    window.pause_button.setChecked(True)
    assert controller.paused and not controller.watching
    assert window.pause_button.text() == "再開"
    project = Path(controller.config.watch_dirs[0])
    _save_version(project / "scene.blend", 1)
    time.sleep(1.0)
    assert controller.snapshots() == []  # 停止中は履歴を作らない
    window.pause_button.setChecked(False)
    assert controller.watching
    assert window.pause_button.text() == "一時停止"


def test_close_hides_instead_of_quitting(controller: AppController, qapp: QApplication) -> None:
    window = MainWindow(controller)
    window.show()
    window.close()
    assert not window.isVisible()
    window.allow_close = True
    window.show()
    window.close()
    assert not window.isVisible()


def test_settings_dialog_roundtrip(
    controller: AppController, qapp: QApplication, tmp_path: Path
) -> None:
    dialog = SettingsDialog(controller.config)
    extra = tmp_path / "more"
    extra.mkdir()
    dialog.dirs.addItem(str(extra))
    dialog.max_versions.setValue(7)
    dialog.min_interval.setValue(5)
    dialog.notify.setChecked(False)
    result = dialog.result_config()
    assert str(extra) in result.watch_dirs
    assert (result.max_versions_per_file, result.min_interval_seconds) == (7, 5.0)
    assert result.notify_on_snapshot is False
    assert result.store_dir == controller.config.store_dir
    assert not dialog.autostart_wanted()  # Linux では無効


def test_apply_config_saves_and_restarts(controller: AppController, tmp_path: Path) -> None:
    controller.start()
    new = Config(
        watch_dirs=[str(tmp_path / "other")],
        store_dir=controller.config.store_dir,
        max_versions_per_file=3,
    )
    (tmp_path / "other").mkdir()
    controller.apply_config(new)
    assert controller.config.max_versions_per_file == 3
    assert Config.load(controller.config_path).max_versions_per_file == 3
    assert controller.watching


# --- 履歴の保存先の移動・圧縮 -------------------------------------------------------


def _fill(controller: AppController, count: int = 3) -> Path:
    project = Path(controller.config.watch_dirs[0])
    for version in range(1, count + 1):
        _save_version(project / "scene.blend", version)
        controller.store.add(project / "scene.blend", note=f"v{version}")
    return project / "scene.blend"


def test_controller_move_store(controller: AppController, qapp: QApplication, tmp_path: Path):
    blend = _fill(controller)
    controller.start()
    old_dir = controller.store.root
    before = [(s.id, s.note) for s in controller.snapshots()]
    new_dir = tmp_path / "newplace" / "store"

    result = controller.move_store(str(new_dir))
    assert result.objects == 3
    assert controller.store.root == new_dir.resolve() or controller.store.root == new_dir
    assert [(s.id, s.note) for s in controller.snapshots()] == before
    assert Config.load(controller.config_path).store_dir == str(new_dir)
    assert controller.previous_store_dir == old_dir
    assert controller.watching  # 移動のあとも、監視は続いている

    freed = controller.delete_store_files(old_dir)
    assert freed > 0 and not old_dir.exists()

    # 新しい保存先に、次の保存が入る
    _save_version(blend, 9)
    assert _pump(qapp, lambda: len(controller.snapshots()) == 4)
    assert (new_dir / "index.db").exists()


def test_apply_config_ignores_store_dir_change(controller: AppController, tmp_path: Path) -> None:
    from dataclasses import replace

    original = controller.config.store_dir
    controller.apply_config(replace(controller.config, store_dir=str(tmp_path / "other")))
    assert controller.config.store_dir == original
    assert controller.store.root == Path(original)


def test_window_move_store_flow(
    controller: AppController,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fill(controller)
    window = MainWindow(controller)
    old_dir = controller.store.root
    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes,
    )
    new_dir = tmp_path / "moved"
    assert window._move_store(str(new_dir)) is True
    assert asked and str(old_dir) in asked[0]
    assert not old_dir.exists()  # 「はい」を選んだので、元の場所は消えた
    window.refresh()
    assert window.history.count() == 3


def test_window_move_store_keeps_old_when_answer_is_no(
    controller: AppController,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fill(controller)
    window = MainWindow(controller)
    old_dir = controller.store.root
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    assert window._move_store(str(tmp_path / "moved")) is True
    assert (old_dir / "index.db").exists()


def test_window_move_store_cancel_and_error(
    controller: AppController,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QProgressDialog

    _fill(controller)
    controller.start()
    window = MainWindow(controller)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append("info"))
    monkeypatch.setattr(QMessageBox, "critical", lambda *a, **k: shown.append("critical"))

    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)
    target = tmp_path / "cancelled"
    assert window._move_store(str(target)) is False
    assert shown == ["info"]
    assert controller.store.root != target and controller.watching
    assert not any(p.is_file() for p in target.rglob("*")) if target.exists() else True

    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: False)
    nested = controller.store.root / "inside"
    assert window._move_store(str(nested)) is False  # 保存先の中は選べない
    assert shown == ["info", "critical"]


def test_open_settings_changes_store_dir(
    controller: AppController,
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    _fill(controller)
    window = MainWindow(controller)
    new_dir = str(tmp_path / "via-settings")
    wanted = replace(controller.config, store_dir=new_dir, compress_history=False)
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: SettingsDialog.DialogCode.Accepted)
    monkeypatch.setattr(SettingsDialog, "result_config", lambda self: wanted)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    window.open_settings()
    assert controller.config.store_dir == new_dir
    assert controller.config.compress_history is False
    assert controller.store.compress is False


def test_window_compact_history(
    controller: AppController, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zstandard

    controller.store.compress = False
    project = Path(controller.config.watch_dirs[0])
    plain = zstandard.ZstdDecompressor().stream_reader(FIXTURE.open("rb")).read()
    (project / "scene.blend").write_bytes(plain)
    controller.store.add(project / "scene.blend")
    window = MainWindow(controller)
    before = controller.stats().stored_bytes
    text = window._compact_history()
    assert controller.stats().stored_bytes < before
    assert "節約" in text


def test_settings_dialog_store_and_compress(controller: AppController, qapp: QApplication) -> None:
    calls: list[int] = []
    dialog = SettingsDialog(
        controller.config, stats_text="履歴 0 件", on_compact=lambda: calls.append(1) or "更新後"
    )
    assert dialog.stats_label.text() == "履歴 0 件"
    assert dialog.compress.isChecked()
    dialog.set_store_dir("D:/somewhere")
    dialog.compress.setChecked(False)
    result = dialog.result_config()
    assert result.store_dir == "D:/somewhere" and result.compress_history is False
    assert "保存」を押すと" in dialog.store_label.text()
    dialog.compact_button.click()
    assert calls == [1] and dialog.stats_label.text() == "更新後"
    assert not SettingsDialog(controller.config).compact_button.isEnabled()


# --- メモ・削除・メニュー -----------------------------------------------------------


def _items(window: MainWindow) -> list:
    return [window.history.item(i) for i in range(window.history.count())]


def test_edit_note_via_dialog(
    controller: AppController, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtWidgets import QInputDialog

    _fill(controller, 3)
    window = MainWindow(controller)
    target = window.history.item(1).data(Qt.ItemDataRole.UserRole)
    window.history.setCurrentRow(1)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("  納品版  ", True))
    window.edit_note()
    assert controller.store.get(target.id).note == "納品版"
    # 履歴の並びと選択位置は、メモを付けても変わらない
    assert window.history.currentItem().data(Qt.ItemDataRole.UserRole).id == target.id

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("別の内容", False))
    window.edit_note()  # キャンセル
    assert controller.store.get(target.id).note == "納品版"

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("", True))
    window.edit_note()  # 空にすると消える
    assert controller.store.get(target.id).note is None


def test_delete_selected_with_confirmation(
    controller: AppController, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fill(controller, 4)
    for snap in controller.snapshots():  # _fill は全部にメモを付けるので、いったん消す
        controller.store.set_note(snap.id, None)
    controller.store.set_note(controller.snapshots()[0].id, "メモ付き")
    window = MainWindow(controller)
    assert window.history.count() == 4
    window.history.item(0).setSelected(True)
    window.history.item(1).setSelected(True)
    assert window.delete_button.text() == "2 件を削除…"
    assert not window.restore_button.isEnabled()  # 複数選択のときは取り出せない

    asked: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    window.delete_selected()
    assert window.history.count() == 4  # 「いいえ」なら消えない
    assert "メモ付きの履歴が 1 件" in asked[0]

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.delete_selected()
    assert window.history.count() == 2
    assert len(controller.snapshots()) == 2


def test_delete_file_history(
    controller: AppController, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Path(controller.config.watch_dirs[0])
    for name in ("a.blend", "b.blend"):
        _save_version(project / name, 1)
        controller.store.add(project / name)
    window = MainWindow(controller)
    assert window.files.count() == 2
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.delete_file_history()
    assert window.files.count() == 1
    assert len(controller.snapshots()) == 1


def test_context_menus_reflect_state(controller: AppController, qapp: QApplication) -> None:
    _fill(controller, 2)
    window = MainWindow(controller)
    actions = {a.text(): a for a in window.build_history_menu().actions()}
    assert set(actions) == {"この版を取り出す…", "メモを編集…", "削除…"}
    assert all(a.isEnabled() for a in actions.values())
    window.history.item(0).setSelected(True)
    window.history.item(1).setSelected(True)
    actions = {a.text(): a for a in window.build_history_menu().actions()}
    assert not actions["この版を取り出す…"].isEnabled() and actions["削除…"].isEnabled()
    file_actions = [a.text() for a in window.build_file_menu().actions()]
    assert "このファイルの履歴をすべて削除…" in file_actions


def test_render_event_reaches_signal_and_webhook(controller, qapp, tmp_path: Path) -> None:
    import json

    from blendkeep.render_events import parse_event

    got = []
    controller.renderFinished.connect(got.append)
    controller.config.render_min_seconds = 30
    now = time.time()

    def event(duration: float):
        return parse_event(
            json.dumps(
                {"kind": "complete", "blend": "a.blend", "finished": now, "duration": duration}
            )
        )

    controller._on_render_event(event(5))
    assert got == []  # 短いレンダーは通知しない
    controller._on_render_event(event(90))
    assert len(got) == 1
    controller.config.notify_on_render = False
    controller._on_render_event(event(90))
    assert len(got) == 1  # 通知オフ


def test_settings_dialog_returns_render_options(qapp) -> None:
    config = Config(notify_on_render=True, render_min_seconds=45, discord_webhook_url="")
    dialog = SettingsDialog(config)
    dialog.render_min.setValue(120)
    dialog.notify_render.setChecked(False)
    dialog.webhook.setText("  https://discord.com/api/webhooks/1/x  ")
    result = dialog.result_config()
    assert result.render_min_seconds == 120 and not result.notify_on_render
    assert result.discord_webhook_url == "https://discord.com/api/webhooks/1/x"


def test_self_test_passes(qapp) -> None:
    from blendkeep.gui.app import self_test

    assert self_test() == 0
