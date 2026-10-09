"""アプリの入口。タスクトレイに常駐する。"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ..config import Config, default_config_path
from ..store import Snapshot
from . import theme
from .controller import AppController
from .single_instance import SingleInstance
from .window import MainWindow


class TrayApp:
    def __init__(self, app: QApplication, controller: AppController, window: MainWindow) -> None:
        self.app = app
        self.controller = controller
        self.window = window
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._create_tray()
        controller.snapshotCreated.connect(self._on_snapshot)
        controller.renderFinished.connect(self._on_render)

    def _create_tray(self) -> None:
        tray = QSystemTrayIcon(theme.make_app_icon())
        tray.setToolTip("BlendKeep")
        menu = QMenu()
        open_action = QAction("履歴を開く", menu)
        open_action.triggered.connect(self.show_window)
        menu.addAction(open_action)
        self.pause_action = QAction("一時停止", menu)
        self.pause_action.setCheckable(True)
        self.pause_action.toggled.connect(self.controller.set_paused)
        menu.addAction(self.pause_action)
        menu.addSeparator()
        quit_action = QAction("終了", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self.tray = tray
        self.controller.stateChanged.connect(self._sync_pause)
        self._menu = menu

    def _sync_pause(self) -> None:
        blocked = self.pause_action.blockSignals(True)
        self.pause_action.setChecked(self.controller.paused)
        self.pause_action.blockSignals(blocked)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_window()

    def _on_snapshot(self, snapshot: Snapshot) -> None:
        if self.tray is not None and self.controller.config.notify_on_snapshot:
            self.tray.showMessage(
                "履歴を作成しました",
                os.path.basename(snapshot.source_path),
                theme.make_app_icon(),
                3000,
            )

    def _on_render(self, event) -> None:  # noqa: ANN001
        if self.tray is not None:
            self.tray.showMessage(event.title, event.describe(), theme.make_app_icon(), 8000)

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        self.window.allow_close = True
        self.controller.shutdown()
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()


def _user_name() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001  ユーザー名が取れない環境でも起動できるようにする
        return "user"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="blendkeep-gui")
    parser.add_argument("--config", type=Path, default=None, help="設定ファイルのパス")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="画面を作って必要な部品が揃っているか確かめ、0 で終了する（配布物の動作確認用）",
    )
    parser.add_argument("--minimized", action="store_true", help="画面を出さずにトレイに常駐する")
    return parser


def self_test() -> int:
    """配布物（exe）に部品が揃っているかの確認。画面を作って、終了コードで結果を返す。"""
    import tempfile

    import watchdog.observers
    import zstandard

    from .. import blender_setup

    del watchdog.observers, zstandard  # 読み込めることだけ確かめる
    if "render_complete" not in blender_setup.bridge_source():
        return 2  # Blender に入れるスクリプトが同梱されていない
    app = QApplication.instance() or QApplication(sys.argv[:1])
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        controller = AppController(Config(store_dir=str(base / "store")), None, base / "events")
        window = MainWindow(controller)
        window.grab()
        controller.shutdown()
    del app
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.self_test:
        return self_test()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("BlendKeep")
    app.setQuitOnLastWindowClosed(False)

    # 二重起動を防ぐ。すでに起動していれば、画面を出すよう伝えて終了する。
    instance = SingleInstance(f"BlendKeep-{_user_name()}")
    if not instance.acquire():
        if not args.minimized:
            instance.notify_primary("show")
        return 0
    theme.apply_theme(app)
    app.setFont(theme.ui_font())

    config_path = args.config or default_config_path()
    controller = AppController(Config.load(config_path), config_path)
    window = MainWindow(controller)
    tray_app = TrayApp(app, controller, window)
    instance.messageReceived.connect(
        lambda message: tray_app.show_window() if message == "show" else None
    )
    controller.start()

    first_run = not controller.config.watch_dirs
    hidden = args.minimized and tray_app.tray is not None and not first_run
    if not hidden:
        window.show()
    if first_run:
        window.open_settings()
    code = app.exec()
    controller.shutdown()
    instance.release()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
