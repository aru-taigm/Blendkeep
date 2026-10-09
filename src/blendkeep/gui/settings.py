"""設定ダイアログ。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, blender_setup
from ..config import Config


class SettingsDialog(QDialog):
    def __init__(
        self,
        config: Config,
        parent: QWidget | None = None,
        stats_text: str = "",
        on_compact: Callable[[], str] | None = None,
        on_test_webhook: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setMinimumWidth(560)
        self._config = config
        self._store_dir = config.store_dir
        self._on_compact = on_compact
        self._on_test_webhook = on_test_webhook

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("監視するフォルダ（中のフォルダもすべて対象です）"))
        self.dirs = QListWidget()
        self.dirs.addItems(config.watch_dirs)
        self.dirs.setMinimumHeight(120)
        self.dirs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.dirs.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        layout.addWidget(self.dirs)
        row = QHBoxLayout()
        add = QPushButton("フォルダを追加…")
        add.clicked.connect(self._add_dir)
        self.remove = QPushButton("選んだフォルダを外す")
        self.remove.clicked.connect(self._remove_dir)
        row.addWidget(add)
        row.addWidget(self.remove)
        row.addStretch(1)
        layout.addLayout(row)

        form = QFormLayout()
        self.max_versions = QSpinBox()
        self.max_versions.setRange(1, 1000)
        self.max_versions.setValue(config.max_versions_per_file)
        self.max_versions.setSuffix(" 件")
        self.max_versions.setToolTip("メモを付けた履歴は、この件数に入らず、自動では削除されません")
        form.addRow("1ファイルに残す履歴", self.max_versions)

        self.min_interval = QSpinBox()
        self.min_interval.setRange(0, 3600)
        self.min_interval.setValue(int(config.min_interval_seconds))
        self.min_interval.setSuffix(" 秒")
        form.addRow("履歴を作る最短の間隔", self.min_interval)

        self.notify = QCheckBox("履歴を作ったときに通知する")
        self.notify.setChecked(config.notify_on_snapshot)
        form.addRow("", self.notify)

        self.autostart = QCheckBox("Windows にログインしたとき自動で開始する")
        if autostart.is_supported():
            self.autostart.setChecked(autostart.is_enabled())
        else:
            self.autostart.setEnabled(False)
            self.autostart.setToolTip("Windows でのみ使えます")
        form.addRow("", self.autostart)
        layout.addLayout(form)

        layout.addWidget(QLabel("履歴の保存先と容量"))
        self.store_label = QLabel(self._store_dir)
        self.store_label.setObjectName("muted")
        self.store_label.setWordWrap(True)
        self.store_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.store_label)
        self.stats_label = QLabel(stats_text)
        self.stats_label.setObjectName("muted")
        layout.addWidget(self.stats_label)

        self.compress = QCheckBox("履歴を圧縮して保存する（容量を節約）")
        self.compress.setChecked(config.compress_history)
        layout.addWidget(self.compress)
        self.chunk = QCheckBox("大きなファイルは変わった部分だけ保存する（差分保存）")
        self.chunk.setChecked(config.chunk_large_files)
        layout.addWidget(self.chunk)

        store_row = QHBoxLayout()
        self.change_store = QPushButton("保存先を変更…")
        self.change_store.clicked.connect(self._choose_store)
        self.compact_button = QPushButton("いまある履歴を圧縮する…")
        self.compact_button.clicked.connect(self._compact)
        self.compact_button.setEnabled(on_compact is not None)
        store_row.addWidget(self.change_store)
        store_row.addWidget(self.compact_button)
        store_row.addStretch(1)
        layout.addLayout(store_row)

        layout.addWidget(QLabel("レンダー完了の通知"))
        self.blender_status = QLabel()
        self.blender_status.setObjectName("muted")
        self.blender_status.setWordWrap(True)
        layout.addWidget(self.blender_status)
        blender_row = QHBoxLayout()
        self.install_bridge = QPushButton("Blender に導入する")
        self.install_bridge.clicked.connect(self._install_bridge)
        self.remove_bridge = QPushButton("Blender から外す")
        self.remove_bridge.clicked.connect(self._remove_bridge)
        blender_row.addWidget(self.install_bridge)
        blender_row.addWidget(self.remove_bridge)
        blender_row.addStretch(1)
        layout.addLayout(blender_row)
        self._refresh_blender_status()

        render_form = QFormLayout()
        self.notify_render = QCheckBox("レンダーが終わったら通知する")
        self.notify_render.setChecked(config.notify_on_render)
        render_form.addRow("", self.notify_render)
        self.render_min = QSpinBox()
        self.render_min.setRange(0, 86400)
        self.render_min.setValue(int(config.render_min_seconds))
        self.render_min.setSuffix(" 秒以上")
        self.render_min.setToolTip("これより短いレンダー（プレビューの確認など）は通知しません")
        render_form.addRow("通知する最短の時間", self.render_min)
        self.webhook = QLineEdit(config.discord_webhook_url)
        self.webhook.setEchoMode(QLineEdit.EchoMode.Password)
        self.webhook.setPlaceholderText("Discord の Webhook URL（スマホにも通知したいとき）")
        render_form.addRow("Discord", self.webhook)
        self.test_webhook = QPushButton("テスト送信")
        self.test_webhook.clicked.connect(self._send_test)
        self.test_webhook.setEnabled(on_test_webhook is not None)
        render_form.addRow("", self.test_webhook)
        layout.addLayout(render_form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # --- 操作 ---------------------------------------------------------------

    def _add_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "監視するフォルダを選ぶ")
        if directory and directory not in self._current_dirs():
            self.dirs.addItem(directory)

    def _remove_dir(self) -> None:
        for item in self.dirs.selectedItems():
            self.dirs.takeItem(self.dirs.row(item))

    def _choose_store(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "履歴の保存先を選ぶ", self._store_dir)
        if directory:
            self.set_store_dir(directory)

    def set_store_dir(self, directory: str) -> None:
        self._store_dir = directory
        changed = directory != self._config.store_dir
        suffix = "\n（「保存」を押すと、履歴をこの場所へ移します）" if changed else ""
        self.store_label.setText(directory + suffix)

    def _compact(self) -> None:
        if self._on_compact is not None:
            self.stats_label.setText(self._on_compact())

    def _current_dirs(self) -> list[str]:
        return [self.dirs.item(i).text() for i in range(self.dirs.count())]

    # --- レンダー通知 ---------------------------------------------------------

    def _refresh_blender_status(self) -> None:
        targets = blender_setup.find_targets()
        if not targets:
            self.blender_status.setText(
                "Blender の設定フォルダが見つかりません。"
                "一度 Blender を起動してから開き直してください。"
            )
        else:
            parts = [f"{t.version}: {'導入済み' if t.installed else '未導入'}" for t in targets]
            self.blender_status.setText(
                "Blender のバージョンごとの状態  "
                + " / ".join(parts)
                + "\n導入したあと、Blender を起動し直すと有効になります。"
            )
        self.install_bridge.setEnabled(bool(targets))
        self.remove_bridge.setEnabled(any(t.installed for t in targets))

    def _install_bridge(self) -> None:
        try:
            done = blender_setup.install()
        except OSError as exc:
            QMessageBox.warning(self, "導入できませんでした", str(exc))
        else:
            QMessageBox.information(
                self,
                "導入しました",
                f"{len(done)} 個の Blender に導入しました。Blender を起動し直してください。",
            )
        self._refresh_blender_status()

    def _remove_bridge(self) -> None:
        try:
            blender_setup.uninstall()
        except OSError as exc:
            QMessageBox.warning(self, "外せませんでした", str(exc))
        self._refresh_blender_status()

    def _send_test(self) -> None:
        if self._on_test_webhook is None:
            return
        try:
            self._on_test_webhook(self.webhook.text().strip())
        except Exception as exc:  # noqa: BLE001  送れない理由はそのまま見せる
            QMessageBox.warning(self, "送信できませんでした", str(exc))
        else:
            QMessageBox.information(self, "送信しました", "Discord にテストを送りました。")

    # --- 結果 ---------------------------------------------------------------

    def result_config(self) -> Config:
        return replace(
            self._config,
            watch_dirs=self._current_dirs(),
            store_dir=self._store_dir,
            max_versions_per_file=self.max_versions.value(),
            min_interval_seconds=float(self.min_interval.value()),
            notify_on_snapshot=self.notify.isChecked(),
            compress_history=self.compress.isChecked(),
            chunk_large_files=self.chunk.isChecked(),
            notify_on_render=self.notify_render.isChecked(),
            render_min_seconds=float(self.render_min.value()),
            discord_webhook_url=self.webhook.text().strip(),
        )

    def autostart_wanted(self) -> bool:
        return self.autostart.isEnabled() and self.autostart.isChecked()
