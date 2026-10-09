"""二重起動の防止。

最初に起動したものだけが監視を行う。2つ目を起動したときは、最初のものに「画面を出して」と
伝えて、自分は終了する（自動開始のあとに、手動で起動した場合など）。
ロックは QLockFile で行い、異常終了したあとの古いロックは、Qt が自動で取り除く。
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class SingleInstance(QObject):
    messageReceived = Signal(str)

    def __init__(self, name: str, lock_dir: Path | None = None) -> None:
        super().__init__()
        self.name = name
        base = lock_dir or Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation))
        base.mkdir(parents=True, exist_ok=True)
        self._lock = QLockFile(str(base / f"{name}.lock"))
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)
        self._primary = False

    # --- 最初の起動 -----------------------------------------------------------

    def acquire(self) -> bool:
        """最初の起動なら True。すでに起動しているものがあれば False。"""
        if not self._lock.tryLock(0):
            return False
        QLocalServer.removeServer(self.name)  # 異常終了で残った古いソケットを消す
        if not self._server.listen(self.name):
            self._lock.unlock()
            return False
        self._primary = True
        return True

    def release(self) -> None:
        if self._primary:
            self._server.close()
            self._lock.unlock()
            self._primary = False

    def _on_connection(self) -> None:
        while (socket := self._server.nextPendingConnection()) is not None:
            socket.readyRead.connect(lambda s=socket: self._read(s))
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        text = bytes(socket.readAll().data()).decode("utf-8", errors="replace").strip()
        socket.disconnectFromServer()
        if text:
            self.messageReceived.emit(text)

    # --- 2つ目の起動 ----------------------------------------------------------

    def notify_primary(self, message: str = "show", timeout_ms: int = 3000) -> bool:
        """最初に起動したものへ、メッセージを送る。届いたら True。"""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if socket.waitForConnected(500):
                socket.write(message.encode("utf-8"))
                sent = socket.waitForBytesWritten(1000)
                socket.disconnectFromServer()
                return sent
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)  # 最初のものが、受け付けを始めるまで少し待つ
