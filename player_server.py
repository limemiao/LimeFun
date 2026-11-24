#!/usr/bin/env python3
"""Local media index and HTTP Range server for the LimeFun player."""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, unquote, urlparse

from download_manager import DownloadManager
from remote_sync import RemoteCatalogSync


APP_ROOT = Path(__file__).resolve().parent
CATALOG_FILE = APP_ROOT / "mutefun_catalog_full.json"
COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov"}
EPISODE_RE = re.compile(r"第\s*0*(\d+)\s*集", re.IGNORECASE)
NATURAL_RE = re.compile(r"(\d+)")


def natural_key(value: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in NATURAL_RE.split(value)]


def ascii_safe(value: str) -> str:
    """Return text that is safe inside a HTTP status line.

    ``http.server`` encodes the reason phrase of a response with latin-1, so a
    localized (for example Chinese) exception message would otherwise make the
    error handler itself raise UnicodeEncodeError.
    """
    return value.encode("latin-1", "replace").decode("latin-1")


def episode_number(name: str) -> int | None:
    match = EPISODE_RE.search(name)
    return int(match.group(1)) if match else None


def episode_sort_key(item: dict[str, object]) -> tuple[object, ...]:
    number = item["number"]
    return (0, number, natural_key(str(item["name"]))) if number is not None else (1, natural_key(str(item["name"])))


class LibraryIndex:
    def __init__(self, roots: dict[str, Path]):
        self.roots = roots
        self._lock = threading.Lock()
        self._data: dict[str, object] | None = None
        self._generated_at = 0.0

    def get(self, refresh: bool = False) -> dict[str, object]:
        if not refresh and self._data is not None and time.time() - self._generated_at < 30:
            return self._data
        with self._lock:
            if not refresh and self._data is not None and time.time() - self._generated_at < 30:
                return self._data
            self._data = self._scan()
            self._generated_at = time.time()
            return self._data

    def _scan(self) -> dict[str, object]:
        root_payloads: list[dict[str, object]] = []
        for root_id, root_path in self.roots.items():
            root_payloads.append(self._scan_root(root_id, root_path))
        return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "roots": root_payloads}

    def _scan_root(self, root_id: str, root_path: Path) -> dict[str, object]:
        series_map: dict[str, dict[str, list[Path]]] = {}
        if root_path.exists():
            for current, _dirs, files in os.walk(root_path):
                video_files = [
                    Path(current) / name
                    for name in files
                    if Path(name).suffix.casefold() in VIDEO_EXTENSIONS
                ]
                if not video_files:
                    continue
                current_path = Path(current)
                relative = current_path.relative_to(root_path)
                parts = relative.parts
                series_name = parts[0] if parts else "未分类"
                season_name = " / ".join(parts[1:]) if len(parts) > 1 else "本目录"
                series_map.setdefault(series_name, {}).setdefault(season_name, []).extend(video_files)

        series_payloads = []
        for series_name in sorted(series_map, key=natural_key):
            season_payloads = []
            for season_name in sorted(series_map[series_name], key=natural_key):
                episodes = []
                for file_path in sorted(series_map[series_name][season_name], key=lambda p: natural_key(p.name)):
                    relative_path = file_path.relative_to(root_path).as_posix()
                    stat = file_path.stat()
                    episodes.append(
                        {
                            "id": relative_path,
                            "name": file_path.stem,
                            "filename": file_path.name,
                            "number": episode_number(file_path.stem),
                            "size": stat.st_size,
                            "modified": int(stat.st_mtime),
                            "url": f"/media?root={root_id}&path={quote(relative_path, safe='/')}",
                        }
                    )
                episodes.sort(key=episode_sort_key)
                season_id = f"{series_name}/{season_name}"
                season_payloads.append(
                    {"id": season_id, "name": season_name, "count": len(episodes), "episodes": episodes}
                )
            series_id = f"{root_id}:{series_name}"
            series_payloads.append(
                {
                    "id": series_id,
                    "name": series_name,
                    "count": sum(int(season["count"]) for season in season_payloads),
                    "seasons": season_payloads,
                }
            )
        return {
            "id": root_id,
            "label": "番剧" if root_id == "archive" else "新番",
            "path": str(root_path),
            "count": sum(int(series["count"]) for series in series_payloads),
            "series": series_payloads,
        }

    def resolve_media(self, root_id: str, relative_path: str) -> Path:
        root = self.roots.get(root_id)
        if root is None:
            raise FileNotFoundError("unknown media root")
        parsed = PurePosixPath(unquote(relative_path))
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in str(parsed):
            raise PermissionError("invalid media path")
        candidate = (root / Path(*parsed.parts)).resolve()
        root_resolved = root.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            raise PermissionError("media path is outside configured root") from exc
        if candidate.suffix.casefold() not in VIDEO_EXTENSIONS or not candidate.is_file():
            raise FileNotFoundError("media file not found")
        return candidate


def normalized_title(value: str) -> str:
    value = html.unescape(value).casefold()
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE)


def titles_similar(left: str, right: str) -> bool:
    """Allow catalog/local names to differ by suffixes, punctuation, or wording."""
    if not left or not right:
        return False
    if left in right or right in left:
        return min(len(left), len(right)) >= 4
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    for size in range(min(len(shorter), 8), 3, -1):
        for index in range(len(shorter) - size + 1):
            if shorter[index:index + size] in longer:
                return True
    return False


MANUAL_LOCAL_ALIASES = {
    "86 -不存在的战区": "86 -不存在的战区 合集",
    "请求女神让我转生成勇者的肋骨": "女神“异世界转生想成为什么”我“勇者的肋骨”",
    "朝花夕誓": "朝花夕誓——于离别之朝束起约定之花",
    "狼与香辛料 重制版": "狼与香辛料 行商邂逅贤狼",
    "白色相簿 第二季": "白色相簿2",
}

# Serializes "decorate the cached library payload -> serialize it" so the shared
# cached dict is never mutated while another request is encoding it.
LIBRARY_VIEW_LOCK = threading.Lock()


class CatalogIndex:
    def __init__(self, library: LibraryIndex, catalog_file: Path, cover_root: Path):
        self.library = library
        self.catalog_file = catalog_file
        self.cover_root = cover_root
        self._lock = threading.Lock()
        self._data: dict[str, object] | None = None
        self._generated_at = 0.0

    def get(self, refresh: bool = False) -> dict[str, object]:
        if not refresh and self._data is not None and time.time() - self._generated_at < 30:
            return self._data
        with self._lock:
            if not refresh and self._data is not None and time.time() - self._generated_at < 30:
                return self._data
            self._data = self._build(refresh)
            self._generated_at = time.time()
            return self._data

    def _build(self, refresh: bool) -> dict[str, object]:
        if not self.catalog_file.exists():
            return {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "items": [],
                "series_map": {},
                "facets": {"years": [], "categories": []},
            }
        with self.catalog_file.open(encoding="utf-8") as stream:
            source = json.load(stream)
        library = self.library.get(refresh)
        local_by_name: dict[str, int] = {}
        local_aliases: dict[str, tuple[int, str]] = {}
        local_series: list[dict[str, object]] = []
        for root in library["roots"]:  # type: ignore[index]
            for series in root["series"]:  # type: ignore[index]
                key = normalized_title(str(series["name"]))
                local_by_name[key] = local_by_name.get(key, 0) + int(series["count"])
                alias_target = normalized_title(MANUAL_LOCAL_ALIASES.get(str(series["name"]), ""))
                if alias_target:
                    local_aliases[alias_target] = (int(series["count"]), key)
                local_series.append({"key": key, "root": root, "series": series})
                for season in series["seasons"]:  # type: ignore[index]
                    season_key = normalized_title(f"{series['name']}{season['name']}")
                    local_aliases[season_key] = (int(season["count"]), key)
                    season_alias_target = normalized_title(MANUAL_LOCAL_ALIASES.get(str(season["name"]), ""))
                    if season_alias_target:
                        local_aliases[season_alias_target] = (int(season["count"]), key)
                    # Some libraries repeat the series name in the season directory
                    # (for example "【我推的孩子】 第二季"), so the season name
                    # itself is also a valid catalog alias.
                    local_aliases[normalized_title(str(season["name"]))] = (int(season["count"]), key)
        items: list[dict[str, object]] = []
        matched_local: set[str] = set()
        # Reverse view of the local matching above: normalized local series name
        # -> the catalog entry that represents it. The player page uses it to
        # jump from a catalog card straight to the matching local series.
        series_map: dict[str, dict[str, object]] = {}
        series_map_exact: set[str] = set()
        years: set[str] = set()
        categories: set[str] = set()
        for raw in source:
            item = dict(raw)
            item["vod_name"] = html.unescape(str(item.get("vod_name", ""))).strip()
            year = str(item.get("vod_year") or "").strip()
            item["vod_year"] = year or None
            if year:
                years.add(year)
            class_names = [part.strip() for part in re.split(r"[,，、]", str(item.get("vod_class") or "")) if part.strip()]
            item["categories"] = class_names
            categories.update(class_names)
            item_key = normalized_title(str(item["vod_name"]))
            local_count = local_by_name.get(item_key, 0)
            matched_series_key = item_key if local_count else None
            if not local_count and item_key in local_aliases:
                local_count, matched_series_key = local_aliases[item_key]
            if local_count:
                series_key = matched_series_key or item_key
                matched_local.add(series_key)
                entry = series_map.get(series_key)
                if entry is None:
                    entry = {"id": item["id"], "vod_name": item["vod_name"], "vod_year": item.get("vod_year"), "ids": []}
                    series_map[series_key] = entry
                elif item_key == series_key and series_key not in series_map_exact:
                    # A catalog entry named exactly like the local series is the
                    # better label than a season or alias entry for the same
                    # series.
                    entry["id"] = item["id"]
                    entry["vod_name"] = item["vod_name"]
                    entry["vod_year"] = item.get("vod_year")
                if item_key == series_key:
                    series_map_exact.add(series_key)
                entry["ids"].append(item["id"])  # type: ignore[union-attr]
            item["local"] = local_count > 0
            item["local_count"] = local_count
            cover_file = str(item.get("cover_file") or "")
            if cover_file and (self.cover_root / cover_file).is_file():
                item["cover_url"] = "/covers/" + quote(cover_file)
            else:
                item["cover_url"] = None
            items.append(item)
        # Some locally archived series never existed in the remote catalog.
        # Keep them visible and searchable under the explicit "其他" category.
        for record in local_series:
            if record["key"] in matched_local:
                continue
            root = record["root"]
            series = record["series"]
            name = str(series["name"])
            items.append(
                {
                    "id": f"local:{root['id']}:{name}",
                    "vod_name": name,
                    "vod_year": None,
                    "vod_class": "其他",
                    "categories": ["其他"],
                    "vod_isend": 0,
                    "vod_remarks": "本地目录",
                    "vod_total": 0,
                    "local": True,
                    "local_only": True,
                    "local_count": int(series["count"]),
                    "cover_url": None,
                }
            )
            categories.add("其他")
        items.sort(key=lambda item: natural_key(str(item.get("vod_name", ""))))
        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "items": items,
            "series_map": series_map,
            "facets": {"years": sorted(years, key=natural_key, reverse=True), "categories": sorted(categories, key=natural_key)},
        }


class FavoriteStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.ids: set[str] = set()
        try:
            payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            if isinstance(payload, list):
                self.ids = {str(item) for item in payload}
        except (OSError, ValueError, TypeError):
            self.ids = set()

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {"ids": sorted(self.ids, key=natural_key)}

    def set(self, item_id: str, favorite: bool) -> dict[str, object]:
        with self.lock:
            if favorite:
                self.ids.add(item_id)
            else:
                self.ids.discard(item_id)
            temporary = self.path.with_name(self.path.name + ".part")
            temporary.write_text(json.dumps(sorted(self.ids, key=natural_key), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            os.replace(temporary, self.path)
            return {"id": item_id, "favorite": favorite}


class PlayerHandler(BaseHTTPRequestHandler):
    server_version = "LimeFunPlayer/1.0"
    _response_started = False

    def handle_one_request(self) -> None:  # noqa: N802
        self._response_started = False
        super().handle_one_request()

    def send_response(self, code: int, message: str | None = None) -> None:
        # Track the response state so a later failure can tell whether writing
        # another status line would corrupt an already started response.
        self._response_started = True
        super().send_response(code, message)

    @property
    def index(self) -> LibraryIndex:
        return self.server.library_index  # type: ignore[attr-defined]

    @property
    def static_root(self) -> Path:
        return self.server.static_root  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        # Windowed builds do not have stderr; logging to it would abort the
        # request before the response is sent.
        if sys.stderr is None or self.path.startswith("/media"):
            return
        super().log_message(format, *args)

    def fail(self, exc: BaseException, as_json: bool = False) -> None:
        """Report an unexpected failure without breaking the response itself."""
        message = ascii_safe(str(exc)) or type(exc).__name__
        self.log_error("request failed: %s", message)
        if self._response_started:
            # Headers are already on the wire, so another status line would end
            # up inside the body. Closing the connection is the only safe move.
            self.close_connection = True
            return
        if as_json:
            self.send_json({"error": message}, HTTPStatus.INTERNAL_SERVER_ERROR)
        else:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, message)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/library":
                library = self.index.get(parse_qs(parsed.query).get("refresh") == ["1"])
                with LIBRARY_VIEW_LOCK:
                    self.apply_catalog_display_names(library)
                    self.send_json(library)
            elif parsed.path == "/api/catalog":
                self.send_json(self.server.catalog_index.get(parse_qs(parsed.query).get("refresh") == ["1"]))  # type: ignore[attr-defined]
            elif parsed.path == "/api/downloads":
                self.send_json(self.server.download_manager.snapshot())  # type: ignore[attr-defined]
            elif parsed.path == "/api/favorites":
                self.send_json(self.server.favorite_store.snapshot())  # type: ignore[attr-defined]
            elif parsed.path == "/api/download/options":
                query = parse_qs(parsed.query)
                vod_id = int(query.get("vod_id", [""])[0])
                name = query.get("name", [""])[0]
                payload = self.server.download_manager.get_vod_options(vod_id, name)  # type: ignore[attr-defined]
                for episode in payload.get("episodes", []):
                    episode.pop("source", None)
                self.send_json(payload)
            elif parsed.path == "/api/catalog/sync-status":
                self.send_json(self.server.catalog_sync.snapshot())  # type: ignore[attr-defined]
            elif parsed.path == "/api/health":
                self.send_json({"status": True})
            elif parsed.path == "/media":
                self.serve_media(parse_qs(parsed.query))
            elif parsed.path == "/" or parsed.path == "/index.html":
                self.serve_static("browse.html")
            elif parsed.path == "/player.html":
                self.serve_static("player.html")
            elif parsed.path == "/downloads.html":
                self.serve_static("downloads.html")
            elif parsed.path.startswith("/static/"):
                self.serve_static(unquote(parsed.path.removeprefix("/static/")))
            elif parsed.path.startswith("/covers/"):
                self.serve_cover(unquote(parsed.path.removeprefix("/covers/")))
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
        except Exception as exc:  # keep server alive on malformed requests
            self.fail(exc)

    def apply_catalog_display_names(self, library: dict[str, object]) -> None:
        """Add canonical MuteFun labels and ids without changing filesystem paths."""
        catalog = self.server.catalog_index.get()  # type: ignore[attr-defined]
        names = [str(item.get("vod_name", "")) for item in catalog.get("items", []) if not item.get("local_only")]
        series_map: dict[str, dict[str, object]] = catalog.get("series_map", {}) or {}  # type: ignore[assignment]
        for root in library.get("roots", []):  # type: ignore[union-attr]
            for series in root.get("series", []):  # type: ignore[union-attr]
                series_key = normalized_title(str(series.get("name", "")))
                match = series_map.get(series_key)
                if match:
                    series["display_name"] = match.get("vod_name")
                    series["catalog_id"] = match.get("id")
                    ids = match.get("ids") or []
                    series["catalog_ids"] = [str(item) for item in ids] or ([str(match.get("id"))] if match.get("id") is not None else [])
                else:
                    exact = next((name for name in names if normalized_title(name) == series_key), None)
                    if exact:
                        series["display_name"] = exact
                for season in series.get("seasons", []):  # type: ignore[union-attr]
                    season_key = normalized_title(str(season.get("name", "")))
                    season_match = series_map.get(season_key) or series_map.get(
                        normalized_title(f"{series.get('name', '')}{season.get('name', '')}")
                    )
                    if season_match:
                        season["display_name"] = season_match.get("vod_name")
                        season["catalog_id"] = season_match.get("id")
                    else:
                        exact_season = next((name for name in names if normalized_title(name) == season_key), None)
                        if exact_season:
                            season["display_name"] = exact_season

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/favorites":
                payload = self.read_json()
                item_id = str(payload.get("id", "")).strip()
                favorite = payload.get("favorite")
                if not item_id or not isinstance(favorite, bool):
                    raise ValueError("id and boolean favorite are required")
                self.send_json(self.server.favorite_store.set(item_id, favorite))  # type: ignore[attr-defined]
                return
            if parsed.path == "/api/download":
                payload = self.read_json()
                vod_id = int(payload.get("vod_id"))
                catalog = self.server.catalog_index.get()  # type: ignore[attr-defined]
                item = next((item for item in catalog["items"] if str(item.get("id")) == str(vod_id)), None)  # type: ignore[index]
                if not item or item.get("local_only"):
                    self.send_json({"error": "清单中没有这个可下载的番剧"}, HTTPStatus.BAD_REQUEST)
                    return
                result = self.server.download_manager.enqueue_vod(vod_id, str(item.get("vod_name", "")))  # type: ignore[attr-defined]
                self.send_json(result, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/download/selected":
                payload = self.read_json()
                vod_id = int(payload.get("vod_id"))
                labels = payload.get("episodes")
                if not isinstance(labels, list) or not labels:
                    raise ValueError("episodes must be a non-empty list")
                result = self.server.download_manager.enqueue_selected(vod_id, str(payload.get("vod_name", "")), [str(label) for label in labels])  # type: ignore[attr-defined]
                self.send_json(result, HTTPStatus.ACCEPTED)
                return
            if parsed.path in {"/api/download/tasks/delete", "/api/download/history/delete"}:
                ids = self.read_json().get("ids")
                if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(item, str) or not item.strip() for item in ids):
                    raise ValueError("请选择 1–500 个有效的任务或记录")
                manager = self.server.download_manager
                result = manager.delete_tasks(ids) if parsed.path == "/api/download/tasks/delete" else manager.delete_histories(ids)
                self.send_json(result)
                return
            if parsed.path == "/api/catalog/sync":
                self.server.catalog_sync.request_now()  # type: ignore[attr-defined]
                self.send_json({"status": True}, HTTPStatus.ACCEPTED)
                return
            parts = parsed.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "download"] and parts[3] == "pause":
                if self.server.download_manager.pause(parts[2]):  # type: ignore[attr-defined]
                    self.send_json({"status": True}, HTTPStatus.ACCEPTED)
                else:
                    self.send_json({"error": "任务不存在或当前不可暂停"}, HTTPStatus.BAD_REQUEST)
                return
            if len(parts) == 4 and parts[:2] == ["api", "download"] and parts[3] == "resume":
                if self.server.download_manager.resume(parts[2]):  # type: ignore[attr-defined]
                    self.send_json({"status": True}, HTTPStatus.ACCEPTED)
                else:
                    self.send_json({"error": "任务不存在或当前不可继续"}, HTTPStatus.BAD_REQUEST)
                return
            if len(parts) == 5 and parts[:3] == ["api", "download", "history"] and parts[4] == "retry":
                if self.server.download_manager.retry_history(parts[3]):  # type: ignore[attr-defined]
                    self.send_json({"status": True}, HTTPStatus.ACCEPTED)
                else:
                    self.send_json({"error": "记录不存在或对应任务当前不可重试"}, HTTPStatus.BAD_REQUEST)
                return
            if len(parts) == 5 and parts[:3] == ["api", "download", "history"] and parts[4] == "delete":
                if self.server.download_manager.delete_history(parts[3]):  # type: ignore[attr-defined]
                    self.send_json({"status": True})
                else:
                    self.send_json({"error": "下载记录不存在"}, HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 4 and parts[:2] == ["api", "download"] and parts[3] == "retry":
                if self.server.download_manager.retry(parts[2]):  # type: ignore[attr-defined]
                    self.send_json({"status": True}, HTTPStatus.ACCEPTED)
                else:
                    self.send_json({"error": "任务不存在或当前不可重试"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_error(HTTPStatus.NOT_FOUND)
        except (ValueError, TypeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.fail(exc, as_json=True)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 64 * 1024:
            raise ValueError("request body too large")
        body = self.rfile.read(length)
        value = json.loads(body.decode("utf-8")) if body else {}
        if not isinstance(value, dict):
            raise ValueError("JSON body must be an object")
        return value

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/media":
            try:
                self.serve_media(parse_qs(parsed.query), head_only=True)
            except (PermissionError, FileNotFoundError):
                self.send_error(HTTPStatus.NOT_FOUND)
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_static(self, relative: str) -> None:
        target = (self.static_root / Path(relative)).resolve()
        try:
            target.relative_to(self.static_root.resolve())
        except ValueError as exc:
            raise PermissionError("invalid static path") from exc
        if not target.is_file():
            raise FileNotFoundError(relative)
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def serve_cover(self, relative: str) -> None:
        cover_root = self.server.cover_root  # type: ignore[attr-defined]
        target = (cover_root / Path(relative)).resolve()
        try:
            target.relative_to(cover_root.resolve())
        except ValueError as exc:
            raise PermissionError("invalid cover path") from exc
        if target.suffix.casefold() not in COVER_EXTENSIONS or not target.is_file():
            raise FileNotFoundError(relative)
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(body)

    def serve_media(self, query: dict[str, list[str]], head_only: bool = False) -> None:
        root_id = query.get("root", [""])[0]
        relative = query.get("path", [""])[0]
        target = self.index.resolve_media(root_id, relative)
        size = target.stat().st_size
        start, end = 0, size - 1
        range_header = self.headers.get("Range")
        partial = False
        if range_header and range_header.startswith("bytes="):
            value = range_header[6:].split(",", 1)[0].strip()
            if "-" not in value:
                self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                return
            first, last = value.split("-", 1)
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                length = int(last)
                start = max(0, size - length)
            if start >= size or start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            end = min(end, size - 1)
            partial = True
        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if head_only:
            return
        try:
            with target.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # The player closed the connection (seek, close, tab switch). That is
            # normal traffic, not a server error worth reporting.
            self.close_connection = True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LimeFun 本地番剧播放浏览器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--archive", default=r"D:\Anime", help="归档番剧目录")
    parser.add_argument("--new", dest="new_root", default=r"D:\NewAnime", help="新番目录")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    roots = {"archive": Path(args.archive), "new": Path(args.new_root)}
    server = ThreadingHTTPServer((args.host, args.port), PlayerHandler)
    server.library_index = LibraryIndex(roots)  # type: ignore[attr-defined]
    server.static_root = APP_ROOT / "player_web"  # type: ignore[attr-defined]
    server.cover_root = APP_ROOT / "covers"  # type: ignore[attr-defined]
    server.catalog_index = CatalogIndex(server.library_index, CATALOG_FILE, server.cover_root)  # type: ignore[attr-defined]
    server.favorite_store = FavoriteStore(APP_ROOT / "favorites.json")  # type: ignore[attr-defined]
    server.catalog_sync = RemoteCatalogSync(CATALOG_FILE)  # type: ignore[attr-defined]
    server.download_manager = DownloadManager(roots["archive"], APP_ROOT / "download_state.json", local_roots=roots.values(), title_aliases=MANUAL_LOCAL_ALIASES)  # type: ignore[attr-defined]
    print(f"LimeFun Player: http://{args.host}:{args.port}", flush=True)
    print(f"番剧目录: {roots['archive']}", flush=True)
    print(f"新番目录: {roots['new']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n播放器已停止", flush=True)
    finally:
        server.download_manager.shutdown()  # type: ignore[attr-defined]
        server.catalog_sync.shutdown()  # type: ignore[attr-defined]
        server.server_close()


if __name__ == "__main__":
    main()
