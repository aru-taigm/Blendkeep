"""アプリのアイコンから blendkeep.ico を作る（Windows の exe 用）。

使い方: QT_QPA_PLATFORM=offscreen python packaging/make_icon.py packaging/blendkeep.ico
"""

import sys

from PySide6.QtGui import QGuiApplication

from blendkeep.gui import theme

app = QGuiApplication([])
image = theme.make_app_icon().pixmap(256, 256).toImage()
if not image.save(sys.argv[1], "ICO"):
    raise SystemExit("ico を書き出せませんでした")
