#!/usr/bin/env python3
"""Standalone MuteFun catalog lookup and downloader.

The app is not required. The script talks to the public API, decrypts its
response payloads, resolves a fresh signed URL for each episode, and writes
downloads atomically so an interrupted transfer can be resumed safely.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError as exc:  # pragma: no cover - exercised during setup only
    raise SystemExit("缺少 cryptography，请先运行: python -m pip install cryptography") from exc


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
DEFAULT_CONFIG = {
    "api_base": "https://go.5idm.top/app/api",
    "aes_key": "",
    "catalog": "mutefun_catalog_full.json",
    "output_dir": "downloads",
    "default_player": "auto",
    "timeout": 30,
    "retries": 3,
}
PLAYER_LABELS = {"r2": "APP专线", "m2": "网页专线", "v1": "v1"}
EPISODE_RE = re.compile(r"第\s*0*(\d+)\s*集")
PRINT_LOCK = threading.Lock()


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open(encoding="utf-8") as fh:
            config.update(json.load(fh))
    # Environment variables make the workflow usable when the API endpoint
    # or key changes, without editing the script.
    config["api_base"] = os.environ.get("MUTEFUN_API_BASE", config["api_base"]).rstrip("/")
    config["aes_key"] = os.environ.get("MUTEFUN_AES_KEY", config["aes_key"])
    return config


def decrypt_data(value: str, key: str) -> Any:
    if not key:
        raise RuntimeError("缺少 MUTEFUN_AES_KEY：请设置环境变量，或在 config.json 的 aes_key 中填写")
    ciphertext = base64.b64decode(value)
    decryptor = Cipher(algorithms.AES(key.encode("ascii")), modes.ECB()).decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    if not plaintext:
        raise ValueError("API 返回了空加密数据")
    padding = plaintext[-1]
    if padding < 1 or padding > 16 or plaintext[-padding:] != bytes([padding]) * padding:
        raise ValueError("API 数据的 PKCS7 填充无效")
    return json.loads(plaintext[:-padding].decode("utf-8"))


class Client:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.api = config["api_base"]
        self.timeout = int(config.get("timeout", 30))
        self.retries = max(1, int(config.get("retries", 3)))

    def request(self, path: str, *, method: str = "GET", body: bytes | None = None) -> dict[str, Any]:
        url = self.api + path
        headers = {"User-Agent": "LimeFun/1.0", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("code") not in (None, 200):
                    raise RuntimeError(f"API 错误: {payload}")
                if "data" in payload:
                    return decrypt_data(payload["data"], self.config["aes_key"])
                return payload
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(1.5**attempt)
        raise RuntimeError(f"请求失败 {path}: {last_error}") from last_error

    def detail(self, vod_id: int) -> dict[str, Any]:
        return self.request(f"/vod/{vod_id}")

    def parse_url(self, source_url: str, player: str) -> str:
        body = json.dumps({"vid": source_url, "player": player}, ensure_ascii=False).encode()
        result = self.request("/vod/parse", method="POST", body=body)
        play_url = result.get("play_url")
        if not play_url:
            raise RuntimeError("parse 接口没有返回 play_url")
        return play_url


def catalog_path(config: dict[str, Any]) -> Path:
    path = Path(config["catalog"])
    return path if path.is_absolute() else ROOT / path


def load_catalog(config: dict[str, Any]) -> list[dict[str, Any]]:
    path = catalog_path(config)
    if not path.exists():
        raise SystemExit(f"找不到番剧清单: {path}")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def display_name(item: dict[str, Any]) -> str:
    return html.unescape(str(item.get("vod_name", ""))).strip()


def resolve_vod(value: str, catalog: list[dict[str, Any]]) -> dict[str, Any]:
    if value.isdigit():
        matches = [item for item in catalog if str(item.get("id")) == value]
    else:
        query = html.unescape(value).casefold().strip()
        matches = [item for item in catalog if display_name(item).casefold() == query]
        if not matches:
            matches = [item for item in catalog if query in display_name(item).casefold()]
    if not matches:
        raise SystemExit(f"清单中找不到番剧: {value}")
    if len(matches) > 1:
        choices = ", ".join(f"{item.get('id')}:{display_name(item)}" for item in matches[:12])
        raise SystemExit(f"名称匹配到多部番剧，请改用 ID: {choices}")
    return matches[0]


def choose_line(detail: dict[str, Any], requested: str) -> tuple[str, dict[str, Any]]:
    lines = {str(line.get("player")): line for line in detail.get("playerData", [])}
    if requested != "auto":
        line = lines.get(requested)
        if not line or not line.get("vids"):
            raise SystemExit(f"线路 {requested} 不存在或没有集数，可用线路: {', '.join(lines)}")
        return requested, line
    for player in ("r2", "m2", "v1"):
        if lines.get(player, {}).get("vids"):
            return player, lines[player]
    for player, line in lines.items():
        if line.get("vids"):
            return player, line
    raise SystemExit("详情中没有可用播放线路")


def episode_number(label: str) -> int | None:
    match = EPISODE_RE.search(label)
    return int(match.group(1)) if match else None


def episodes_for(detail: dict[str, Any], requested: str) -> tuple[str, list[dict[str, Any]]]:
    player, line = choose_line(detail, requested)
    episodes = []
    for raw in line["vids"]:
        label, separator, source = str(raw).partition("$")
        if not separator or not source:
            continue
        episodes.append({"label": label.strip(), "number": episode_number(label), "source": source})
    return player, episodes


def safe_filename(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return value or "未命名"


def output_filename(label: str, player: str) -> str:
    number = episode_number(label)
    title = f"第{number:02d}集" if number is not None else safe_filename(label)
    suffix = PLAYER_LABELS.get(player, player)
    return f"{title}-{suffix}.mp4"


def download_file(url: str, target: Path, client: Client) -> None:
    if target.exists() and target.stat().st_size > 0:
        log(f"跳过(已存在): {target.name}")
        return
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": "LimeFun/1.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=max(60, client.timeout * 2))
    except urllib.error.HTTPError as exc:
        if offset and exc.code == 416:
            part.unlink(missing_ok=True)
            return download_file(url, target, client)
        raise
    append = offset > 0 and getattr(response, "status", 200) == 206
    if not append:
        offset = 0
    total = response.headers.get("Content-Length")
    total_bytes = offset + int(total) if total and total.isdigit() else 0
    started = time.monotonic()
    received = offset
    target.parent.mkdir(parents=True, exist_ok=True)
    with response, part.open("ab" if append else "wb") as stream:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
            received += len(chunk)
            if time.monotonic() - started > 10:
                progress = f"/{total_bytes / 1048576:.0f} MB" if total_bytes else ""
                log(f"  {target.name}: {received / 1048576:.0f} MB{progress}")
                started = time.monotonic()
    if total_bytes and received != total_bytes:
        raise IOError(f"下载长度不完整: {received} / {total_bytes} bytes")
    os.replace(part, target)
    log(f"完成: {target.name} ({received / 1048576:.1f} MB)")


def download_episode(client: Client, detail: dict[str, Any], player: str, item: dict[str, Any], out_dir: Path) -> None:
    target = out_dir / output_filename(item["label"], player)
    if target.exists() and target.stat().st_size > 0:
        log(f"跳过(已存在): {target.name}")
        return
    last_error: Exception | None = None
    for attempt in range(client.retries):
        try:
            # Signed URLs expire, so resolve one immediately before downloading.
            url = client.parse_url(item["source"], player)
            download_file(url, target, client)
            return
        except Exception as exc:
            last_error = exc
            if attempt + 1 < client.retries:
                log(f"重试 {target.name}: {exc}")
                time.sleep(1.5**attempt)
    log(f"失败: {target.name}: {last_error}")


def cmd_search(args: argparse.Namespace, config: dict[str, Any]) -> None:
    catalog = load_catalog(config)
    query = args.query.casefold()
    matches = [item for item in catalog if query in display_name(item).casefold() or query == str(item.get("id"))]
    for item in matches[: args.limit]:
        status = "完结" if item.get("vod_isend") else "连载"
        print(f"{item.get('id')}\t{display_name(item)}\t{item.get('vod_remarks', '')}\t{status}")
    print(f"共 {len(matches)} 部，显示 {min(len(matches), args.limit)} 部", file=sys.stderr)


def cmd_episodes(args: argparse.Namespace, config: dict[str, Any]) -> None:
    item = resolve_vod(args.vod, load_catalog(config))
    detail = Client(config).detail(int(item["id"]))
    player, episodes = episodes_for(detail, args.player)
    print(f"{display_name(item)} (ID {item['id']}, 线路 {player}, 共 {len(episodes)} 集)")
    for ep in episodes:
        print(f"{ep['number'] if ep['number'] is not None else '-'}\t{ep['label']}")


def cmd_download(args: argparse.Namespace, config: dict[str, Any]) -> None:
    client = Client(config)
    item = resolve_vod(args.vod, load_catalog(config))
    detail = client.detail(int(item["id"]))
    player, episodes = episodes_for(detail, args.player)
    matches = [ep for ep in episodes if ep["number"] == args.episode]
    if not matches:
        raise SystemExit(f"第 {args.episode} 集不存在；可用集数: {[ep['number'] for ep in episodes]}")
    out_dir = Path(args.output or config["output_dir"]) / safe_filename(display_name(item))
    download_episode(client, detail, player, matches[0], out_dir)


def cmd_download_all(args: argparse.Namespace, config: dict[str, Any]) -> None:
    client = Client(config)
    item = resolve_vod(args.vod, load_catalog(config))
    detail = client.detail(int(item["id"]))
    player, episodes = episodes_for(detail, args.player)
    selected = [ep for ep in episodes if ep["number"] is None or args.start <= ep["number"] <= args.end]
    out_dir = Path(args.output or config["output_dir"]) / safe_filename(display_name(item))
    log(f"{display_name(item)}: 线路 {player}，待处理 {len(selected)} 集 -> {out_dir}")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(download_episode, client, detail, player, ep, out_dir) for ep in selected]
        for future in as_completed(futures):
            future.result()
    log("全部完成")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LimeFun：不依赖 App 的番剧查询与下载工具")
    sub = parser.add_subparsers(dest="command", required=True)
    search = sub.add_parser("search", help="在本地番剧清单中搜索")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=30)
    eps = sub.add_parser("episodes", help="查询番剧集数和可用线路")
    eps.add_argument("vod")
    eps.add_argument("--player", choices=["auto", "r2", "m2", "v1"], default=None)
    one = sub.add_parser("download", help="下载单集")
    one.add_argument("vod")
    one.add_argument("episode", type=int)
    one.add_argument("--player", choices=["auto", "r2", "m2", "v1"], default=None)
    one.add_argument("--output", help="输出根目录，默认 config.json 的 output_dir")
    all_cmd = sub.add_parser("download-all", help="下载全部或指定范围的集数")
    all_cmd.add_argument("vod")
    all_cmd.add_argument("--player", choices=["auto", "r2", "m2", "v1"], default=None)
    all_cmd.add_argument("--output", help="输出根目录，默认 config.json 的 output_dir")
    all_cmd.add_argument("--workers", type=int, default=4)
    all_cmd.add_argument("--start", type=int, default=0)
    all_cmd.add_argument("--end", type=int, default=10**9)
    return parser


def main() -> None:
    args = make_parser().parse_args()
    config = load_config()
    if getattr(args, "player", None) is None:
        args.player = config.get("default_player", "auto")
    commands = {"search": cmd_search, "episodes": cmd_episodes, "download": cmd_download, "download-all": cmd_download_all}
    commands[args.command](args, config)


if __name__ == "__main__":
    main()
