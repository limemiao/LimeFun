#!/usr/bin/env python3
"""Enrich the local catalog with years and download vod_pic covers."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "mutefun_catalog_full.json"
COVERS = ROOT / "covers"
API = os.environ.get("MUTEFUN_API_BASE", "https://go.5idm.top/app/api").rstrip("/")
AES_KEY = os.environ.get("MUTEFUN_AES_KEY", "").encode("ascii")
PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def decrypt_data(value: str) -> dict:
    if not AES_KEY:
        raise SystemExit("缺少 MUTEFUN_AES_KEY：请设置环境变量后再运行")
    ciphertext = base64.b64decode(value)
    decryptor = Cipher(algorithms.AES(AES_KEY), modes.ECB()).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    padding = plaintext[-1]
    return json.loads(plaintext[:-padding].decode("utf-8"))


def fetch_detail(vod_id: int, retries: int = 3) -> dict:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            request = urllib.request.Request(
                f"{API}/vod/{vod_id}", headers={"User-Agent": "LimeFunCatalogSync/1.0"}
            )
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("code") != 200:
                raise RuntimeError(str(payload)[:200])
            return decrypt_data(payload["data"])
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(1.5**attempt)
    raise RuntimeError(f"vod {vod_id}: {last_error}") from last_error


def cover_extension(url: str, content_type: str = "") -> str:
    suffix = Path(urlparse(url).path).suffix.casefold()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return suffix
    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
    return guessed if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"} else ".jpg"


def normalize_cover_url(url: str) -> str:
    # Older catalog rows use mac:// as a placeholder for an HTTPS image URL.
    return "https://" + url[6:] if url.startswith("mac://") else url


def cover_headers(url: str) -> dict[str, str]:
    headers = {"User-Agent": "Mozilla/5.0 (LimeFunCatalogSync/1.0)", "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}
    if "doubanio.com" in url:
        headers["Referer"] = "https://movie.douban.com/"
    return headers


def download_cover(item: dict, retries: int = 3) -> tuple[int, str | None, str | None]:
    vod_id = int(item["id"])
    url = normalize_cover_url(str(item.get("vod_pic") or "").strip())
    if not url:
        return vod_id, None, "no vod_pic"
    COVERS.mkdir(parents=True, exist_ok=True)
    existing = list(COVERS.glob(f"{vod_id}.*"))
    existing = [path for path in existing if path.suffix.casefold() in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}]
    if existing and existing[0].stat().st_size > 0:
        return vod_id, existing[0].name, None
    last_error: Exception | None = None
    for attempt in range(retries):
        temporary: Path | None = None
        try:
            request = urllib.request.Request(url, headers=cover_headers(url))
            with urllib.request.urlopen(request, timeout=30) as response:
                suffix = cover_extension(url, response.headers.get("Content-Type", ""))
                target = COVERS / f"{vod_id}{suffix}"
                temporary = target.with_name(target.name + ".part")
                with temporary.open("wb") as stream:
                    while chunk := response.read(256 * 1024):
                        stream.write(chunk)
            if temporary.stat().st_size == 0:
                raise IOError("empty cover response")
            os.replace(temporary, target)
            return vod_id, target.name, None
        except Exception as exc:
            last_error = exc
            if temporary:
                temporary.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(1.5**attempt)
    return vod_id, None, str(last_error)


def main() -> None:
    parser = argparse.ArgumentParser(description="补全 vod_year 并下载本地 vod_pic 封面")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--no-covers", action="store_true", help="只补全年份，不下载封面")
    parser.add_argument("--covers-only", action="store_true", help="跳过详情请求，只重试/补齐封面")
    parser.add_argument("--catalog", default=str(CATALOG))
    args = parser.parse_args()
    catalog_path = Path(args.catalog)
    with catalog_path.open(encoding="utf-8") as stream:
        catalog = json.load(stream)
    by_id = {int(item["id"]): item for item in catalog}
    log(f"catalog: {len(catalog)} items")

    details: dict[int, dict] = {}
    errors: list[str] = []
    if not args.covers_only:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(fetch_detail, int(item["id"])): int(item["id"]) for item in catalog}
            for index, future in enumerate(as_completed(futures), 1):
                vod_id = futures[future]
                try:
                    details[vod_id] = future.result()
                except Exception as exc:
                    errors.append(str(exc))
                if index % 100 == 0 or index == len(catalog):
                    log(f"details: {index}/{len(catalog)}")
    for vod_id, detail in details.items():
        item = by_id[vod_id]
        if detail.get("vod_year") is not None:
            item["vod_year"] = str(detail["vod_year"])
        for key in ("vod_class", "vod_isend", "vod_remarks", "vod_pic"):
            if detail.get(key) is not None:
                item[key] = detail[key]

    if not args.no_covers:
        cover_errors: list[str] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(download_cover, item): item for item in catalog}
            for index, future in enumerate(as_completed(futures), 1):
                item = futures[future]
                vod_id, filename, error = future.result()
                if filename:
                    item["cover_file"] = filename
                elif error:
                    cover_errors.append(f"{vod_id}: {error}")
                if index % 100 == 0 or index == len(catalog):
                    log(f"covers: {index}/{len(catalog)}")
        errors.extend(cover_errors)

    temporary = catalog_path.with_name(catalog_path.name + ".part")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(catalog, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    os.replace(temporary, catalog_path)
    log(f"done: years={sum(1 for item in catalog if item.get('vod_year'))} covers={sum(1 for item in catalog if item.get('cover_file'))} errors={len(errors)}")
    if errors:
        error_file = catalog_path.with_name("catalog_sync_errors.txt")
        error_file.write_text("\n".join(errors) + "\n", encoding="utf-8")
        log(f"errors written to {error_file}")


if __name__ == "__main__":
    main()
