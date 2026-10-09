"""配色とアイコン。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication

ACCENT = "#3fb6a8"
BACKGROUND = "#202226"
PANEL = "#1a1c1f"
TEXT = "#e8e9eb"
MUTED = "#9aa0a6"

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BACKGROUND}; }}
QPushButton#pause:checked {{ background: #b5651d; color: #ffffff; }}
QStatusBar {{ color: {MUTED}; }}
QSplitter::handle {{ background: {BACKGROUND}; width: 1px; }}
QListWidget {{
    background: {PANEL}; border: 1px solid #2c2f34; border-radius: 8px; outline: 0;
    color: {TEXT}; padding: 4px;
}}
QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
QListWidget::item:hover {{ background: #25282c; }}
QListWidget::item:selected {{ background: {ACCENT}; color: #0e1a19; }}
QPushButton {{
    background: #2e3136; color: {TEXT}; border: none; border-radius: 6px; padding: 8px 16px;
}}
QPushButton:hover {{ background: #3a3e44; }}
QPushButton:disabled {{ color: #666b72; background: #26282c; }}
QPushButton#danger {{ color: #ff8a80; }}
QPushButton#danger:hover {{ background: #4a2b2b; }}
QPushButton#danger:disabled {{ color: #6b4a48; }}
QMenu {{ background: {PANEL}; color: {TEXT}; border: 1px solid #2c2f34; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #0e1a19; }}
QPushButton#primary {{ background: {ACCENT}; color: #0e1a19; font-weight: bold; }}
QPushButton#primary:hover {{ background: #52c7b9; }}
QPushButton#primary:disabled {{ background: #2a4a46; color: #5b7d79; }}
QLabel#title {{ font-size: 18px; font-weight: bold; color: {TEXT}; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#empty {{ color: {MUTED}; font-size: 14px; }}
QSpinBox, QCheckBox {{ color: {TEXT}; }}
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, BACKGROUND),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, PANEL),
        (QPalette.ColorRole.AlternateBase, BACKGROUND),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, "#2e3136"),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#0e1a19"),
        (QPalette.ColorRole.ToolTipBase, PANEL),
        (QPalette.ColorRole.ToolTipText, TEXT),
        (QPalette.ColorRole.PlaceholderText, MUTED),
    ):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(STYLESHEET)


def make_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_draw_icon(size))
    return icon


def _draw_icon(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#52c7b9"))
    gradient.setColorAt(1.0, QColor("#2a8f84"))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect, size * 0.22, size * 0.22)
    # 履歴を表す、積み重なった3枚のカード。
    painter.setBrush(QColor(255, 255, 255, 235))
    for i, alpha in enumerate((110, 170, 240)):
        painter.setBrush(QColor(255, 255, 255, alpha))
        offset = size * 0.07 * (2 - i)
        card = QRectF(size * 0.27 + offset * 0.5, size * 0.27 + offset, size * 0.46, size * 0.30)
        painter.drawRoundedRect(card, size * 0.05, size * 0.05)
    painter.setPen(QPen(QColor("#1f6f66"), max(1.0, size * 0.04)))
    painter.drawLine(QPointF(size * 0.34, size * 0.40), QPointF(size * 0.60, size * 0.40))
    painter.end()
    return pixmap


def ui_font() -> QFont:
    font = QFont()
    font.setPointSize(10)
    return font
