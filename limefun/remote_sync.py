#!/usr/bin/env python3
"""Periodic remote MuteFun catalog synchronization."""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class RemoteCatalogSync:
    def __init__(self, catalog_path: Path, interval: int = 6 * 60 * 60):
        self.catalog_path = catalog_path
        self.cover_root = catalog_path.parent / "covers"
        self.interval = max(300, interval)
        self.api = os.environ.get("MUTEFUN_API_BASE", "https://go.5idm.top/app/api").rstrip("/")
        self.key = os.environ.get("MUTEFUN_AES_KEY", "").encode("ascii")
        self.lock = threading.RLock()
        self.status: dict[str, Any] = {"running": False, "last_started": None, "last_finished": None, "last_error": "", "added": 0, "updated": 0}
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="limefun-catalog-sync", daemon=True)
        self.thread.start()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.status)

    def request_now(self) -> None:
        threading.Thread(target=self.sync, name="limefun-catalog-sync-now", daemon=True).start()

    def _loop(self) -> None:
        # Delay the first run so the player remains responsive immediately after startup.
        self.stop_event.wait(20)
        while not self.stop_event.is_set():
            self.sync()
            self.stop_event.wait(self.interval)

    def _decrypt(self, value: str) -> list[dict[str, Any]]:
        if not self.key:
            raise RuntimeError("缺少 MUTEFUN_AES_KEY：请设置环境变量后再试")
        ciphertext = base64.b64decode(value)
        decryptor = Cipher(algorithms.AES(self.key), modes.ECB()).decryptor()
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        padding = plaintext[-1]
        payload = json.loads(plaintext[:-padding].decode("utf-8"))
        return payload.get("filter_vods", [])

    def _fetch_page(self, page: int) -> list[dict[str, Any]]:
        request = urllib.request.Request(f"{self.api}/content/filter?type=0&page={page}&sort=0", headers={"User-Agent": "LimeFunCatalogSync/2.0"})
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("code") != 200:
            raise RuntimeError(f"remote catalog page {page}: {payload}")
        return self._decrypt(payload["data"])

    def sync(self) -> None:
        with self.lock:
            if self.status["running"]:
                return
            self.status.update(running=True, last_started=time.time(), last_error="")
        try:
            existing = json.loads(self.catalog_path.read_text(encoding="utf-8")) if self.catalog_path.exists() else []
            by_id = {str(item.get("id")): dict(item) for item in existing if item.get("id") is not None}
            remote: dict[str, dict[str, Any]] = {}
            page = 1
            while True:
                rows = self._fetch_page(page)
                if not rows:
                    break
                for row in rows:
                    remote[str(row["id"])] = row
                if len(rows) < 12:
                    break
                page += 1
                time.sleep(0.15)
            added = updated = 0
            new_items: list[dict[str, Any]] = []
            for item_id, row in remote.items():
                if item_id not in by_id:
                    by_id[item_id] = row
                    new_items.append(by_id[item_id])
                    added += 1
                    continue
                item = by_id[item_id]
                before = json.dumps({key: item.get(key) for key in ("vod_name", "vod_class", "vod_isend", "vod_remarks", "vod_pic")}, ensure_ascii=False, sort_keys=True)
                for key in ("vod_name", "vod_class", "vod_isend", "vod_remarks", "vod_pic"):
                    if row.get(key) is not None:
                        item[key] = row[key]
                after = json.dumps({key: item.get(key) for key in ("vod_name", "vod_class", "vod_isend", "vod_remarks", "vod_pic")}, ensure_ascii=False, sort_keys=True)
                if before != after:
                    updated += 1
            if new_items:
                from limefun.sync_catalog import download_cover, fetch_detail
                for item in new_items:
                    try:
                        detail = fetch_detail(int(item["id"]))
                        for key in ("vod_year", "vod_class", "vod_isend", "vod_remarks", "vod_pic"):
                            if detail.get(key) is not None:
                                item[key] = detail[key]
                    except Exception:
                        pass
                    try:
                        _vod_id, filename, _error = download_cover(item)
                        if filename:
                            item["cover_file"] = filename
                    except Exception:
                        pass
            temporary = self.catalog_path.with_name(self.catalog_path.name + ".sync.part")
            temporary.write_text(json.dumps(sorted(by_id.values(), key=lambda item: int(item.get("id", 0))), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            os.replace(temporary, self.catalog_path)
            with self.lock:
                self.status.update(running=False, last_finished=time.time(), added=added, updated=updated, last_error="")
        except Exception as exc:
            with self.lock:
                self.status.update(running=False, last_finished=time.time(), last_error=str(exc))

    def shutdown(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
