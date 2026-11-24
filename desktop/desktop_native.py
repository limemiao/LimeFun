"""Native Qt desktop client for LimeFun; no browser window is used."""

from __future__ import annotations

import json
import importlib.util
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# PySide6 native DLLs need an explicit search directory on Windows, especially
# when the application is launched from a PyInstaller one-file extraction dir.
if os.name == "nt":
    _bundle_dir = Path(getattr(sys, "_MEIPASS", ""))
    _dll_dirs = [
        _bundle_dir,
        _bundle_dir / "PySide6",
    _bundle_dir / "shiboken6",
        Path(sys.prefix) / "Lib" / "site-packages" / "PySide6",
        Path(sys.prefix) / "Lib" / "site-packages" / "shiboken6",
    ]
    for _module_name in ("PySide6", "shiboken6"):
        try:
            _spec = importlib.util.find_spec(_module_name)
            if _spec and _spec.submodule_search_locations:
                _dll_dirs.extend(Path(p) for p in _spec.submodule_search_locations)
        except (ImportError, AttributeError):
            pass
    for _dll_dir in _dll_dirs:
        if _dll_dir.exists():
            os.environ["PATH"] = str(_dll_dir) + os.pathsep + os.environ.get("PATH", "")
            try:
                os.add_dll_directory(str(_dll_dir))
            except (FileNotFoundError, OSError):
                pass

try:
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtGui import QAction, QIcon
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
    from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QTabWidget, QVBoxLayout, QWidget,
    )
except ImportError:
    from desktop.desktop_tk import main as fallback_main
    if __name__ == "__main__":
        raise SystemExit(fallback_main())
    raise


from limefun.project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
HOST = "127.0.0.1"
PORT = 8765
BASE = f"http://{HOST}:{PORT}"


def normalized_title(value: str) -> str:
    return re.sub(r"[\s_·・:：\-—]", "", str(value).lower())


def wait_port(timeout: float = 20.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.4):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def api(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def run_server() -> int:
    try:
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
    except Exception as exc:
        (ROOT / "native_server_error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return 1
    try:
        server.serve_forever()
    finally:
        server.download_manager.shutdown()
        server.catalog_sync.shutdown()
        server.server_close()
    return 0


class LimeFunWindow(QMainWindow):
    def __init__(self, server_process: subprocess.Popen | None):
        super().__init__()
        self.server_process = server_process
        self.catalog: dict = {"items": [], "facets": {}}
        self.library: dict = {"roots": []}
        self.current_series: dict | None = None
        self.setWindowTitle("LimeFun 本地番剧播放器")
        self.setWindowIcon(QIcon(str(ROOT / "desktop" / "limefun.ico")))
        self.resize(1440, 900)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #121716; color: #e7efea; font-family: 'Microsoft YaHei'; }
            QLineEdit, QComboBox, QListWidget { background: #1b2421; border: 1px solid #34433d; padding: 8px; color: #e7efea; }
            QPushButton { background: #8ed1b0; color: #102019; border: 0; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #b2e4c9; }
            QTabBar::tab { background: #1b2421; padding: 12px 22px; color: #9eb2a7; }
            QTabBar::tab:selected { background: #26352f; color: #e7efea; }
            QListWidget::item { padding: 10px; border-bottom: 1px solid #293631; }
            QListWidget::item:selected { background: #315344; }
        """)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.build_search(), "查看 / 搜索")
        self.tabs.addTab(self.build_player(), "播放")
        self.tabs.addTab(self.build_downloads(), "下载中心")
        self.setCentralWidget(self.tabs)
        self.load_catalog()
        self.load_library()

    def build_search(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page)
        heading = QHBoxLayout(); title = QLabel("番剧目录"); title.setStyleSheet("font-size: 24px; font-weight: 700;")
        self.search = QLineEdit(); self.search.setPlaceholderText("搜索番剧名称或 ID"); self.search.textChanged.connect(self.render_catalog)
        refresh = QPushButton("刷新目录"); refresh.clicked.connect(self.load_catalog)
        heading.addWidget(title); heading.addStretch(); heading.addWidget(self.search); heading.addWidget(refresh); layout.addLayout(heading)
        filters = QHBoxLayout(); self.year = QComboBox(); self.category = QComboBox(); self.local = QComboBox()
        self.local.addItems(["全部本地状态", "本地已有", "本地没有"])
        for combo in (self.year, self.category, self.local): combo.currentTextChanged.connect(self.render_catalog)
        filters.addWidget(QLabel("年份")); filters.addWidget(self.year); filters.addWidget(QLabel("类别")); filters.addWidget(self.category); filters.addWidget(self.local); filters.addStretch(); layout.addLayout(filters)
        self.catalog_list = QListWidget(); self.catalog_list.itemDoubleClicked.connect(self.open_catalog_item); layout.addWidget(self.catalog_list)
        return page

    def build_player(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); split = QSplitter(Qt.Horizontal)
        left = QWidget(); left_l = QVBoxLayout(left); left_l.addWidget(QLabel("本地播放列表")); self.series_list = QListWidget(); self.series_list.itemClicked.connect(self.select_series); left_l.addWidget(self.series_list); split.addWidget(left)
        right = QWidget(); right_l = QVBoxLayout(right); self.video = QVideoWidget(); self.video.setMinimumHeight(420); right_l.addWidget(self.video)
        self.player_title = QLabel("选择番剧和集数开始播放"); right_l.addWidget(self.player_title); self.episodes = QListWidget(); self.episodes.itemClicked.connect(self.play_episode); right_l.addWidget(self.episodes); split.addWidget(right); split.setStretchFactor(1, 3); layout.addWidget(split)
        self.media = QMediaPlayer(self); self.audio = QAudioOutput(self); self.media.setAudioOutput(self.audio); self.media.setVideoOutput(self.video)
        return page

    def build_downloads(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.addWidget(QLabel("下载中心")); self.download_list = QListWidget(); layout.addWidget(self.download_list); refresh = QPushButton("刷新任务"); refresh.clicked.connect(self.load_downloads); layout.addWidget(refresh); return page

    def load_catalog(self) -> None:
        try:
            self.catalog = api("/api/catalog?refresh=1")
            self.year.clear(); self.year.addItem("全部年份"); self.year.addItems([str(v) for v in self.catalog.get("facets", {}).get("years", [])])
            self.category.clear(); self.category.addItem("全部类别"); self.category.addItems(self.catalog.get("facets", {}).get("categories", [])); self.render_catalog()
        except Exception as exc: QMessageBox.warning(self, "目录读取失败", str(exc))

    def render_catalog(self) -> None:
        self.catalog_list.clear(); query = self.search.text().lower(); year = self.year.currentText(); category = self.category.currentText(); local = self.local.currentText()
        for item in self.catalog.get("items", []):
            name = str(item.get("vod_name", ""));
            if query and query not in name.lower() and query != str(item.get("id")): continue
            if year and year != "全部年份" and str(item.get("vod_year", "")) != year: continue
            if category and category != "全部类别" and category not in item.get("categories", []): continue
            if local == "本地已有" and not item.get("local"): continue
            if local == "本地没有" and item.get("local"): continue
            row = QListWidgetItem(f"{name}\n{item.get('vod_year','年份未知')}  ·  {'本地已有 ' + str(item.get('local_count')) + ' 集' if item.get('local') else '本地未收藏'}")
            row.setData(Qt.UserRole, item); self.catalog_list.addItem(row)

    def load_library(self) -> None:
        try:
            self.library = api("/api/library?refresh=1"); self.series_list.clear()
            for root in self.library.get("roots", []):
                for series in root.get("series", []):
                    row = QListWidgetItem(f"{series['name']}  ({series.get('count', 0)} 集)"); row.setData(Qt.UserRole, (root, series)); self.series_list.addItem(row)
        except Exception as exc: QMessageBox.warning(self, "本地库读取失败", str(exc))

    def find_library_series(self, item: dict) -> tuple[dict, dict] | None:
        """Resolve a catalog entry to the local series it belongs to."""
        entries = [
            (root, series)
            for root in self.library.get("roots", [])
            for series in root.get("series", [])
        ]
        vod_id = str(item.get("id", ""))
        if vod_id:
            for root, series in entries:
                ids = series.get("catalog_ids") or ([series.get("catalog_id")] if series.get("catalog_id") is not None else [])
                if vod_id in {str(value) for value in ids}:
                    return root, series
        wanted = normalized_title(str(item.get("vod_name", "")))
        if not wanted:
            return None
        for root, series in entries:
            for value in (series.get("name"), series.get("display_name")):
                if value and normalized_title(str(value)) == wanted:
                    return root, series
        for root, series in entries:
            value = normalized_title(str(series.get("name", "")))
            if value and (value in wanted or wanted in value):
                return root, series
        return None

    def open_catalog_item(self, row: QListWidgetItem) -> None:
        item = row.data(Qt.UserRole)
        if not item.get("local"): return
        found = self.find_library_series(item)
        if not found: return
        root, series = found
        self.tabs.setCurrentIndex(1)
        for i in range(self.series_list.count()):
            candidate = self.series_list.item(i)
            candidate_root, candidate_series = candidate.data(Qt.UserRole)
            if candidate_root.get("id") == root.get("id") and candidate_series.get("id") == series.get("id"):
                self.series_list.setCurrentItem(candidate); self.select_series(candidate); return

    def select_series(self, row: QListWidgetItem) -> None:
        self.current_series = row.data(Qt.UserRole)[1]; self.episodes.clear()
        for season in self.current_series.get("seasons", []):
            for episode in season.get("episodes", []):
                item = QListWidgetItem(f"{season['name']} · {episode.get('name', episode.get('filename'))}"); item.setData(Qt.UserRole, episode); self.episodes.addItem(item)

    def play_episode(self, row: QListWidgetItem) -> None:
        episode = row.data(Qt.UserRole); self.player_title.setText(row.text()); self.media.setSource(QUrl(BASE + episode["url"])); self.media.play()

    def load_downloads(self) -> None:
        try:
            data = api("/api/downloads"); self.download_list.clear()
            for task in data.get("tasks", []) + data.get("history", []): self.download_list.addItem(f"{task.get('vod_name')} · {task.get('episode_name')} · {task.get('status')} · {task.get('progress', 0):.0%}")
        except Exception as exc: QMessageBox.warning(self, "下载状态读取失败", str(exc))

    def closeEvent(self, event) -> None:
        self.media.stop()
        if self.server_process and self.server_process.poll() is None: self.server_process.terminate()
        event.accept()


def main() -> int:
    server = None
    if not wait_port(0.2):
        if getattr(sys, "frozen", False): server = subprocess.Popen([str(ROOT / "runtime" / "current" / "LimeFunServer.exe")], cwd=ROOT)
        else: server = subprocess.Popen([sys.executable, "-m", "limefun.player_server", "--host", HOST, "--port", str(PORT)], cwd=ROOT)
        if not wait_port(): raise RuntimeError("本地播放器服务启动失败")
    app = QApplication(sys.argv); app.setApplicationName("LimeFun")
    window = LimeFunWindow(server); window.show(); return app.exec()


if __name__ == "__main__":
    try:
        raise SystemExit(run_server() if "--server" in sys.argv[1:] else main())
    except Exception as exc:
        (ROOT / "desktop_native_error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
