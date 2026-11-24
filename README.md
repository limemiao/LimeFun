# LimeFun

LimeFun 用于搜索番剧、管理下载和播放本机视频。可以从浏览器管理媒体库，也可以通过 CLI 查询线路和下载剧集。Windows 桌面端使用 WebView2 显示同一个本地播放器。

## 安装与首次启动

需要 Python 3.10+。以下命令从仓库根目录运行。浏览器播放只需浏览器，打包桌面端还需要 Windows 10/11 和 Microsoft Edge WebView2 Runtime。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\run-server.cmd --archive ".\media\archive" --new ".\media\new"
```

打开 <http://127.0.0.1:8765>。把已有视频按番剧放进指定目录即可浏览，播放能力取决于浏览器支持的媒体格式。归档目录也用作 Web 下载目的地。默认目录是 `D:\Anime` 和 `D:\NewAnime`，默认监听本机。与其他本地服务端口冲突时可加 `--port 8766`。

| 页面 | 用途 |
| --- | --- |
| `/` | 搜索、筛选和浏览本地库与远程目录 |
| `/player.html` | 选集、倍速、连播与播放进度 |
| `/downloads.html` | 管理队列、重试失败项和查看下载记录 |

## 远程目录与配置

播放本地文件无需密钥。远程目录、线路和下载需要可用的 MuteFun 接口，以及你获准使用的解密密钥。在启动服务的同一个终端设置环境变量。

```powershell
$env:MUTEFUN_AES_KEY = '填写你的密钥'
# 可选，自定义接口地址
# $env:MUTEFUN_API_BASE = 'https://example.com/api'
.\run-server.cmd
```

在首页触发远程目录同步，等状态显示结束。成功后会生成 `mutefun_catalog_full.json`，封面缓存在 `covers/`。也可以使用已有的兼容清单。补全年份、重新下载封面时运行 `python -m limefun.sync_catalog`，该命令需要先有清单。

复制 `config.example.json` 为 `config.json` 可调整 CLI 默认值。桌面和 Web 的远程连接参数通过环境变量配置。

| CLI 配置 | 作用 |
| --- | --- |
| `api_base`、`aes_key` | 接口与解密密钥，环境变量优先 |
| `catalog` | 清单路径 |
| `output_dir` | 下载目录，默认 `downloads/` |
| `default_player` | `auto`、`r2`、`m2` 或 `v1` |
| `timeout`、`retries` | 请求超时秒数和重试次数 |

## CLI 常用操作

激活虚拟环境后运行，或把 `python` 换成本项目虚拟环境里的 Python。

```powershell
python -m limefun search "番剧名"
python -m limefun episodes 2177
python -m limefun download 2177 1 --player auto
python -m limefun download-all 2177 --start 1 --end 7 --workers 2
```

下载前会刷新临时下载地址。下载中写入 `.part` 文件，服务端支持 HTTP Range 时可尝试续传。下载完成后再改为正式文件名。Web 下载中心把当前任务和历史记录分开管理，删除历史记录不会移除已完成的视频。

## Windows 桌面端

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller pywebview
.\build.cmd
.\run.cmd
```

构建产物位于 `runtime/current/`。桌面端会连接本机 8765 端口并按需启动配套的后台服务。更新目录结构后请重新构建桌面端。更多细节见 [桌面版](docs/README_DESKTOP.md) 和 [播放器](docs/README_PLAYER.md)。

## 常见问题

- **本地库为空**：检查启动时传给 `--archive` 和 `--new` 的目录是否存在，视频是否放在作品子目录里。
- **CLI 搜索找不到清单**：先同步远程目录，或在 `config.json` 指定本机已有清单。Web 和桌面端默认读取根目录的 `mutefun_catalog_full.json`。
- **远程同步失败**：确认变量设置在启动服务的终端，再检查首页的同步状态。已经运行的服务需要重启才能读到环境变量的变更。
- **桌面窗口加载失败**：确认已按新目录重新构建、WebView2 Runtime 已安装，以及 8765 端口没有被其他程序占用。

## 目录与开发

| 路径 | 内容 |
| --- | --- |
| `limefun/` | CLI、HTTP 服务、下载管理和目录同步 |
| `limefun/player_web/` | HTML、CSS、JavaScript 和季度数据 |
| `desktop/` | WebView2、Qt、Tk 等桌面入口与图标 |
| `packaging/` | PyInstaller 配置，标准构建使用 Server 和 WebView 两份 |
| `docs/`、`tests/` | 使用手册与下载流程回归测试 |

旧的 `python limefun.py`、`python player_server.py`、`python sync_catalog.py` 分别换成 `python -m limefun`、`python -m limefun.player_server`、`python -m limefun.sync_catalog`。原有本地清单、收藏和下载状态仍从仓库根目录读取。

```powershell
python -m unittest discover -s tests -v
```

本地配置、封面、清单、收藏、下载状态和视频应留在使用者的机器上。远程功能受接口可用性及站点条款约束。本仓库尚未附带开源许可证。
