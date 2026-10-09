"""メインウィンドウ: ファイルの一覧と、選んだファイルの履歴。"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import autostart
from ..blendfile import Thumbnail
from ..formatting import format_size, format_when
from ..store import MigrationCancelled, Snapshot, StoreCorruptionError
from . import theme
from .controller import AppController
from .model import FileEntry, default_restore_path, describe_stats, group_files
from .settings import SettingsDialog

THUMB_SIZE = 128
SNAPSHOT_ROLE = Qt.ItemDataRole.UserRole
FILE_KEY_ROLE = Qt.ItemDataRole.UserRole + 1


def thumbnail_pixmap(
    thumb: Thumbnail | None, size: int = THUMB_SIZE, badge: str | None = None
) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("#2b2e33"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    if thumb is None:
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "プレビューなし")
    else:
        image = QImage(
            thumb.rgba, thumb.width, thumb.height, thumb.width * 4, QImage.Format.Format_RGBA8888
        ).copy()
        scaled = image.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawImage((size - scaled.width()) // 2, (size - scaled.height()) // 2, scaled)
    if badge:
        bar = pixmap.rect().adjusted(0, size - 22, 0, 0)
        painter.fillRect(bar, QColor(0, 0, 0, 170))
        painter.setPen(QColor("#ffffff"))
        metrics = painter.fontMetrics()
        text = metrics.elidedText(badge, Qt.TextElideMode.ElideRight, size - 12)
        painter.drawText(bar, Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pixmap


def reveal_in_file_manager(path: str) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])  # noqa: S603, S607
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))


class MainWindow(QMainWindow):
    def __init__(self, controller: AppController) -> None:
        super().__init__()
        self.controller = controller
        self.setWindowTitle("BlendKeep")
        self.setWindowIcon(theme.make_app_icon())
        self.resize(980, 620)
        self.allow_close = False  # True のときだけ、閉じるボタンで終了する

        # 左: ファイルの一覧
        self.files = QListWidget()
        self.files.setMinimumWidth(240)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.files.currentItemChanged.connect(self._on_file_changed)

        # 右: 履歴
        self.title = QLabel()
        self.title.setObjectName("title")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("muted")
        self.history = QListWidget()
        self.history.setViewMode(QListView.ViewMode.IconMode)
        self.history.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.history.setResizeMode(QListView.ResizeMode.Adjust)
        self.history.setMovement(QListView.Movement.Static)
        self.history.setSpacing(8)
        self.history.setWordWrap(True)
        self.history.setUniformItemSizes(True)
        self.history.setGridSize(QSize(THUMB_SIZE + 32, THUMB_SIZE + 64))
        self.history.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.history.currentItemChanged.connect(self._update_buttons)
        self.history.itemSelectionChanged.connect(self._update_buttons)
        self.history.itemDoubleClicked.connect(lambda _item: self.restore_selected())
        self.history.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.history.customContextMenuRequested.connect(self._history_menu_requested)
        self.files.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self._file_menu_requested)

        self.restore_button = QPushButton("この版を取り出す…")
        self.restore_button.setObjectName("primary")
        self.restore_button.clicked.connect(self.restore_selected)
        self.note_button = QPushButton("メモ…")
        self.note_button.setToolTip("メモを付けた履歴は、古くなっても自動では削除されません")
        self.note_button.clicked.connect(self.edit_note)
        self.reveal_button = QPushButton("元のフォルダを開く")
        self.reveal_button.clicked.connect(self._reveal_original)
        self.delete_button = QPushButton("削除…")
        self.delete_button.setObjectName("danger")
        self.delete_button.clicked.connect(self.delete_selected)
        buttons = QHBoxLayout()
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.note_button)
        buttons.addWidget(self.reveal_button)
        buttons.addStretch(1)
        buttons.addWidget(self.delete_button)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        detail_layout.addWidget(self.title)
        detail_layout.addWidget(self.subtitle)
        detail_layout.addWidget(self.history, 1)
        detail_layout.addLayout(buttons)

        self.empty = QLabel()
        self.empty.setObjectName("empty")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty_button = QPushButton("監視するフォルダを追加…")
        self.empty_button.setObjectName("primary")
        self.empty_button.clicked.connect(self.open_settings)
        empty_page = QWidget()
        empty_layout = QVBoxLayout(empty_page)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self.empty)
        empty_layout.addWidget(self.empty_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)

        self.stack = QStackedWidget()
        self.stack.addWidget(detail)
        self.stack.addWidget(empty_page)

        splitter = QSplitter()
        splitter.addWidget(self.files)
        splitter.addWidget(self.stack)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 700])
        splitter.setChildrenCollapsible(False)
        wrapper = QWidget()
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(0, 0, 0, 0)
        wrapper_layout.addWidget(self._build_header())
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(12, 0, 12, 12)
        body_layout.addWidget(splitter)
        wrapper_layout.addWidget(body, 1)
        self.setCentralWidget(wrapper)

        controller.snapshotCreated.connect(lambda _snap: self.refresh())
        controller.stateChanged.connect(self._update_state)
        self.refresh()
        self._update_state()

    # --- 部品 ---------------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(12, 10, 12, 6)
        logo = QLabel()
        logo.setPixmap(theme.make_app_icon().pixmap(28, 28))
        name = QLabel("BlendKeep")
        name.setObjectName("title")
        row.addWidget(logo)
        row.addWidget(name)
        row.addStretch(1)
        self.pause_button = QPushButton("一時停止")
        self.pause_button.setCheckable(True)
        self.pause_button.setObjectName("pause")
        self.pause_button.toggled.connect(self.controller.set_paused)
        row.addWidget(self.pause_button)
        self.settings_button = QPushButton("設定…")
        self.settings_button.clicked.connect(self.open_settings)
        row.addWidget(self.settings_button)
        return header

    # --- 表示の更新 -----------------------------------------------------------

    def refresh(self) -> None:
        selected = self._current_file_key()
        snapshots = self.controller.snapshots()
        entries = group_files(snapshots)
        self.files.blockSignals(True)
        self.files.clear()
        for entry in entries:
            self.files.addItem(self._file_item(entry))
        self.files.blockSignals(False)

        self.files.setVisible(bool(entries))
        if not entries:
            self.stack.setCurrentIndex(1)
            self._update_empty_message()
            self._update_buttons()
            return

        target_row = 0
        for row in range(self.files.count()):
            if self.files.item(row).data(FILE_KEY_ROLE) == selected:
                target_row = row
                break
        self.stack.setCurrentIndex(0)
        self.files.setCurrentRow(target_row)
        self._show_history()

    def _file_item(self, entry: FileEntry) -> QListWidgetItem:
        parent = os.path.basename(entry.folder) or entry.folder
        item = QListWidgetItem(
            f"{entry.name}\n{parent}  ·  {entry.count} 件  ·  {format_when(entry.latest)}"
        )
        item.setData(FILE_KEY_ROLE, entry.key)
        item.setData(SNAPSHOT_ROLE, entry.path)
        item.setToolTip(entry.path)
        return item

    def _current_file_key(self) -> str | None:
        item = self.files.currentItem()
        return item.data(FILE_KEY_ROLE) if item else None

    def _current_file_path(self) -> str | None:
        item = self.files.currentItem()
        return item.data(SNAPSHOT_ROLE) if item else None

    def _on_file_changed(self) -> None:
        self._show_history()

    def _show_history(self, select_id: int | None = None) -> None:
        path = self._current_file_path()
        self.history.clear()
        if path is None:
            self._update_buttons()
            return
        snapshots = self.controller.snapshots(path)
        self.title.setText(os.path.basename(path))
        self.subtitle.setText(f"{path}\n{len(snapshots)} 件の履歴（新しい順）")
        for snap in snapshots:
            self.history.addItem(self._history_item(snap))
        if self.history.count():
            row = next(
                (
                    i
                    for i in range(self.history.count())
                    if self.history.item(i).data(SNAPSHOT_ROLE).id == select_id
                ),
                0,
            )
            self.history.setCurrentRow(row)
        self._update_buttons()

    def _history_item(self, snap: Snapshot) -> QListWidgetItem:
        label = f"{format_when(snap.taken_at)}\n{format_size(snap.size)}"
        item = QListWidgetItem(label)
        item.setIcon(thumbnail_pixmap(self.controller.thumbnail(snap), badge=snap.note))
        item.setData(SNAPSHOT_ROLE, snap)
        item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        tooltip = f"履歴 {snap.id}\n{snap.sha256[:12]}"
        item.setToolTip(f"{snap.note}\n{tooltip}" if snap.note else tooltip)
        return item

    def _update_empty_message(self) -> None:
        folders = self.controller.config.watch_dirs
        if not folders:
            self.empty.setText(
                "監視するフォルダがまだありません。\n"
                "Blender のファイルがあるフォルダを追加してください。"
            )
        else:
            listing = "\n".join(folders)
            self.empty.setText(
                "まだ履歴がありません。\n"
                ".blend を保存すると、ここに並びます。\n\n"
                f"監視中のフォルダ:\n{listing}"
            )

    def _update_state(self) -> None:
        controller = self.controller
        count = len(controller.config.watch_dirs)
        if controller.paused:
            self.statusBar().showMessage("一時停止中です")
        elif controller.watching:
            self.statusBar().showMessage(f"{count} 個のフォルダを監視しています")
        elif count:
            self.statusBar().showMessage("監視は停止しています")
        else:
            self.statusBar().showMessage("監視するフォルダがありません")
        blocked = self.pause_button.blockSignals(True)
        self.pause_button.setChecked(controller.paused)
        self.pause_button.setText("再開" if controller.paused else "一時停止")
        self.pause_button.blockSignals(blocked)
        if self.stack.currentIndex() == 1:
            self._update_empty_message()

    def _selected_snapshot(self) -> Snapshot | None:
        item = self.history.currentItem()
        return item.data(SNAPSHOT_ROLE) if item else None

    def _selected_snapshots(self) -> list[Snapshot]:
        items = self.history.selectedItems()
        if not items and self.history.currentItem() is not None:
            items = [self.history.currentItem()]
        return [item.data(SNAPSHOT_ROLE) for item in items]

    def _update_buttons(self) -> None:
        selected = self._selected_snapshots()
        self.restore_button.setEnabled(len(selected) == 1)
        self.note_button.setEnabled(len(selected) == 1)
        self.delete_button.setEnabled(bool(selected))
        self.delete_button.setText(f"{len(selected)} 件を削除…" if len(selected) > 1 else "削除…")
        self.reveal_button.setEnabled(self._current_file_path() is not None)

    # --- 操作 ---------------------------------------------------------------

    def restore_selected(self) -> None:
        snap = self._selected_snapshot()
        if snap is None:
            return
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "取り出し先",
            default_restore_path(snap),
            "Blender ファイル (*.blend)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not dest:
            return
        try:
            restored = self.controller.restore(snap.id, dest)
        except FileExistsError:
            QMessageBox.warning(
                self,
                "取り出せません",
                "同じ名前のファイルがすでにあります。\n"
                "元のファイルを守るため、上書きはしません。別の名前を指定してください。",
            )
            return
        except OSError as error:
            QMessageBox.critical(self, "取り出せません", str(error))
            return
        self.statusBar().showMessage(f"取り出しました: {restored}", 10000)
        reveal_in_file_manager(str(restored))

    def _reveal_original(self) -> None:
        path = self._current_file_path()
        if path:
            reveal_in_file_manager(path)

    def edit_note(self) -> None:
        selected = self._selected_snapshots()
        if len(selected) != 1:
            return
        snap = selected[0]
        text, accepted = QInputDialog.getText(
            self,
            "メモ",
            "この版のメモ（空にすると消えます）\nメモを付けた履歴は、古くなっても自動では削除されません。",
            text=snap.note or "",
        )
        if not accepted:
            return
        self.controller.set_note(snap.id, text)
        self._show_history(select_id=snap.id)

    def delete_selected(self) -> None:
        selected = self._selected_snapshots()
        if not selected:
            return
        noted = [s for s in selected if s.note]
        message = (
            f"選んだ {len(selected)} 件の履歴を削除します。\n"
            "元の .blend ファイルには影響しません。この操作は取り消せません。"
        )
        if noted:
            message += f"\n\nメモ付きの履歴が {len(noted)} 件含まれています。"
        answer = QMessageBox.question(
            self,
            "履歴を削除",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.controller.delete_snapshots([s.id for s in selected])
        self.statusBar().showMessage(
            f"{result.count} 件を削除しました（{format_size(result.freed_bytes)} を空けました）",
            10000,
        )
        self.refresh()

    def delete_file_history(self) -> None:
        path = self._current_file_path()
        if path is None:
            return
        count = len(self.controller.snapshots(path))
        answer = QMessageBox.question(
            self,
            "履歴をすべて削除",
            f"{os.path.basename(path)} の履歴 {count} 件を、すべて削除します。\n"
            "元の .blend ファイルには影響しません。この操作は取り消せません。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self.controller.delete_file_history(path)
        self.statusBar().showMessage(
            f"{result.count} 件を削除しました（{format_size(result.freed_bytes)} を空けました）",
            10000,
        )
        self.refresh()

    def build_history_menu(self) -> QMenu:
        menu = QMenu(self)
        for text, handler, enabled in (
            ("この版を取り出す…", self.restore_selected, self.restore_button.isEnabled()),
            ("メモを編集…", self.edit_note, self.note_button.isEnabled()),
            ("削除…", self.delete_selected, self.delete_button.isEnabled()),
        ):
            action = QAction(text, menu)
            action.setEnabled(enabled)
            action.triggered.connect(handler)
            menu.addAction(action)
        return menu

    def build_file_menu(self) -> QMenu:
        menu = QMenu(self)
        reveal = QAction("元のフォルダを開く", menu)
        reveal.triggered.connect(self._reveal_original)
        delete = QAction("このファイルの履歴をすべて削除…", menu)
        delete.triggered.connect(self.delete_file_history)
        menu.addAction(reveal)
        menu.addAction(delete)
        return menu

    def _history_menu_requested(self, position) -> None:
        item = self.history.itemAt(position)
        if item is not None and not item.isSelected():
            self.history.setCurrentItem(item)
        if item is not None:
            self.build_history_menu().exec(self.history.viewport().mapToGlobal(position))

    def _file_menu_requested(self, position) -> None:
        item = self.files.itemAt(position)
        if item is not None:
            self.files.setCurrentItem(item)
            self.build_file_menu().exec(self.files.viewport().mapToGlobal(position))

    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self.controller.config,
            self,
            stats_text=describe_stats(self.controller.stats()),
            on_compact=self._compact_history,
            on_test_webhook=self.controller.send_test_webhook,
        )
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return
        new_config = dialog.result_config()
        if new_config.store_dir != self.controller.config.store_dir and not self._move_store(
            new_config.store_dir
        ):
            new_config = replace(new_config, store_dir=self.controller.config.store_dir)
        self.controller.apply_config(new_config)
        if autostart.is_supported() and dialog.autostart_wanted() != autostart.is_enabled():
            try:
                autostart.set_enabled(dialog.autostart_wanted())
            except OSError as error:
                QMessageBox.warning(self, "自動起動の設定", str(error))
        self.refresh()

    # --- 履歴の移動・圧縮 -----------------------------------------------------

    def _progress_dialog(self, title: str) -> QProgressDialog:
        dialog = QProgressDialog(title, "キャンセル", 0, 1000, self)
        dialog.setWindowTitle("BlendKeep")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)
        return dialog

    @staticmethod
    def _progress_callback(dialog: QProgressDialog):
        def callback(done: int, total: int, name: str) -> None:
            dialog.setValue(int(done * 1000 / total) if total else 1000)
            dialog.setLabelText(f"{dialog.windowTitle()}\n{name}")
            QApplication.processEvents()
            if dialog.wasCanceled():
                raise MigrationCancelled

        return callback

    def _move_store(self, new_dir: str) -> bool:
        """履歴を新しい保存先へ移す。成功したら True。"""
        dialog = self._progress_dialog("履歴を移動しています…")
        try:
            result = self.controller.move_store(new_dir, self._progress_callback(dialog))
        except MigrationCancelled:
            QMessageBox.information(self, "移動を中止しました", "保存先は、元のままです。")
            return False
        except (ValueError, OSError, StoreCorruptionError) as error:
            QMessageBox.critical(self, "履歴を移せませんでした", str(error))
            return False
        finally:
            dialog.close()
        old = self.controller.previous_store_dir
        if result.adopted:
            QMessageBox.information(
                self,
                "保存先を切り替えました",
                "移動先にあった履歴を使います。\n元の場所の履歴は、そのまま残してあります。",
            )
        elif old is not None:
            answer = QMessageBox.question(
                self,
                "履歴を移しました",
                f"履歴を {new_dir} へ移しました。\n\n"
                f"元の場所の履歴を削除して、容量を空けますか？\n{old}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                freed = self.controller.delete_store_files(old)
                self.statusBar().showMessage(f"{format_size(freed)} を空けました", 10000)
        return True

    def _compact_history(self) -> str:
        """いまある履歴を圧縮する。設定画面に出す、新しい容量の説明を返す。"""
        dialog = self._progress_dialog("履歴を圧縮しています…")
        try:
            saved = self.controller.compact(self._progress_callback(dialog))
        except MigrationCancelled:
            saved = 0
        except (OSError, StoreCorruptionError) as error:
            QMessageBox.critical(self, "圧縮できませんでした", str(error))
            saved = 0
        finally:
            dialog.close()
        self.statusBar().showMessage(f"{format_size(saved)} 節約しました", 10000)
        return describe_stats(self.controller.stats())

    # --- 閉じる -------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.allow_close:
            event.accept()
        else:
            event.ignore()
            self.hide()
