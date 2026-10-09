"""配布用の実行ファイル（Nuitka）の入口。パッケージの中の相対 import を避けるための薄い入口。"""

from blendkeep.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
