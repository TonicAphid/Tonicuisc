# Tonicuisc

一个自用的音乐软件：**Python 后端（FastAPI + musicdl，音源为咪咕 / 酷我）** + **Flutter 客户端（Windows / Linux / macOS / iOS）**。

> 仅用于个人学习与自建服务，请勿用于商业用途或大规模分发。所有音频由第三方音源提供。

## 目录结构

```
server/                      # 后端 API
  tonicuisc_server/
    config.py                # 环境变量配置、音源别名
    service.py               # musicdl 封装（搜索 / 下载 / 缓存）
    main.py                  # FastAPI 路由（含 Range 流式播放）
    __main__.py              # python -m tonicuisc_server
  tests/test_api.py
  requirements.txt
app/                         # Flutter 客户端
  lib/
    main.dart                # 入口（桌面端初始化 media_kit 播放后端）
    api/api_client.dart      # 后端 HTTP 客户端 + 后端地址持久化
    models/song.dart
    player/player_controller.dart
    pages/home_page.dart     # 搜索 / 试听 / 下载
    widgets/player_bar.dart
tool/
  bootstrap_platforms.ps1/.sh  # 生成各平台原生工程 + 打补丁 + pub get
  apply_patches.ps1/.sh        # 覆盖 macOS 权限、iOS Info.plist
  platform_patches/            # 原生配置补丁源文件
.github/workflows/
  server-ci.yml              # 后端编译 + 接口测试
  build.yml                  # 四平台客户端构建产物
```

## 后端

```bash
cd server
pip install -r requirements.txt
python -m tonicuisc_server          # 默认 0.0.0.0:8000
```

环境变量（见 `server/.env.example`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TONICUISC_HOST` | `0.0.0.0` | 监听地址 |
| `TONICUISC_PORT` | `8000` | 端口 |
| `TONICUISC_SOURCES` | `migu,kuwo` | 启用音源 |
| `TONICUISC_SEARCH_SIZE` | `10` | 每个音源返回数量（调大要翻页，更慢） |
| `TONICUISC_CACHE_DIR` | `server/.cache` | 下载缓存目录 |

接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态 |
| GET | `/api/sources` | 可用音源 |
| GET | `/api/search?keyword=&sources=migu,kuwo&limit=50&refresh=false` | 搜索（`refresh=true` 跳过搜索缓存） |
| GET | `/api/url/{id}` | 直链（best effort） |
| GET | `/api/lyric/{id}` | 歌词 |
| GET | `/api/stream/{id}` | 音频流，支持 HTTP Range |
| GET | `/api/download/{id}` | 附件下载 |

`{id}` 形如 `migu:123456`（实际是 `MiguMusicClient:600929000000096577`），由 `/api/search` 返回；服务端缓存 3 小时，过期需重新搜索。

### 缓存

- **搜索缓存**：相同关键词 + 音源 + limit 在 10 分钟内直接返回，不再联网；并发相同请求只会真正搜一次。
- **音频缓存**：下载后的文件统一放在 `.cache/files/<音源>_<id>.<ext>`，同一首歌不会重复下载。
- musicdl 每次搜索都会新建 `.cache/music/<音源>/<时间戳> <关键词>/`，服务端下载完会把文件搬走、顺手清掉 `search_results.pkl` 之类的记录文件（搜索前也会清理闲置 10 分钟以上的空壳目录）。

### 播放链路

客户端点歌时：

1. 先请求 `/api/url/{id}` 拿音源直链，直接连 CDN 播放（最快出声，服务端不中转）；请求时带 `/api/url` 返回的 headers（部分 CDN 需要 UA / Cookie）。
2. 直链拿不到、或播放器报错，自动回退 `/api/stream/{id}`。
3. `/api/stream/{id}` 未缓存时**边下边播**：从音源拉数据的同时写缓存并吐给播放器（实测首字节 ~0.5s，不用等整首下完）；中断会丢掉半截文件；缓存完成后转为按 Range 读本地文件，支持拖动进度。

手机端锁屏 / 控制中心的控制由 `just_audio_background` 提供，iOS 还依赖 [tool/platform_patches/ios/Runner/Info.plist](tool/platform_patches/ios/Runner/Info.plist) 里的 `UIBackgroundModes: audio`（构建时由 `apply_patches` 打上）。

## 客户端

Flutter 原生工程目录（`app/windows`、`app/linux`、`app/macos`、`app/ios`）不入库，用脚本生成；`flutter create` 不会覆盖已有的 `lib/` 代码。

```bash
# Windows
pwsh -File tool/bootstrap_platforms.ps1
# macOS / Linux
bash tool/bootstrap_platforms.sh

cd app
flutter run -d windows     # 或 linux / macos
flutter build windows --release
```

本机运行后端时，桌面端默认地址是 `http://127.0.0.1:8000`；iOS 真机要在右上角设置里改成局域网地址（如 `http://192.168.1.10:8000`）。

播放后端：iOS / macOS 用 just_audio 原生实现，Windows / Linux 通过 `just_audio_media_kit`（media_kit）播放。

## CI

- `server-ci.yml`：安装依赖 → 编译检查 → pytest。
- `build.yml`：
  - `静态检查` job 在三平台构建前先跑一次 `flutter analyze`；
  - `Windows / Linux / macOS / iOS` 矩阵构建，Flutter 版本固定在 `env.FLUTTER_VERSION`（固定版本才能让 `flutter-action` 缓存命中，否则每次都要重新保存缓存）；
  - 全部成功后由 `发布 Release` job 汇总产物并上传到 GitHub Release，标签取 `app/pubspec.yaml` 的版本号（如 `v1.0.0`）；手动触发时可自定义标签，重复运行会覆盖同名产物。

Release 产物：`tonicuisc-windows-x64.zip`、`tonicuisc-linux-x64.tar.gz`、`tonicuisc-macos.zip`、`tonicuisc-ios-unsigned.ipa`。

iOS 产物为未签名 `.ipa`，安装需要自行用 Xcode 重签。

## 备注

- Linux 构建需要 `libgtk-3-dev`、`libmpv-dev`、`clang`、`cmake`、`ninja-build`（CI 已安装）。
- `flutter analyze` 若因依赖版本报错，先执行 `flutter pub upgrade`。
