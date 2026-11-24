# LimeFun

不依赖官方 App 的番剧搜索、播放与下载工作流。提供命令行下载器、本地 Web 播放器
和 WebView2 桌面端，媒体文件保存在本地，不上传到网络。

## 功能

- 本地番剧清单搜索：按标题、年份、类别筛选，支持数字 ID 精确匹配。
- 线路查询与下载：`r2`（APP 专线）、`m2`（网页专线）、`v1`，自动选择可用线路。
- 单集 / 整季下载，默认 4 并发，`.part` 临时文件 + HTTP Range 断点续传。
- 下载队列：暂停、继续、取消、删除、失败重试、状态持久化。
- 本地播放器：查看/搜索页、播放页、下载中心，支持选集、倍速、进度记忆。
- WebView2 桌面窗口，复用网页端全部功能。
- 可选远程目录同步，定时更新本地清单和封面。

## 环境要求

- Windows 10 / 11。
- Python 3.10 或更高版本。
- 播放器和桌面端需要 Microsoft Edge WebView2 Runtime。
- 依赖：`cryptography`（接口返回 AES 加密 JSON）。
- 构建桌面端需要 PyInstaller。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 仅构建桌面端时需要
.\.venv\Scripts\python.exe -m pip install pyinstaller
```

## 配置

```powershell
copy config.example.json config.json
```

`config.json` 字段：

| 字段 | 说明 |
|---|---|
| `api_base` | 接口地址 |
| `aes_key` | 接口解密密钥 |
| `catalog` | 本地清单文件名 |
| `output_dir` | 默认下载目录 |
| `default_player` | `auto` / `r2` / `m2` / `v1` |
| `timeout` / `retries` | 请求超时与重试次数 |

解密密钥不随仓库分发，推荐通过环境变量提供：

```powershell
$env:MUTEFUN_API_BASE = "https://go.5idm.top/app/api"
$env:MUTEFUN_AES_KEY  = "<your-key>"
```

也可以在 `config.json` 的 `aes_key` 中填写。`config.json` 已被 `.gitignore` 忽略。

首次使用需要生成番剧清单：

```powershell
python sync_catalog.py
```

清单写入 `mutefun_catalog_full.json`，封面缓存到 `covers/`。

## 命令行

```powershell
# 搜索本地清单
python limefun.py search "碧蓝之海"
python limefun.py search 2177

# 查询线路和集数
python limefun.py episodes 2177
python limefun.py episodes "碧蓝之海 第三季" --player r2

# 下载单集 / 整季
python limefun.py download 2177 1
python limefun.py download 2177 1 --player r2 --output D:\Anime
python limefun.py download-all 2177
python limefun.py download-all 2177 --start 1 --end 7 --workers 2 --output D:\Anime
```

每集下载前会重新获取预签名 URL，URL 不能长期保存复用。正式文件已存在且大小大于 0
时跳过；中断后保留 `.part`，再次运行会尝试续传。

## 本地播放器

```powershell
# 双击 run-server.cmd，或
python player_server.py

# 指定端口 / 媒体库
python player_server.py --port 8766 --archive "D:\Anime" --new "D:\NewAnime"
```

浏览器打开 <http://127.0.0.1:8765>：

- `/`：查看与搜索
- `/player.html`：播放
- `/downloads.html`：下载中心

播放器只读取本地媒体库，不修改视频文件。`player_web/season-data.json` 是公开的
番剧季度元数据，用于播放器的季度视图。

## 桌面端

```powershell
# 构建（输出到 runtime/current/）
build.cmd

# 启动
run.cmd
```

桌面端使用 WebView2 创建独立窗口，启动时自动检查并拉起本地后台服务；关闭窗口后，
本次启动的服务进程会退出。也可以双击 `启动LimeFun桌面版.bat`。

## 目录结构

```text
limefun/
├─ limefun.py            # CLI：搜索、线路、下载
├─ download_manager.py   # 下载队列、断点续传、状态持久化
├─ sync_catalog.py       # 抓取并生成本地番剧清单
├─ remote_sync.py        # 定时远程目录同步
├─ player_server.py      # 本地播放器 HTTP 服务
├─ player_web/           # 播放器前端（HTML/CSS/JS）
├─ project_paths.py      # 源码/打包后的项目根目录定位
├─ desktop_*.py          # Tk / WebView2 桌面入口
├─ limefun.spec 等       # PyInstaller 配置
├─ config.example.json
├─ requirements.txt
├─ run-server.cmd
└─ run.cmd
```

## 已知限制

- `mutefun_catalog_full.json` 是本地快照，不会自动更新；需要时重新运行
  `sync_catalog.py`。
- 接口域名失效时需要替换 `api_base`；服务端加密方式或密钥变更时需要同步更新
  解密逻辑。
- 预签名下载地址由服务端生成，下载速度和可用线路取决于服务端与网络状况。
- 本工具只负责下载和文件落盘，不自动整理、重命名或生成归档清单。
- 视频文件、`downloads/`、`covers/`、`runtime/` 和构建产物均不入库。

## 免责声明

本项目仅供学习和技术研究使用。请遵守目标站点的服务条款与当地法律法规，不要
传播或商用下载内容。

## 许可

本仓库未附带开源许可证。
