"""Start LimeFun as a standalone Edge application window."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path


# The media library and web assets are intentionally kept outside the EXE.
# This also makes the one-file build independent of PyInstaller's temp folder.
from limefun.project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}/"


def run_server() -> int:
    sys.path.insert(0, str(ROOT))
    from limefun import player_server

    player_server.APP_ROOT = ROOT
    player_server.CATALOG_FILE = ROOT / "mutefun_catalog_full.json"
    roots = {"archive": Path(r"D:\Anime"), "new": Path(r"D:\NewAnime")}
    server = player_server.ThreadingHTTPServer((HOST, PORT), player_server.PlayerHandler)
    server.library_index = player_server.LibraryIndex(roots)
    server.static_root = ROOT / "limefun" / "player_web"
    server.cover_root = ROOT / "covers"
    server.catalog_index = player_server.CatalogIndex(server.library_index, player_server.CATALOG_FILE, server.cover_root)
    server.favorite_store = player_server.FavoriteStore(ROOT / "favorites.json")
    server.catalog_sync = player_server.RemoteCatalogSync(player_server.CATALOG_FILE)
    server.download_manager = player_server.DownloadManager(roots["archive"], ROOT / "download_state.json", local_roots=roots.values(), title_aliases=player_server.MANUAL_LOCAL_ALIASES)
    try:
        server.serve_forever()
    finally:
        server.download_manager.shutdown()
        server.catalog_sync.shutdown()
        server.server_close()
    return 0


def wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def find_edge() -> str | None:
    candidates = [
        shutil.which("msedge.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    return next((path for path in candidates if path and Path(path).exists()), None)


def main() -> int:
    own_server = False
    server = None
    try:
        if not wait_for_port(HOST, PORT, timeout=0.2):
            if getattr(sys, "frozen", False):
                server = subprocess.Popen([sys.executable, "--server"], cwd=ROOT)
            else:
                python = sys.executable
                log = (ROOT / "desktop_server.log").open("ab")
                server = subprocess.Popen(
                    [python, "-m", "limefun.player_server", "--host", HOST, "--port", str(PORT)],
                    cwd=ROOT,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            own_server = True
            if not wait_for_port(HOST, PORT):
                print("LimeFun 服务启动失败，请检查项目目录中的 desktop_server.log", file=sys.stderr)
                return 1
        edge = find_edge()
        if not edge:
            print(f"找不到 Microsoft Edge，请手动打开 {URL}", file=sys.stderr)
            return 1
        app = subprocess.Popen(
            [edge, f"--app={URL}", "--start-maximized", "--disable-session-crashed-bubble", "--no-first-run", f"--user-data-dir={ROOT / 'edge-profile'}"],
            cwd=ROOT,
        )
        time.sleep(2)
        return 0
    except Exception as exc:
        (ROOT / "desktop_launcher_error.log").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
        )
        return 1
    finally:
        # The frozen server is a detached child that must outlive this
        # short-lived launcher process and the Edge hand-off process.
        if own_server and server is not None and not getattr(sys, "frozen", False):
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()


if __name__ == "__main__":
    raise SystemExit(run_server() if "--server" in sys.argv[1:] else main())
