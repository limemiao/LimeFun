"""Dependency-free native fallback UI used when Qt DLLs are unavailable."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.request
import tkinter as tk
from tkinter import ttk
from pathlib import Path

from limefun.project_paths import PROJECT_ROOT

ROOT = PROJECT_ROOT
BASE = "http://127.0.0.1:8765"


def request(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


class App:
    def __init__(self, server):
        self.server = server; self.data = {"items": []}; self.library = {"roots": []}; self.rows = []
        self.root = tk.Tk(); self.root.title("LimeFun 本地番剧播放器"); self.root.geometry("1180x760"); self.root.configure(bg="#121716")
        style = ttk.Style(self.root); style.theme_use("clam"); style.configure("TFrame", background="#121716"); style.configure("TLabel", background="#121716", foreground="#e7efea"); style.configure("TButton", padding=7); style.configure("Treeview", background="#1b2421", fieldbackground="#1b2421", foreground="#e7efea", rowheight=34)
        top = ttk.Frame(self.root); top.pack(fill="x", padx=20, pady=16); ttk.Label(top, text="LimeFun", font=("Microsoft YaHei", 20, "bold")).pack(side="left"); ttk.Label(top, text="  本地番剧播放器", foreground="#8ed1b0").pack(side="left")
        self.query = tk.StringVar(); entry = ttk.Entry(top, textvariable=self.query, width=34); entry.pack(side="right", padx=8); entry.bind("<KeyRelease>", lambda _: self.render()); ttk.Button(top, text="刷新目录", command=self.load).pack(side="right")
        bar = ttk.Frame(self.root); bar.pack(fill="x", padx=20); self.local = ttk.Combobox(bar, values=["全部", "本地已有", "本地没有"], state="readonly", width=14); self.local.current(0); self.local.pack(side="left"); self.local.bind("<<ComboboxSelected>>", lambda _: self.render())
        body = ttk.Frame(self.root); body.pack(fill="both", expand=True, padx=20, pady=14); self.list = ttk.Treeview(body, columns=("meta",), show="tree headings"); self.list.heading("#0", text="番剧"); self.list.heading("meta", text="状态"); self.list.column("#0", width=650); self.list.column("meta", width=260); self.list.pack(fill="both", expand=True); self.list.bind("<Double-1>", self.open_item)
        self.status = ttk.Label(self.root, text="正在读取目录..."); self.status.pack(anchor="w", padx=20, pady=8); self.load()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load(self):
        try: self.data = request("/api/catalog?refresh=1"); self.library = request("/api/library?refresh=1"); self.render()
        except Exception as exc: self.status.config(text=f"读取失败: {exc}")

    def render(self):
        self.list.delete(*self.list.get_children()); self.rows=[]; q=self.query.get().lower(); mode=self.local.get()
        for item in self.data.get("items", []):
            name=str(item.get("vod_name", "")); present=bool(item.get("local"))
            if q and q not in name.lower() and q != str(item.get("id")): continue
            if mode == "本地已有" and not present or mode == "本地没有" and present: continue
            iid=self.list.insert("", "end", text=name, values=((f"本地已有 · {item.get('local_count',0)} 集" if present else "本地未收藏"),)); self.rows.append((iid,item))
        self.status.config(text=f"显示 {len(self.rows)} 部番剧")

    def open_item(self, _event):
        selected=self.list.selection();
        if not selected: return
        item=next((x for i,x in self.rows if i == selected[0]), None)
        if not item or not item.get("local"): return
        name=item.get("vod_name"); target=None
        for root in self.library.get("roots", []):
            for series in root.get("series", []):
                if series.get("name") == name:
                    seasons=series.get("seasons", []); episodes=seasons[0].get("episodes", []) if seasons else []
                    if episodes: target=Path("D:/") / ("番剧" if root.get("id") == "archive" else "新番") / name / seasons[0].get("name", "") / episodes[0].get("filename", "")
        if target and target.exists(): os.startfile(str(target))

    def close(self):
        if self.server and self.server.poll() is None: self.server.terminate()
        self.root.destroy()

    def run(self): self.root.mainloop()


def main():
    server=None
    try:
        request("/api/catalog")
    except Exception:
        subprocess.run(["taskkill", "/IM", "LimeFunServer.exe", "/F"], capture_output=True)
        server=subprocess.Popen([str(ROOT / "runtime" / "current" / "LimeFunServer.exe")], cwd=ROOT)
        deadline=time.monotonic()+30
        while time.monotonic() < deadline:
            try:
                request("/api/catalog")
                break
            except Exception:
                time.sleep(.5)
    app=App(server); app.run(); return 0
