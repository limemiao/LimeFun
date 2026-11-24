"""Full LimeFun client in a native WebView2 window (no browser chrome)."""
from __future__ import annotations

import socket
import subprocess
import time
from pathlib import Path

import webview

from project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
URL = "http://127.0.0.1:8765/"


def hidden_process_options() -> dict:
    """Return Windows subprocess flags that keep the console service invisible."""
    options = {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        options["startupinfo"] = startupinfo
    return options


def ready() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8765), timeout=0.4):
            return True
    except OSError:
        return False


def main() -> int:
    server = None
    if not ready():
        subprocess.run(["taskkill", "/IM", "LimeFunServer.exe", "/F"], **hidden_process_options())
        server = subprocess.Popen(
            [str(ROOT / "runtime" / "current" / "LimeFunServer.exe")],
            cwd=ROOT,
            **hidden_process_options(),
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not ready():
            time.sleep(0.3)
    webview.create_window("LimeFun 本地番剧播放器", URL, width=1500, height=950, resizable=True, text_select=True)
    try:
        webview.start(gui="edgechromium", debug=False)
    finally:
        if server and server.poll() is None:
            server.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
