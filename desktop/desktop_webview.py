"""Full-featured standalone LimeFun window using embedded Qt WebEngine."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from limefun.project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
HOST = "127.0.0.1"
PORT = 8765

if os.name == "nt":
    bundle = Path(getattr(sys, "_MEIPASS", ""))
    dirs = [bundle, bundle / "PySide6", bundle / "shiboken6", Path(sys.prefix) / "Lib" / "site-packages" / "PySide6", Path(sys.prefix) / "Lib" / "site-packages" / "shiboken6"]
    for d in dirs:
        if d.exists():
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(str(d))
            except OSError:
                pass

from PySide6.QtCore import QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMainWindow
from PySide6.QtWebEngineWidgets import QWebEngineView


def ready() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.4):
            return True
    except OSError:
        return False


def main() -> int:
    server = None
    if not ready():
        subprocess.run(["taskkill", "/IM", "LimeFunServer.exe", "/F"], capture_output=True)
        server = subprocess.Popen([str(ROOT / "runtime" / "current" / "LimeFunServer.exe")], cwd=ROOT)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not ready():
            time.sleep(0.3)
    app = QApplication(sys.argv)
    app.setApplicationName("LimeFun")
    app.setWindowIcon(QIcon(str(ROOT / "desktop" / "limefun.ico")))
    window = QMainWindow()
    window.setWindowTitle("LimeFun 本地番剧播放器")
    window.resize(1500, 950)
    view = QWebEngineView(window)
    view.setUrl(QUrl(f"http://{HOST}:{PORT}/"))
    window.setCentralWidget(view)
    def close():
        if server and server.poll() is None:
            server.terminate()
        app.quit()
    app.aboutToQuit.connect(close)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
