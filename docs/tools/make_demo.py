"""README 用のデモ GIF を作る（デモ用のデータを使い、実際の画面を描画して並べる）。

使い方:
  QT_QPA_PLATFORM=offscreen python docs/tools/make_demo.py docs/demo.gif
Pillow が必要（pip install pillow）。サムネイルは、絵を描いて作った見本です。
"""

from __future__ import annotations

import struct
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication

from blendkeep.config import Config
from blendkeep.gui import theme
from blendkeep.gui.controller import AppController
from blendkeep.gui.window import MainWindow
from blendkeep.store import SnapshotStore

W, H = 128, 128
HEADER = b"BLENDER17-01v0502"


def thumb_image(step: int) -> QImage:
    """モデリングが進んでいく様子の見本（版ごとに部品が増える）。"""
    img = QImage(W, H, QImage.Format.Format_RGBA8888)
    img.fill(QColor("#2b2f36"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#3a404a"))
    p.drawRect(0, 92, W, 36)  # 床
    parts = [
        (QColor("#c9d1d9"), QRectF(46, 28, 36, 36), "ellipse"),  # 頭
        (QColor("#7fb8ff"), QRectF(40, 62, 48, 44), "rect"),  # 体
        (QColor("#ff9d5c"), QRectF(22, 66, 16, 34), "rect"),  # 腕
        (QColor("#ff9d5c"), QRectF(90, 66, 16, 34), "rect"),
        (QColor("#52c7b9"), QRectF(52, 14, 24, 14), "rect"),  # 帽子
    ]
    for color, rect, kind in parts[: step + 1]:
        p.setBrush(QBrush(color))
        p.drawEllipse(rect) if kind == "ellipse" else p.drawRoundedRect(rect, 6, 6)
    p.end()
    return img


def blend_bytes(step: int) -> bytes:
    """サムネイル付きの、最小の新形式 .blend（本物の Blender では開けない見本）。"""
    img = thumb_image(step).flipped(Qt.Orientation.Vertical)  # .blend は下の行から並ぶ
    rgba = bytes(img.constBits())[: W * H * 4]
    body = struct.pack("<ii", W, H) + rgba
    test = b"TEST" + struct.pack("<iQqq", 0, 0x10, len(body), 1) + body
    pad = bytes((step * 37 + i) % 251 for i in range(20000 + step * 3000))
    data = b"DATA" + struct.pack("<iQqq", 0, 0x20, len(pad), 1) + pad
    end = b"ENDB" + struct.pack("<iQqq", 0, 0, 0, 0)
    return HEADER + test + data + end


def build(root: Path, versions: int) -> AppController:
    watch = root / "Blender" / "ProjectA"
    watch.mkdir(parents=True)
    store = SnapshotStore(root / "store", chunk_large_files=False)
    f = watch / "character_rig.blend"
    now = time.time()
    ago = [60, 60 * 25, 3600 * 3, 86400]
    for i in range(versions):
        f.write_bytes(blend_bytes(i))
        snap = store.add(f)
        if snap is not None:
            store._db.execute(
                "UPDATE snapshots SET taken_at=? WHERE id=?", (now - ago[versions - 1 - i], snap.id)
            )
            store._db.commit()
    store.close()
    cfg = Config(watch_dirs=[str(watch)], store_dir=str(root / "store"))
    return AppController(cfg, root / "config.json")


def with_toast(pix, title: str, text: str):
    img = pix.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    rect = QRectF(img.width() - 330, img.height() - 96, 310, 76)
    p.setPen(QColor("#52c7b9"))
    p.setBrush(QColor("#1d2128"))
    p.drawRoundedRect(rect, 8, 8)
    p.setPen(QColor("#ffffff"))
    font = QFont(theme.ui_font())
    font.setBold(True)
    p.setFont(font)
    p.drawText(rect.adjusted(16, 10, -10, -38), Qt.AlignmentFlag.AlignVCenter, title)
    font.setBold(False)
    p.setFont(font)
    p.setPen(QColor("#b9c2cc"))
    p.drawText(rect.adjusted(16, 36, -10, -8), Qt.AlignmentFlag.AlignVCenter, text)
    p.end()
    return img


def shot(win: MainWindow, app: QApplication):
    """見た目を整えて撮る（デモの Linux のパスを、Windows のパスに見せる）。"""
    text = win.subtitle.text().split("\n", 1)
    win.subtitle.setText("D:\\Blender\\ProjectA\\character_rig.blend\n" + text[-1])
    app.processEvents()
    return win.grab()


def to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    from io import BytesIO

    return Image.open(BytesIO(bytes(buf.data()))).convert("RGB")


def main(out: Path) -> None:
    app = QApplication([])
    theme.apply_theme(app)
    app.setFont(theme.ui_font())
    frames: list[tuple[Image.Image, int]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for versions in range(1, 5):
            ctl = build(Path(tmp) / f"v{versions}", versions)
            win = MainWindow(ctl)
            win.resize(960, 640)
            win.show()
            ctl.start()
            app.processEvents()
            toast = with_toast(
                shot(win, app), "履歴を作成しました", "character_rig.blend（保存のたびに自動）"
            )
            frames.append((to_pil(toast), 1400))
            frames.append((to_pil(shot(win, app).toImage()), 1000))
            if versions == 4:
                win.history.clearSelection()
                win.history.item(2).setSelected(True)
                app.processEvents()
                frames.append((to_pil(shot(win, app).toImage()), 2600))
            ctl.shutdown()
    first, *rest = (f for f, _ in frames)
    first.save(
        out,
        save_all=True,
        append_images=rest,
        duration=[d for _, d in frames],
        loop=0,
        optimize=True,
    )
    print(f"{out} ({out.stat().st_size // 1024} KB, {len(frames)} frames)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
