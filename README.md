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
| `TONICUISC_SEARCH_SIZE` | `10` | 每个音源返回数量 |
| `TONICUISC_SEARCH_SIZE_PER_PAGE` | `1` | 每个请求取几条；`1` = 10 条拆成 10 个请求并行拿 |
| `TONICUISC_SEARCH_THREADS` | `10` | 每个音源的并发请求数 |
| `TONICUISC_AUTH` | `1` | 设备 API Key 校验（`0` 关闭） |
| `TONICUISC_DEVICE_CODE_TTL` | `600` | 设备码有效期（秒） |
| `TONICUISC_PUBLIC_URL` | 空 | 对外地址，启动横幅打印登录链接用 |
| `TONICUISC_CACHE_DIR` | `server/.cache` | 下载缓存目录 |
| `TONICUISC_DB` | `<CACHE_DIR>/tonicuisc.db` | SQLite 数据库文件 |

接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态（**免鉴权**） |
| POST | `/api/device/start` | App 登记设备码，发起登录（**免鉴权**） |
| GET | `/api/device/status` | App 轮询登录状态，批准后返回一次 `api_key`（**免鉴权**） |
| GET | `/login` | 设备码登录网页（浏览器打开，**免鉴权**） |
| POST | `/login` | 提交设备码 / 选择账户 / 注册（**免鉴权**） |
| GET | `/api/me` | 当前账户 + 本设备信息 |
| GET | `/api/devices` | 已登录设备列表 |
| DELETE | `/api/devices/{device_id}` | 吊销设备（**直接删除记录**，它的 key 立刻失效） |
| GET | `/api/library/summary` | 三个列表的数量 + 喜欢/收藏的 id |
| GET | `/api/library/{kind}` | 列表内容（`kind` = `like` / `favorite` / `history`） |
| POST | `/api/library/{kind}` | 加入列表（`{"song_id": "..."}`，喜欢/收藏幂等，历史累加播放次数） |
| DELETE | `/api/library/{kind}/{song_id}` | 从列表移除 |
| DELETE | `/api/library/{kind}` | 清空列表 |
| GET | `/api/sources` | 可用音源 |
| GET | `/api/search?keyword=&sources=migu,kuwo&limit=50&refresh=false` | 搜索（`refresh=true` 跳过搜索缓存） |
| GET | `/api/history?limit=20` | 搜索历史（按关键词聚合） |
| GET | `/api/url/{id}` | 直链（best effort） |
| GET | `/api/lyric/{id}` | 歌词 |
| GET | `/api/stream/{id}` | 音频流，支持 HTTP Range |
| GET | `/api/download/{id}` | 附件下载 |

除上表标注「免鉴权」的以外，所有 `/api` 接口都必须带请求头 `X-API-Key`。

`{id}` 形如 `migu:123456`（实际是 `MiguMusicClient:600929000000096577`），由 `/api/search` 返回；服务端缓存 3 小时，过期需重新搜索。

### 登录（设备码流）

App 自己生成设备码，用户在网页上批准，服务端从不生成、也不打印任何码。

```
App                         浏览器(/login)                 服务端
 │ 生成 A1B1-C1D1 + poll_secret
 │ POST /api/device/start ───────────────────────────────► 记录 pending(10 分钟)
 │ 显示设备码
 │                       打开 https://…/login
 │                       输入 A1B1-C1D1 ─────────────────► 校验设备码
 │                       选账户+密码 / 注册新账户 ────────► 建设备、发 key（暂存）
 │ GET /api/device/status?code&poll_secret ───────────────► approved + api_key
 │ 存进系统安全存储          ◄──────────────────────────── 立刻清掉暂存的明文
```

要点：

- **设备码由 App 生成**（`A1B1-C1D1` 格式，去掉了容易看错的 `I/O/0/1`），10 分钟有效；
- **轮询密钥 `poll_secret` 只有 App 知道**，别人猜到设备码也拿不到 key；
- 账户密码用 **PBKDF2-HMAC-SHA256（20 万次迭代 + 随机盐）**存储；
- 设备 API Key 256 bit 随机，`devices` 表里**只有 `sha256(key)`**；批准到领取之间的明文只暂存在 `device_requests` 行里，App 取走立刻清空；
- App 收到 401 自动清凭据并回到登录页。

本机管理（不需要 key）：

```bash
python -m tonicuisc_server devices            # 设备 + 账户列表
python -m tonicuisc_server revoke <device_id> # 吊销某台设备
python -m tonicuisc_server cleanup            # 清理无账户 / 已吊销的设备记录
```

`cleanup` 会删掉两类记录：**没有关联账户的**（旧版配对流程留下的、账户已删除导致 `user_id` 悬空的）和**已吊销的**，另外顺手清过期登录请求。正常设备不动。

### 限速（防爆破）

`/login` 和 `/api/device/start` 都有限速，单进程内存滑动窗口：

| 限制 | 默认 | 作用 |
| --- | --- | --- |
| `/login` 提交 | 30 次/分钟/来源 IP | 防刷页面、防脚本 |
| `/api/device/start` | 30 次/分钟/来源 IP | 防刷设备码 |
| 密码错误 | 5 次/5 分钟/(IP + 用户名) | **防爆破**，锁定期内即使密码正确也拒绝 |

超限返回 429 并带 `Retry-After`，网页上显示「密码错误次数过多，请在 N 秒后再试」。

来源 IP 默认取反向代理写的 `X-Forwarded-For`（`TONICUISC_TRUST_PROXY=1`）。**如果直接把端口暴露到公网、前面没有反代，要设成 `0`**，否则攻击者可以伪造这个头绕过限速。

登录页**不会列出服务器上有哪些账户**，用户名靠手填——避免把账户名单泄露给任何能访问公网的人。

关闭鉴权：`TONICUISC_AUTH=0`（任何人可调，仅限完全可信环境）。

**这套东西的边界**（很重要）：

- 它解决的是「谁能调接口」+「按设备吊销」。**HTTP 明文下，链路上抓包的人可以直接拿走 key**——要真安全必须上 HTTPS。
- 设备被物理接触（越狱/root/调试器）就能读出 key，这是所有客户端凭据的共性。

### HTTPS（nip.io）

`deploy/Caddyfile` 里已经按你的公网 IP 写好：

```bash
caddy run --config deploy/Caddyfile     # https://106-35-196-104.nip.io
```

规则是把 IP 里的点换成横杠（`106.35.196.104` → `106-35-196-104.nip.io`），Caddy 自动申请并续期 Let's Encrypt 证书。**前提是 80/443 能从公网回连**——nip.io 只是把域名解析到那个 IP，Let's Encrypt 校验时是从公网反连你的机器，所以纯内网 IP（`192.168.x.x`）签不下来。

内网自签就用 mkcert：`mkcert 192.168.1.10`，把根证书装到手机上（iOS 要在「关于本机 → 证书信任设置」里手动打开），Caddyfile 里注释掉的部分有示例。

App 里服务器地址填 `https://106-35-196-104.nip.io` 即可。**注意公网暴露后：** `/login` 是免鉴权的，建议只开放 443、给 Caddy 加上访问限速，密码别用弱口令。

### 数据库（SQLite）

`server/.cache/tonicuisc.db`，Python 自带 `sqlite3`，没有额外依赖。三张表：

- `songs`：搜到的歌曲元信息 + musicdl 的 `SongInfo` 序列化结果（JSON）
- `searches` / `search_results`：搜索历史与当时的结果列表

**只存元信息，不把直链当长期有效**：酷我/咪咕的 `download_url` 是带签名的临时 token，所以库里给直链记了一个保守的 30 分钟有效期（`URL_TTL_SECONDS`）。重启服务后：

1. 内存缓存空了 → 从 `songs` 表恢复歌曲（实测重启后仍能拿到歌名/格式）；
2. 直链还在有效期内 → 直接播；
3. 直链过期 → 用「歌名 + 歌手」自动重搜一次换新链接；
4. 重搜也失败（比如那首歌下架了）→ 返回 404/502，客户端重新搜索即可。

搜索时顺手写入，`searches` 保留最近 500 条、`songs` 保留最近 5000 首，自动裁剪。

### 搜索过程

- **只请求选中的音源**：musicdl 的 `MusicClient.search()` 会把配置里所有音源都打一遍，所以服务端直接调用选中音源的 client；没勾的音源不会被请求。
- **并行拿结果**：`search_size_per_source=10` + `search_size_per_page=1` → musicdl 会生成 10 个搜索 URL（每页 1 条），再用 `search_threads` 个线程并发请求；多个音源之间也并行。
- **不打印进度条**：给 musicdl 传一个 `disable=True` 的 rich `Progress`，它就不再往控制台刷进度条。
- **耗时可见**：服务端每个 `/api` 请求打一行 `[access] GET /api/search 200 5.482s`；`/api/search` 另外在响应里返回 `elapsed`（服务端耗时）。客户端自己再量一次总耗时（含网络往返），显示成「搜索完成 · 用时 5.5 秒（服务端 2.9 秒）· 共 20 首」。

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

界面（底部三个 tab）：

- **登录页**（未登录时的首屏，`lib/pages/login_page.dart`）：显示 App 生成的设备码 `A1B1-C1D1`，主按钮「打开登录页并复制设备码」会直接弹出系统浏览器并把设备码放进剪贴板；后台每 2 秒轮询，用户在网页上批准后自动完成登录。
- **搜索 tab**（`lib/pages/search_page.dart`）：关键词搜索、音源勾选（咪咕 / 酷我）、显示「正在进行…」和用时、试听、下载、点红心加进「我喜欢」。
- **列表 tab**（`lib/pages/library_page.dart`）：**我喜欢 / 收藏 / 播放历史** 三个入口，各自显示数量，点进去是歌曲列表（可播放、单条移除、清空）。
- **我的 tab**（`lib/pages/profile_page.dart`）：账户名、本机设备名与设备 ID、服务器地址、重新登录、退出登录，以及**已登录设备列表**（可逐台吊销，删完即从列表消失）。
- **底部播放条**：封面缩略图、播放/暂停、进度拖动；**点一下展开全屏播放页**。
- **全屏播放页**（`lib/pages/now_playing_page.dart`）：大封面 + 喜欢/收藏按钮 + 歌词随进度高亮自动滚动（点歌词行可跳转播放位置）+ 进度条 + 播放/暂停/前后 10 秒；向下滑动或点顶部箭头收起。
- 手机端锁屏 / 控制中心控制见上方"播放链路"。

```bash
# Windows
pwsh -File tool/bootstrap_platforms.ps1
# macOS / Linux
bash tool/bootstrap_platforms.sh

cd app
flutter run -d windows     # 或 linux / macos
flutter build windows --release
```

App 的默认服务器地址是 **`http://192.168.5.37:8000`**，定义在 `app/lib/api/api_client.dart` 的 `kDefaultServerUrl`——要换服务器改这一行就行。用户改过之后会存在本机（「我的 → 服务器地址」），不会再回到默认值。

播放后端：iOS / macOS 用 just_audio 原生实现，Windows / Linux 通过 `just_audio_media_kit`（media_kit）播放。

## CI

- `server-ci.yml`：安装依赖 → 编译检查 → pytest。
- `build.yml`：
  - `静态检查` job 跑 `flutter analyze` + `flutter test`；
  - `Windows / Linux / macOS / iOS` 矩阵构建，Flutter 版本固定在 `env.FLUTTER_VERSION`（固定版本才能让 `flutter-action` 缓存命中，否则每次都要重新保存缓存）；
  - 全部成功后由 `发布 Release` job 汇总产物并上传到 GitHub Release；
  - **Release 标题 = 提交标题**（commit message 的第一行），Actions 列表里那一行也是提交标题（`run-name`）。
  - **Release 标签从提交标题里取 `v0.0.x`**：比如提交信息写 `v0.0.3 修复播放转圈`，tag 就是 `v0.0.3`、标题是整行。没写版本号时依次回退：推送的 tag → 手动输入 → `app/pubspec.yaml` 版本号。重复同一版本会更新标题并覆盖同名产物，不需要手动打 tag。

Release 产物：`tonicuisc-windows-x64.zip`、`tonicuisc-linux-x64.tar.gz`、`tonicuisc-macos.zip`、`tonicuisc-ios-unsigned.ipa`。

iOS 产物为未签名 `.ipa`，安装需要自行用 Xcode 重签。

## 备注

- Linux 构建需要 `libgtk-3-dev`、`libmpv-dev`、`clang`、`cmake`、`ninja-build`（CI 已安装）。
- `flutter analyze` 若因依赖版本报错，先执行 `flutter pub upgrade`。
