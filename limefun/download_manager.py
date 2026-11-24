#!/usr/bin/env python3
"""Background MuteFun downloads and workflow-owned history."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


EPISODE_RE = re.compile(r"第\s*0*(\d+)\s*集")
EPISODE_FILE_PATTERNS = (
    EPISODE_RE,
    re.compile(r"(?:^|[^a-z0-9])(?:ep?|e)\s*0*(\d+)(?:[^a-z0-9]|$)", re.IGNORECASE),
    re.compile(r"^\s*0*(\d+)(?:\D|$)"),
)
SEASON_CN_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百]+)\s*(季|期|部(?:分)?)")
SEASON_EN_RE = re.compile(r"(?<![A-Za-z0-9])(?:season|s)\s*0*(\d+)(?![A-Za-z0-9])", re.IGNORECASE)
FINAL_SEASON_RE = re.compile(r"最终季|final\s*season", re.IGNORECASE)
SPECIAL_CN_RE = re.compile(r"特别篇|特別篇|总集篇|總集篇|合集|番外篇|剧场版|劇場版|映画")
SPECIAL_EN_RE = re.compile(r"(?<![A-Za-z0-9])(?:OVA|OAD|SP|movie)(?![A-Za-z0-9])", re.IGNORECASE)
MOVIE_PREFIX_RE = re.compile(r"^(?:剧场版|劇場版|映画|movie)\s*[:：]?\s*", re.IGNORECASE)
FIRST_SEASON_RE = re.compile(r"(?:第\s*[一1]\s*[季期]|(?:season|s)\s*0*1|正片|TV版|本篇)(?:\s|$)", re.IGNORECASE)
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".m4v", ".avi", ".mov"}


class DownloadPaused(Exception):
    """Internal control flow used to stop a transfer without recording a failure."""


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "未命名"


def episode_number(label: str) -> int | None:
    match = EPISODE_RE.search(label)
    return int(match.group(1)) if match else None


def file_episode_number(name: str) -> int | None:
    for pattern in EPISODE_FILE_PATTERNS:
        match = pattern.search(name)
        if match:
            return int(match.group(1))
    return None


def normalized_name(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def number_from_text(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[1:], 0)
    if value.endswith("十"):
        return digits.get(value[:-1], 0) * 10
    if "十" in value:
        tens, ones = value.split("十", 1)
        return digits.get(tens, 0) * 10 + digits.get(ones, 0)
    return digits.get(value, 0)


def season_marker_key(value: str) -> str:
    match = SEASON_CN_RE.search(value)
    if match:
        return f"第{number_from_text(match.group(1))}{match.group(2)}"
    match = SEASON_EN_RE.search(value)
    if match:
        return f"第{int(match.group(1))}季"
    if FINAL_SEASON_RE.search(value):
        return "最终季"
    return ""


def season_suffix_tail(value: str) -> str:
    """Text after the first season/special marker, e.g. 夺还篇 in 第四季 夺还篇."""
    for pattern in (SEASON_CN_RE, SEASON_EN_RE, FINAL_SEASON_RE, SPECIAL_CN_RE, SPECIAL_EN_RE):
        match = pattern.search(value)
        if match:
            return value[match.end():].strip(" ～~:：—-")
    return ""


def is_first_season_suffix(value: str) -> bool:
    match = SEASON_CN_RE.match(value)
    if match:
        if match.group(2) == "部分" or match.group(2) == "部":
            return False
        return number_from_text(match.group(1)) == 1
    match = SEASON_EN_RE.match(value)
    if match:
        return int(match.group(1)) == 1
    return False


class DownloadManager:
    def __init__(self, output_root: Path, state_file: Path, workers: int = 3, local_roots: Any = None, title_aliases: dict[str, str] | None = None):
        self.output_root = output_root
        roots = [Path(root) for root in (local_roots or [output_root])]
        self.local_roots = list(dict.fromkeys([output_root, *roots]))
        self.directory_aliases = {
            normalized_name(catalog_name): normalized_name(local_name)
            for local_name, catalog_name in (title_aliases or {}).items()
        }
        self.state_file = state_file
        self.api = os.environ.get("MUTEFUN_API_BASE", "https://go.5idm.top/app/api").rstrip("/")
        self.aes_key = os.environ.get("MUTEFUN_AES_KEY", "").encode("ascii")
        self.timeout = 30
        self.retries = 3
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.worker_count = max(1, workers)
        self.pool = ThreadPoolExecutor(max_workers=self.worker_count, thread_name_prefix="limefun-download")
        self.tasks: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self._next_queue_order = 0
        self._next_priority_order = 0
        self._last_persist = 0.0
        self._shutdown = False
        self._worker_futures: list[Any] = []
        self._active_runs: dict[str, str] = {}
        self._responses: dict[str, Any] = {}
        self._last_worker_error = ""
        self._last_worker_error_at: float | None = None
        self._load()
        with self.condition:
            self._ensure_workers_locked()
        self._supervisor = threading.Thread(target=self._supervisor_loop, name="limefun-download-supervisor", daemon=True)
        self._supervisor.start()

    def _load(self) -> None:
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self.tasks = data.get("tasks", {})
            self.history = data.get("history", [])[-500:]
            changed = False
            for entry in self.history:
                if not entry.get("history_id"):
                    entry["history_id"] = uuid.uuid4().hex[:16]
                    changed = True
            ordered_tasks = sorted(self.tasks.values(), key=lambda task: task.get("created_at", 0) or 0)
            finished_ids: list[str] = []
            recovered_history: list[dict[str, Any]] = []
            for task in ordered_tasks:
                queue_order = task.get("queue_order")
                if not isinstance(queue_order, int):
                    self._next_queue_order += 1
                    task["queue_order"] = self._next_queue_order
                    changed = True
                else:
                    self._next_queue_order = max(self._next_queue_order, queue_order)
                    self._next_priority_order = min(self._next_priority_order, queue_order)
                if task.get("status") == "completed":
                    # Finished downloads are recorded in history; keeping a copy
                    # in the queue as well would grow this file without bound.
                    if any(entry.get("id") == task.get("id") and entry.get("status") == "completed" for entry in self.history):
                        finished_ids.append(str(task.get("id", "")))
                    else:
                        try:
                            recovered_history.append(self._history_entry(task))
                        except (KeyError, TypeError):
                            continue
                        finished_ids.append(str(task.get("id", "")))
                    changed = True
                    continue
                if task.get("status") == "downloading":
                    task["status"] = "queued"
                    task["error"] = ""
                    task["started_at"] = None
                    task["speed"] = 0.0
                    changed = True
                elif task.get("status") == "pausing":
                    task["status"] = "paused"
                    task["started_at"] = None
                    task["speed"] = 0.0
                    changed = True
                if task.pop("run_token", None) is not None:
                    changed = True
            if recovered_history:
                self.history.extend(recovered_history)
                self.history.sort(key=lambda entry: (entry.get("finished_at") or entry.get("created_at") or 0) if isinstance(entry, dict) else 0)
            if finished_ids:
                for task_id in finished_ids:
                    self.tasks.pop(task_id, None)
                changed = True
            for task_id, task in list(self.tasks.items()):
                if task.get("status") in {"deleting", "delete_failed"}:
                    self._finish_delete_locked(task_id)
                    changed = True
            if changed:
                self._persist_locked(force=True)
        except (OSError, ValueError, TypeError):
            self.tasks = {}
            self.history = []

    def _allocate_queue_order_locked(self) -> int:
        self._next_queue_order += 1
        return self._next_queue_order

    def _allocate_priority_order_locked(self) -> int:
        self._next_priority_order -= 1
        return self._next_priority_order

    def _ensure_workers_locked(self) -> None:
        alive = []
        for future in self._worker_futures:
            if not future.done():
                alive.append(future)
                continue
            try:
                error = future.exception()
            except Exception as exc:
                error = exc
            if error:
                self._last_worker_error = f"{type(error).__name__}: {error}"
                self._last_worker_error_at = time.time()
        if not self._shutdown:
            while len(alive) < self.worker_count:
                alive.append(self.pool.submit(self._worker_loop))
        self._worker_futures = alive

    def _supervisor_loop(self) -> None:
        while True:
            with self.condition:
                if self._shutdown:
                    return
                self._ensure_workers_locked()
                self.condition.wait(timeout=2.0)

    def _recover_worker_exception(self, task_id: str | None, run_token: str | None, error: Exception) -> None:
        try:
            with self.condition:
                self._last_worker_error = f"{type(error).__name__}: {error}"
                self._last_worker_error_at = time.time()
                task = self.tasks.get(task_id or "")
                if task and task.get("run_token") == run_token and task.get("status") in {"downloading", "pausing"}:
                    was_pausing = task.get("status") == "pausing"
                    task["status"] = "paused" if was_pausing else "failed"
                    task["speed"] = 0.0
                    task["started_at"] = None
                    task.pop("run_token", None)
                    if not was_pausing:
                        task["error"] = f"下载线程异常: {error}"
                        task["finished_at"] = time.time()
                        self.history.append(self._history_entry(task))
                    self._persist_locked(force=True)
                self.condition.notify_all()
        except Exception:
            pass

    def _worker_loop(self) -> None:
        while True:
            task_id: str | None = None
            run_token: str | None = None
            try:
                with self.condition:
                    while not self._shutdown:
                        queued = [task for task in self.tasks.values() if task.get("status") == "queued"]
                        if queued:
                            task = min(queued, key=lambda item: item.get("queue_order", 0))
                            task["status"] = "downloading"
                            task["started_at"] = time.time()
                            task["speed"] = 0.0
                            task_id = str(task["id"])
                            run_token = uuid.uuid4().hex
                            task["run_token"] = run_token
                            self._active_runs[run_token] = task_id
                            self._persist_locked(force=True)
                            break
                        self.condition.wait()
                    else:
                        return
                self._run_task(task_id, run_token)
            except Exception as exc:
                self._recover_worker_exception(task_id, run_token, exc)
                time.sleep(0.25)
            finally:
                if run_token:
                    with self.condition:
                        self._active_runs.pop(run_token, None)
                        self._responses.pop(run_token, None)
                        task = self.tasks.get(task_id or "")
                        if task and task.get("status") == "deleting":
                            self._finish_delete_locked(task_id)
                            self._persist_locked(force=True)
                        if task and task.get("run_token") == run_token and task.get("status") == "pausing":
                            task["status"] = "paused"
                            task["speed"] = 0.0
                            task["started_at"] = None
                            task.pop("run_token", None)
                            self._persist_locked(force=True)
                        self.condition.notify_all()

    def _persist_locked(self, force: bool = False, min_interval: float = 0.7) -> None:
        now = time.time()
        if not force and now - self._last_persist < min_interval:
            return
        if len(self.history) > 500:
            self.history = self.history[-500:]
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_name(self.state_file.name + ".part")
        payload = {"tasks": self.tasks, "history": self.history[-500:]}
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temporary, self.state_file)
        self._last_persist = now

    def _history_entry(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "history_id": uuid.uuid4().hex[:16],
            "id": task["id"],
            "vod_id": task["vod_id"],
            "vod_name": task["vod_name"],
            "episode_name": task["episode_name"],
            "filename": task["filename"],
            "status": task["status"],
            "size": task.get("downloaded", 0),
            "error": task.get("error", ""),
            "created_at": task.get("created_at"),
            "finished_at": task.get("finished_at"),
        }

    def _api_json(self, path: str, method: str = "GET", body: bytes | None = None) -> dict[str, Any]:
        headers = {"User-Agent": "LimeFunDownloader/1.0", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.api + path, data=body, headers=headers, method=method)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("code") != 200:
                    raise RuntimeError(str(payload)[:300])
                return self._decrypt(payload["data"])
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(1.5**attempt)
        raise RuntimeError(str(last_error)) from last_error

    def _decrypt(self, value: str) -> dict[str, Any]:
        if not self.aes_key:
            raise RuntimeError("缺少 MUTEFUN_AES_KEY：请设置环境变量后再试")
        ciphertext = base64.b64decode(value)
        decryptor = Cipher(algorithms.AES(self.aes_key), modes.ECB()).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        padding = plaintext[-1]
        return json.loads(plaintext[:-padding].decode("utf-8"))

    @staticmethod
    def _direct_videos(directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        try:
            return [path for path in directory.iterdir() if path.is_file() and path.suffix.casefold() in VIDEO_EXTENSIONS]
        except OSError:
            return []

    def _all_local_directories(self, roots: Any = None) -> list[Path]:
        directories: list[Path] = []
        for root in (roots or self.local_roots):
            if not root.is_dir():
                continue
            directories.append(root)
            try:
                directories.extend(path for path in root.rglob("*") if path.is_dir())
            except OSError:
                continue
        return directories

    @staticmethod
    def _title_markers(value: str) -> list[tuple[int, int]]:
        markers: list[tuple[int, int]] = []
        for pattern in (SEASON_CN_RE, SEASON_EN_RE, FINAL_SEASON_RE, SPECIAL_CN_RE, SPECIAL_EN_RE):
            for match in pattern.finditer(value):
                if match.start() > 0:
                    markers.append((match.start(), match.end()))
        return sorted(markers)

    @staticmethod
    def _strip_normalized_prefix(value: str, prefix: str) -> str:
        target = normalized_name(prefix)
        if not target:
            return value.strip()
        count = 0
        for index, char in enumerate(value):
            part = normalized_name(char)
            if part:
                count += len(part)
            if count >= len(target):
                return value[index + 1:].strip(" ～~:：—-")
        return ""

    @staticmethod
    def _longest_existing_prefix(value: str, target_root: Path, directories: list[Path]) -> str:
        wanted = normalized_name(value)
        best: tuple[int, int, int, str] | None = None
        for path in directories:
            if path == target_root or not path.is_dir():
                continue
            key = normalized_name(path.name)
            if len(key) < 2 or not wanted.startswith(key):
                continue
            score = (1 if path.parent == target_root else 0, len(key), -len(path.parts))
            if best is None or score > best[:3]:
                best = (*score, path.name)
        return best[3] if best else ""

    def _split_series_title(self, series_name: str, target_root: Path, directories: list[Path]) -> tuple[str, str, bool]:
        """Split a catalog title into (base series, suffix, first-season flag).

        Examples:
            入间同学入魔了 第四季       -> (入间同学入魔了, 第四季, False)
            Re:0 第四季 夺还篇          -> (Re:0, 第四季 夺还篇, False)
            剧场版 链锯人 蕾塞篇        -> (链锯人, 剧场版 蕾塞篇, False)
            关于我转生变成史莱姆这档事 OAD -> (关于我转生变成史莱姆这档事, OAD, False)
        """
        prefix = MOVIE_PREFIX_RE.match(series_name)
        if prefix:
            rest = series_name[prefix.end():].strip()
            marker = prefix.group(0).strip()
            if rest:
                base_name = self._longest_existing_prefix(rest, target_root, directories)
                if base_name:
                    remainder = self._strip_normalized_prefix(rest, base_name)
                    suffix = f"{marker} {remainder}".strip() if remainder else marker
                    return base_name, suffix, False
                parts = re.split(r"\s+|[～~：:—–-]", rest, maxsplit=1)
                base_name = parts[0].strip()
                if len(parts) == 1:
                    return base_name or rest, marker, False
                remainder = rest[len(base_name):].lstrip(" ～~:：—-") if rest.startswith(base_name) else parts[1]
                return base_name, f"{marker} {remainder}".strip(), False
        markers = self._title_markers(series_name)
        if markers:
            start, _end = markers[0]
            base_name = series_name[:start].strip()
            suffix = series_name[start:].strip()
            if base_name:
                return base_name, suffix, is_first_season_suffix(suffix)
        return series_name, "", False

    def _find_base_directory(self, base: str, target_root: Path, directories: list[Path]) -> Path | None:
        key = normalized_name(base)
        wanted = {key, self.directory_aliases.get(key, "")}
        candidates = [
            path for path in directories
            if path != target_root and path.is_dir() and normalized_name(path.name) in wanted
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda path: (path.parent == target_root, -len(path.parts), len(self._direct_videos(path))),
        )

    def _find_season_child(self, base_dir: Path, suffix: str, series_name: str) -> Path | None:
        exact_names = {
            normalized_name(suffix),
            normalized_name(series_name),
            normalized_name(base_dir.name + suffix),
            normalized_name(base_dir.name + " " + suffix),
        }
        marker_key = season_marker_key(suffix)
        tail = season_suffix_tail(suffix)
        tail_key = normalized_name(tail)
        candidates: list[Path] = []
        try:
            children = [child for child in base_dir.iterdir() if child.is_dir()]
        except OSError:
            return None
        for child in children:
            child_name = normalized_name(child.name)
            if child_name in exact_names:
                candidates.append(child)
            elif marker_key and not tail and season_marker_key(child.name) == marker_key:
                candidates.append(child)
            elif tail_key and tail_key in child_name:
                candidates.append(child)
        if not candidates:
            return None
        return max(candidates, key=lambda path: (len(self._direct_videos(path)), -len(path.parts)))

    def _target_directory(self, series_name: str, root: Path | None = None) -> Path:
        target_root = root or self.output_root
        wanted = normalized_name(series_name)
        wanted_names = {wanted, self.directory_aliases.get(wanted, "")}
        directories = self._all_local_directories([target_root])
        exact = [path for path in directories if normalized_name(path.name) in wanted_names]
        base, suffix, first_season = self._split_series_title(series_name, target_root, directories)

        if suffix:
            # Reuse an existing directory that already carries the full title.
            if exact:
                base_key = normalized_name(base)
                return max(
                    exact,
                    key=lambda path: (
                        normalized_name(path.parent.name) == base_key,
                        len(self._direct_videos(path)),
                        -len(path.parts),
                    ),
                )
            base_dir = self._find_base_directory(base, target_root, directories)
            if base_dir is None:
                # New base series: first season lives in the base directory,
                # everything else gets a suffix subdirectory.
                return target_root / base if first_season else target_root / base / suffix
            child = self._find_season_child(base_dir, suffix, series_name)
            if child:
                return child
            if first_season:
                try:
                    mains = [
                        child for child in base_dir.iterdir()
                        if child.is_dir() and FIRST_SEASON_RE.search(child.name)
                    ]
                except OSError:
                    mains = []
                if mains:
                    return max(mains, key=lambda path: len(self._direct_videos(path)))
                if self._direct_videos(base_dir):
                    return base_dir
            return base_dir / suffix

        numbered = [
            (sum(file_episode_number(path.stem) is not None for path in self._direct_videos(directory)), directory)
            for directory in exact
        ]
        numbered = [item for item in numbered if item[0]]
        if numbered:
            return max(numbered, key=lambda item: (item[0], len(item[1].parts)))[1]

        # A base-series directory may only contain season subdirectories. Route
        # an unsuffixed catalog title to its existing first-season directory.
        for directory in sorted(exact, key=lambda path: len(path.parts), reverse=True):
            try:
                first_seasons = [child for child in directory.iterdir() if child.is_dir() and FIRST_SEASON_RE.search(child.name)]
            except OSError:
                first_seasons = []
            if first_seasons:
                return max(first_seasons, key=lambda path: len(self._direct_videos(path)))
        if exact:
            return max(exact, key=lambda path: len(path.parts))

        return target_root / series_name

    def _local_episode_state(self, target_dirs: Any) -> tuple[set[int], set[str]]:
        numbers: set[int] = set()
        names: set[str] = set()
        directories = [target_dirs] if isinstance(target_dirs, Path) else target_dirs
        for directory in directories:
            for path in self._direct_videos(directory):
                try:
                    if path.stat().st_size <= 0:
                        continue
                except OSError:
                    continue
                number = file_episode_number(path.stem)
                if number is not None:
                    numbers.add(number)
                names.add(normalized_name(path.stem))
        return numbers, names

    def _episode_options(self, vod_id: int, vod_name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        detail = self._api_json(f"/vod/{vod_id}")
        lines = {str(line.get("player")): line for line in detail.get("playerData", [])}
        player = next((key for key in ("r2", "m2", "v1") if lines.get(key, {}).get("vids")), None)
        if not player:
            raise RuntimeError("没有可用下载线路")
        series_name = safe_filename(vod_name or detail.get("vod_name", f"vod-{vod_id}"))
        target_dir = self._target_directory(series_name)
        local_dirs = list(dict.fromkeys(self._target_directory(series_name, root) for root in self.local_roots))
        local_numbers, local_names = self._local_episode_state(local_dirs)
        with self.lock:
            matching_tasks = [task for task in self.tasks.values() if str(task.get("vod_id")) == str(vod_id)]
            tasks_by_episode = {normalized_name(str(task.get("episode_name", ""))): task for task in matching_tasks}
        options: list[dict[str, Any]] = []
        for raw in lines[player]["vids"]:
            label, separator, source = str(raw).partition("$")
            if not separator or not source:
                continue
            number = episode_number(label)
            title = f"第{number}集" if number is not None else safe_filename(label.strip())
            filename = f"{title}.mp4"
            target = target_dir / filename
            task = tasks_by_episode.get(normalized_name(label.strip()))
            local_name = normalized_name(label.strip())
            target_exists = target.exists() and target.stat().st_size > 0
            locally_downloaded = target_exists or (number is not None and number in local_numbers) or (number is None and local_name in local_names)
            if locally_downloaded:
                status = "downloaded"
            elif task and task.get("status") in {"queued", "downloading"}:
                status = "downloading"
            elif task and task.get("status") in {"pausing", "paused"}:
                status = "paused"
            elif task and task.get("status") == "failed":
                status = "failed"
            else:
                status = "missing"
            options.append({"label": label.strip(), "filename": filename, "source": source, "status": status, "task_id": task.get("id") if task else None})
        return {"player": player, "vod_name": vod_name or detail.get("vod_name", ""), "series_name": series_name, "target_dir": str(target_dir)}, options

    def get_vod_options(self, vod_id: int, vod_name: str) -> dict[str, Any]:
        meta, options = self._episode_options(vod_id, vod_name)
        return {**meta, "episodes": options}

    def enqueue_selected(self, vod_id: int, vod_name: str, labels: list[str]) -> dict[str, Any]:
        meta, options = self._episode_options(vod_id, vod_name)
        wanted = set(str(label) for label in labels)
        created: list[str] = []
        retried: list[str] = []
        skipped = 0
        with self.lock:
            known_targets = {task.get("target") for task in self.tasks.values() if task.get("status") not in {"failed"}}
            for option in options:
                if option["label"] not in wanted:
                    continue
                if option["status"] in {"downloaded", "downloading", "paused"}:
                    skipped += 1
                    continue
                if option["status"] == "failed" and option.get("task_id"):
                    task = self.tasks.get(str(option["task_id"]))
                    if not task or task.get("status") != "failed":
                        skipped += 1
                        continue
                    target = Path(meta["target_dir"]) / option["filename"]
                    task["status"] = "queued"
                    task["progress"] = 0.0
                    task["downloaded"] = 0
                    task["total"] = 0
                    task["speed"] = 0.0
                    task["error"] = ""
                    task["finished_at"] = None
                    task["filename"] = option["filename"]
                    task["target"] = str(target)
                    task["source_url"] = option["source"]
                    task["player"] = meta["player"]
                    task["queue_order"] = self._allocate_priority_order_locked()
                    retried.append(str(option["task_id"]))
                    continue
                target = Path(meta["target_dir"]) / option["filename"]
                if str(target) in known_targets:
                    skipped += 1
                    continue
                player = meta["player"]
                source = option["source"]
                if not source:
                    continue
                task_id = uuid.uuid4().hex[:12]
                task = {"id": task_id, "vod_id": vod_id, "vod_name": meta["vod_name"], "episode_name": option["label"], "filename": option["filename"], "target": str(target), "source_url": source, "player": player, "status": "queued", "progress": 0.0, "downloaded": 0, "total": 0, "speed": 0.0, "error": "", "created_at": time.time(), "queue_order": self._allocate_queue_order_locked(), "started_at": None, "finished_at": None}
                self.tasks[task_id] = task
                created.append(task_id)
            self._persist_locked(force=True)
            self.condition.notify_all()
        return {"created": len(created), "retried": len(retried), "skipped": skipped, "task_ids": created + retried, "player": meta["player"]}

    def enqueue_vod(self, vod_id: int, vod_name: str) -> dict[str, Any]:
        meta, options = self._episode_options(vod_id, vod_name)
        return self.enqueue_selected(vod_id, vod_name, [option["label"] for option in options if option["status"] in {"missing", "failed"}])

    def _run_task(self, task_id: str, run_token: str) -> None:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.get("status") != "downloading" or task.get("run_token") != run_token:
                return
            target = Path(str(task.get("target", "")))
            if target.is_file() and target.stat().st_size > 0:
                size = target.stat().st_size
                task["status"] = "completed"
                task["progress"] = 1.0
                task["downloaded"] = size
                task["total"] = size
                task["speed"] = 0.0
                task["finished_at"] = task.get("finished_at") or time.time()
                task.pop("run_token", None)
                if not any(entry.get("id") == task_id and entry.get("status") == "completed" for entry in self.history):
                    self.history.append(self._history_entry(task))
                self.tasks.pop(task_id, None)
                self._persist_locked(force=True)
                return
        try:
            signed = self._parse_url(task["source_url"], task["player"])
            with self.lock:
                current = self.tasks.get(task_id, {})
                if current.get("status") != "downloading" or current.get("run_token") != run_token:
                    raise DownloadPaused
            self._download_url(signed, task_id, run_token)
            with self.lock:
                task = self.tasks.get(task_id)
                if not task or task.get("run_token") != run_token or task.get("status") == "deleting":
                    return
                task["status"] = "completed"
                task["progress"] = 1.0
                task["speed"] = 0.0
                task["finished_at"] = time.time()
                task.pop("run_token", None)
                self.history.append(self._history_entry(task))
                self.tasks.pop(task_id, None)
                self._persist_locked(force=True)
        except DownloadPaused:
            with self.lock:
                task = self.tasks.get(task_id)
                if task and task.get("status") == "deleting":
                    return
                if task and task.get("run_token") == run_token:
                    task["status"] = "paused"
                    task["speed"] = 0.0
                    task["started_at"] = None
                    task.pop("run_token", None)
                    self._persist_locked(force=True)
        except Exception as exc:
            with self.lock:
                task = self.tasks.get(task_id)
                if not task or task.get("run_token") != run_token:
                    return
                if task.get("status") == "deleting":
                    return
                if task.get("status") == "pausing":
                    task["status"] = "paused"
                    task["speed"] = 0.0
                    task["started_at"] = None
                    task.pop("run_token", None)
                    self._persist_locked(force=True)
                    return
                task["status"] = "failed"
                task["speed"] = 0.0
                task["error"] = str(exc)
                task["finished_at"] = time.time()
                task.pop("run_token", None)
                self.history.append(self._history_entry(task))
                self._persist_locked(force=True)

    def _parse_url(self, source_url: str, player: str) -> str:
        body = json.dumps({"vid": source_url, "player": player}).encode()
        result = self._api_json("/vod/parse", method="POST", body=body)
        if not result.get("play_url"):
            raise RuntimeError("没有返回下载地址")
        return result["play_url"]

    def _download_url(self, url: str, task_id: str, run_token: str) -> None:
        with self.lock:
            task = self.tasks[task_id]
            if task.get("status") != "downloading" or task.get("run_token") != run_token:
                raise DownloadPaused
            target = Path(task["target"])
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        offset = temporary.stat().st_size if temporary.exists() else 0
        headers = {"User-Agent": "LimeFunDownloader/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=90) as response:
            with self.lock:
                current = self.tasks.get(task_id, {})
                if current.get("status") != "downloading" or current.get("run_token") != run_token:
                    raise DownloadPaused
                self._responses[run_token] = response
            append = offset > 0 and getattr(response, "status", 200) == 206
            if not append:
                offset = 0
            content_length = response.headers.get("Content-Length")
            total = offset + int(content_length) if content_length and content_length.isdigit() else 0
            received = offset
            sample_time = time.monotonic()
            sample_received = received
            with self.lock:
                task = self.tasks[task_id]
                if task.get("status") != "downloading" or task.get("run_token") != run_token:
                    raise DownloadPaused
                task["total"] = total
                task["downloaded"] = received
                task["progress"] = received / total if total else 0.0
            with temporary.open("ab" if append else "wb") as stream:
                while True:
                    with self.lock:
                        current = self.tasks.get(task_id, {})
                        if current.get("status") != "downloading" or current.get("run_token") != run_token:
                            raise DownloadPaused
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    with self.lock:
                        current = self.tasks.get(task_id, {})
                        if current.get("status") != "downloading" or current.get("run_token") != run_token:
                            raise DownloadPaused
                    stream.write(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    with self.lock:
                        task = self.tasks[task_id]
                        task["downloaded"] = received
                        task["total"] = total
                        task["progress"] = received / total if total else 0.0
                        elapsed = now - sample_time
                        if elapsed >= 0.5:
                            instant_speed = (received - sample_received) / elapsed
                            previous_speed = float(task.get("speed", 0) or 0)
                            task["speed"] = instant_speed if previous_speed <= 0 else previous_speed * 0.55 + instant_speed * 0.45
                            sample_time = now
                            sample_received = received
                        # Progress-only writes are throttled harder than state
                        # transitions: .part size is the source of truth for a
                        # resumed transfer, so a crash losing a few seconds of
                        # progress reporting is harmless.
                        self._persist_locked(min_interval=3.0)
        if total and received != total:
            raise IOError(f"下载长度不完整: {received} / {total} bytes")
        # Serialize the final rename with cancellation so a cancelled transfer
        # cannot publish a partial file after the delete request is accepted.
        with self.lock:
            current = self.tasks.get(task_id, {})
            if current.get("status") != "downloading" or current.get("run_token") != run_token:
                raise DownloadPaused
            os.replace(temporary, target)

    def _finish_delete_locked(self, task_id: str) -> None:
        """Called only after the writer exits, or before workers start on load."""
        task = self.tasks[task_id]
        try:
            target = Path(task["target"])
            partial = target.with_name(target.name + ".part")
            resolved = partial.resolve()
            if not any(resolved.is_relative_to(root.resolve()) for root in self.local_roots):
                raise ValueError("临时文件路径不在下载目录内")
            if any(other_id != task_id and Path(other["target"]).resolve() == target.resolve()
                   for other_id, other in self.tasks.items()):
                raise ValueError("另一个任务正在使用相同文件，请先处理该任务")
            partial.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError) as exc:
            task.update(status="delete_failed", speed=0.0, error=f"临时文件清理失败：{exc}")
            task.pop("run_token", None)
            return
        self.tasks.pop(task_id)

    def delete_tasks(self, task_ids: list[str]) -> dict[str, Any]:
        result: dict[str, Any] = {"deleted": [], "pending": [], "missing": [], "errors": {}}
        with self.condition:
            for task_id in dict.fromkeys(task_ids):
                task = self.tasks.get(task_id)
                if not task:
                    result["missing"].append(task_id)
                    continue
                task.update(status="deleting", speed=0.0, error="")
                # Persist intent before removing files; restart completes cleanup.
                self._persist_locked(force=True)
                if task_id in self._active_runs.values():
                    response = self._responses.get(task.get("run_token"))
                    if response:
                        try:
                            connection = response.fp.raw._sock
                            try:
                                connection.shutdown(socket.SHUT_RDWR)
                            finally:
                                # HTTPResponse owns a makefile reference, so
                                # socket.close() alone defers handle closure.
                                # Windows timed recv/select needs the actual
                                # handle closed to wake immediately. Only this
                                # cancelled transfer owns this connection.
                                connection._real_close()
                        except (AttributeError, OSError):
                            pass  # Worker checks status after read / network timeout.
                    result["pending"].append(task_id)
                else:
                    self._finish_delete_locked(task_id)
                    if task_id in self.tasks:
                        result["errors"][task_id] = self.tasks[task_id]["error"]
                    else:
                        result["deleted"].append(task_id)
            self._persist_locked(force=True)
            self.condition.notify_all()
        return result

    def delete_histories(self, history_ids: list[str]) -> dict[str, Any]:
        with self.lock:
            wanted = set(history_ids)
            existing = {entry.get("history_id") for entry in self.history}
            self.history = [entry for entry in self.history if entry.get("history_id") not in wanted]
            self._persist_locked(force=True)
            return {"deleted": sorted(wanted & existing), "missing": sorted(wanted - existing)}

    def retry(self, task_id: str) -> bool:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task or task.get("status") != "failed":
                return False
            task["status"] = "queued"
            task["progress"] = 0.0
            task["downloaded"] = 0
            task["total"] = 0
            task["speed"] = 0.0
            task["error"] = ""
            task["finished_at"] = None
            task.pop("run_token", None)
            task["queue_order"] = self._allocate_priority_order_locked()
            self.history = [
                entry
                for entry in self.history
                if not (entry.get("id") == task_id and entry.get("status") == "failed")
            ]
            self._persist_locked(force=True)
            self.condition.notify_all()
        return True

    def pause(self, task_id: str) -> bool:
        with self.condition:
            task = self.tasks.get(task_id)
            if not task or task.get("status") not in {"queued", "downloading"}:
                return False
            task["status"] = "pausing" if task.get("status") == "downloading" else "paused"
            task["speed"] = 0.0
            if task["status"] == "paused":
                task["started_at"] = None
            self._persist_locked(force=True)
            self.condition.notify_all()
            return True

    def resume(self, task_id: str) -> bool:
        with self.condition:
            task = self.tasks.get(task_id)
            if not task or task.get("status") != "paused":
                return False
            task["status"] = "queued"
            task["speed"] = 0.0
            task["error"] = ""
            task["finished_at"] = None
            task.pop("run_token", None)
            task["queue_order"] = self._allocate_priority_order_locked()
            self._persist_locked(force=True)
            self.condition.notify_all()
            return True

    def retry_history(self, history_id: str) -> bool:
        with self.lock:
            entry = next(
                (
                    item
                    for item in self.history
                    if item.get("history_id") == history_id and item.get("status") == "failed"
                ),
                None,
            )
            if not entry:
                return False
            task_id = str(entry.get("id", ""))
        return self.retry(task_id)

    def delete_history(self, history_id: str) -> bool:
        with self.lock:
            remaining = [entry for entry in self.history if entry.get("history_id") != history_id]
            if len(remaining) == len(self.history):
                return False
            self.history = remaining
            self._persist_locked(force=True)
            return True

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._ensure_workers_locked()
            tasks = sorted(
                (dict(task) for task in self.tasks.values()),
                key=lambda item: (
                    {"downloading": 0, "pausing": 1, "paused": 2, "queued": 3, "failed": 4}.get(str(item.get("status")), 5),
                    item.get("queue_order", 0),
                ),
            )
            history = sorted(
                (dict(item) for item in self.history),
                key=lambda item: item.get("finished_at", item.get("created_at", 0)) or 0,
                reverse=True,
            )
            workers_alive = sum(not future.done() for future in self._worker_futures)
            return {
                "tasks": tasks,
                "history": history[:500],
                "workers": {
                    "configured": self.worker_count,
                    "alive": workers_alive,
                    "busy": len(self._active_runs),
                    "busy_tasks": len(set(self._active_runs.values())),
                    "last_error": self._last_worker_error,
                    "last_error_at": self._last_worker_error_at,
                },
            }

    def shutdown(self) -> None:
        with self.condition:
            self._shutdown = True
            self.condition.notify_all()
        self.pool.shutdown(wait=False, cancel_futures=False)
