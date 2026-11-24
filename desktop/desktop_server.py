"""Bundled headless LimeFun HTTP service for the native desktop client."""
from pathlib import Path
import sys

from limefun.project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
sys.path.insert(0, str(ROOT))
from limefun import player_server

player_server.APP_ROOT = ROOT
player_server.CATALOG_FILE = ROOT / "mutefun_catalog_full.json"
roots = {"archive": Path(r"D:\Anime"), "new": Path(r"D:\NewAnime")}
server = player_server.ThreadingHTTPServer(("127.0.0.1", 8765), player_server.PlayerHandler)
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
