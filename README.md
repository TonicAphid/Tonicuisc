# Tonicuisc

自用音乐软件：**Python 后端（FastAPI + musicdl，音源为咪咕 / 酷我）** + **Flutter 客户端（Windows / Linux / macOS / iOS）**。

> 仅用于个人学习与自建服务，请勿商业使用或大规模分发。所有音频由第三方音源提供，本项目只做代理与整理。

## 功能一览

**后端**

- 搜索：只请求勾选的音源，**每个音源拆成多个并行请求**（musicdl 单请求内部是串行解析的），控制台不刷进度条，返回耗时
- 播放：优先给音源直链（客户端直连 CDN），拿不到就回退服务端代理；未缓存时**边下边播**，缓存后支持 HTTP Range 拖动
- 歌词 / 封面 / 附件下载
- 持久化：Redis 存歌曲元信息、搜索历史、搜索结果缓存、**喜欢 / 收藏 / 播放历史**（按账户隔离），服务端启动时自动拉起 Redis、退出时自动关掉
- 登录：**设备码流**（App 生成码，浏览器批准），每设备一把随机 API Key，服务端只存哈希
- 安全：密码 PBKDF2-HMAC-SHA256、限速防爆破、按设备吊销、忘记密码用**恢复码**重置
- 控制台：启动后底部常驻 `>` 提示符（像 MC 服务端），`h` 看命令、`q` 停服，设备 / 密码 / 恢复码 / 缓存都能直接操作

**客户端**

- 三个 tab：**搜索 / 列表 / 我的**
- 搜索：关键词 + 音源勾选、显示用时、试听、红心、下载
- 列表：**播放列表（当前队列）** + 我喜欢 / 收藏 / 播放历史
- 播放：**顺序 / 列表循环 / 单曲循环 / 随机**，上一首 / 下一首
- 全屏播放页：封面 + 歌词跟随高亮 + 进度条（拖动时继续播放，松手才跳）
- 锁屏 / 控制中心控制（iOS / Android），前台常亮，列表用搜到的封面

## 目录结构

```
server/                            后端
  tonicuisc_server/
    core/                          基础设施：config（环境变量、音源别名）/ constants / errors /
                                   logging / text / tls（证书、自签）/ runtime（线程池）
    storage/                       Redis：base（连接、key）+ songs / history / users / devices /
                                   device_requests / library / search_cache + redis_runtime
    auth/                          鉴权：tokens（密码、API Key 哈希）/ codes（设备码、恢复码）/
                                   accounts（注册、校验、找回）/ device_flow（设备码登录流）/
                                   ratelimit（滑动窗口限速、防爆破）
    services/                      业务：sources（只搜选中的音源）/ searching（搜索、分页）/
                                   downloads（下载、缓存、边下边播）/ artwork（封面）/
                                   qqmusic + MusicService 门面
    api/                           HTTP：app（装配）/ deps（服务与鉴权单例）/ middleware
                                   （鉴权闸门 + 访问日志）/ limiters / ranges /
                                   routes（health、account、library、media、search、login）
    web/                           /login 网页：pages（怎么画）+ login（点了之后干什么）
    cli/                           命令行：commands（管理命令）/ console（`>` 控制台）/
                                   launch（uvicorn + 原生 TLS）
    __main__.py                    python -m tonicuisc_server
  scripts/                         smoke.py / smoke_api.py / smoke_db.py / import_sqlite.py
  tests/                           pytest（不联网、不需要 musicdl、不需要真的 Redis）
  pytest.ini                       只在 tests/ 里收集
  start.ps1 / start.sh             一键启动
  requirements.txt / requirements-dev.txt
  .env.example                     环境变量样例

redis/                             Redis 二进制（首次启动自动下载，不进版本库）
  win/                             Windows：redis-server.exe
  linux/                            Linux：redis-server

app/                               Flutter 客户端
  lib/
    main.dart                      入口：只做平台初始化（media_kit / 后台播放 / 自签开关）
    version.dart                   构建时由 tool/apply_version.py 写入
    app/app.dart                   应用根 widget（Cupertino 主题 + 中文本地化）
    app/theme.dart                 iOS 系统蓝 + 浅色 / 深色两套 Cupertino 主题
    app/main_shell.dart            外壳：手机 / 桌面两套布局 + 登录闸门 + 401 收口
    app/layout.dart                响应式断点（窗口多宽算桌面）
    app/nav_sidebar.dart           桌面左侧导航栏（苹果风「图标 + 文字」宽侧边栏）
    app/glass_tab_bar.dart         手机底部「悬浮胶囊」tab 栏（iOS 26 风）
    app/legacy_material_host.dart  过渡壳：罩住还没迁到 Cupertino 的页面（迁完即删）
    core/api_client.dart           HTTP 客户端（接口方法、`X-API-Key`、下载）
    core/api_errors.dart           异常类型 + friendlyError（把报错翻成人话）
    core/server_config.dart        默认服务器地址 + 本机保存的地址
    core/credentials.dart          设备凭据（系统安全存储 + 桌面端兜底）
    core/self_signed.dart          自签证书开关（内网 HTTPS）+ 给 HttpClient 装双栈策略
    core/dual_stack.dart           双栈连接：IPv6 优先、v6 不通 250ms 内回落 IPv4
    features/auth/device_login.dart      设备码登录协议 + 轮询节奏
    features/auth/login_page.dart        登录页
    features/search/search_page.dart     搜索 tab
    features/search/artist_page.dart     歌手主页
    features/library/library_page.dart   列表 tab（播放队列 + 三个歌单）
    features/library/library_state.dart  喜欢/收藏/历史的本地缓存（乐观更新）
    features/player/player_controller.dart  队列、播放模式、上/下一首
    features/player/now_playing_page.dart   全屏播放页（封面 / 歌词 / 进度）
    features/player/queue_page.dart         播放队列（整页 / 底部弹层）
    features/player/player_bar.dart         底部播放条
    features/player/play_mode_icons.dart    播放模式图标
    features/profile/profile_page.dart      我的 tab（账户 / 设备 / 版本）
    models/song.dart               歌曲模型
    models/lyric.dart              LRC 解析
    shared/cover_cache.dart        QQ 封面缓存（按 id 批量问，同一首只问一次）
    shared/song_avatar.dart        封面（失败退回音源首字）
    shared/toast.dart              苹果风轻提示（替代 Material 的 SnackBar）
    shared/dialogs.dart            苹果风确认弹窗（showCupertinoDialog）
    shared/glass.dart              「液态玻璃」材质（模糊 + 半透明 + 高光描边 + 投影）
  test/                            单测：歌词解析、模型、文件名过滤；layout_test / shell_test 测响应式布局与外壳
  tool/probe_net.dart              网络探针：连不上服务器时先跑它（会先做一次「备案体检」，再测四条路）

tool/                              构建与自检脚本
  bootstrap_platforms.ps1/.sh      生成各平台原生工程 + 打补丁 + pub get
  apply_patches.ps1/.sh            覆盖 macOS 权限、iOS Info.plist
  apply_version.py                 从 tag / 提交标题写版本号
  check_dart_balance.py            本地自检 Dart 括号配平
  check_dart_imports.py            本地自检 Dart import 是否指到真实文件
  platform_patches/                原生配置补丁源文件

deploy/                            家里部署（外部 v4 + v6 双栈 → 内部 IPv4）
  Caddyfile.template               Caddy 配置模板（按当前环境生成实际配置）
  open-firewall.ps1                放行入站 80 / 443（管理员，可 -Remove 撤销）
  update_dns.py                    维护 A / AAAA 记录（阿里云解析；--update 才真改，--check 只读）
  .aliyun.env.example              AK 配置样例（真文件 .aliyun.env 已 gitignore，用 ddns set-key 填）
.github/workflows/                 server-ci.yml（后端）/ build.yml（客户端）/ macos.yml（macOS 单独构建）
```

## 快速开始

### 后端

```bash
cd server
pip install -r requirements.txt
python -m tonicuisc_server            # 默认 0.0.0.0:8000
```

Windows 也可以直接 `pwsh -File server/start.ps1`。启动后控制台会打印登录地址：

```
========================================================
  Tonicuisc 服务已启动
  登录页: https://uisc.tonicaphid.cn/login
  在 App 里点「登录」拿到设备码，再用手机浏览器打开上面的链接
========================================================
```

打开 `http://<地址>:8000/docs` 可以直接看接口。

### 客户端

原生工程目录（`app/windows`、`app/linux`、`app/macos`、`app/ios`）不入库，用脚本生成；`flutter create` 不会覆盖已有的 `lib/` 代码。

```bash
# Windows
pwsh -File tool/bootstrap_platforms.ps1
# macOS / Linux
bash tool/bootstrap_platforms.sh

cd app
flutter run -d windows     # 或 linux / macos
flutter build windows --release
```

**默认服务器地址是 `https://uisc.tonicaphid.cn`（不带端口）**，定义在 `app/lib/core/server_config.dart` 的 `kDefaultServerUrl`——要换服务器改这一行即可。用户在本机改过之后会存在本地设置里，不会再回到默认值；改地址的入口是「我的」页**长按版本号**（平时隐藏，App 不再要求用户填 IP）。同一个弹窗里还有「**允许自签证书**」开关，内网自签 HTTPS 时才需要打开。

> **为什么不带端口、两族还都通**：`uisc` 上是**一条 A + 一条 AAAA**——
> **A = 一个能用的中转节点 IP**（**手填**，见 `deploy/free-frp-relay.md`；换节点只改这一条）、
> **AAAA = 家里的 IPv6**。所以：
>
> - **有 IPv6 的客户端** → 连**家里**的 `443`（**不过隧道**，最短路径）
> - **没有 IPv6 的客户端** → 连**节点**的 `443`，节点按 SNI 送进隧道 → 家里
>
> 客户端**优先 IPv6**（`app/lib/core/dual_stack.dart`：v6 先连，250ms 没连上就把 v4 也发出去，
> 谁先连上算谁；**要是 TCP 连上了但握手被掐，还会换个家族整套重来**）——正好是「v6 走直连、v6 不通走隧道」。
>
> ⚠️ **节点别选大陆的**（2026-10-18 实测）：大陆机器上跑**未备案**域名的 443 会被阿里云
> **备案拦截**——现象正是「浏览器能打开、App 连不上」。详见 `deploy/free-frp-relay.md`
> 的「大陆节点会被备案拦截」一节。
>
> ⚠️ 这套要求 **DDNS 只刷 AAAA**（`ddns install --only v6`）：A 得一直指着节点；被刷成家里的 v4（家里 443 被运营商封）的话，没 v6 的客户端就**全连不上**。详见下面「HTTPS → 家里的两个坑」。

### Windows 本机第一次跑（三个坑 + 一个噪音）

全新机器上 `flutter run -d windows` 必踩三个坑，**都跟 App 代码无关**：

1. **Visual Studio 组件**：要「使用 C++ 的桌面开发」工作负载，而且**必须勾上 ATL**（单个组件里的「适用于最新 v143 生成工具的 C++ ATL (x86 和 x64)」）。工程里 8 个 Windows 插件只有 `flutter_secure_storage_windows` 需要它，缺了会在 `flutter_secure_storage_windows_plugin.cpp` 报 `C1083: 无法打开包括文件: "atlstr.h"`。
2. **mpv 下载**：`media_kit_libs_windows_audio` 的 CMake 会从 GitHub 拉 `mpv-dev-x86_64-*.7z`（国内直连必失败）。手动把文件放进 `app/build/windows/x64/` 就能跳过——CMake **只校验 MD5 == `cd738e16e2a19626d7cfa48801524f8c`**，对上就不下载。镜像用 `https://gh-proxy.com/`（`ghproxy.net` 不行）。**别 `flutter clean`**，会把它删掉。
3. **CMake 脏缓存（最阴的一个）**：`windows/CMakeLists.txt` 里修 `CMAKE_INSTALL_PREFIX` 的那段用 `if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)` 包着——这个条件**只在第一次 configure 为真**，而那段又排在 `include(flutter/generated_plugins.cmake)`（第 58 行）**后面**。所以只要第一次 configure 因为上面第 2 条失败过，缓存里就永久留下默认前缀 `C:/Program Files/<工程名>`，之后每次 INSTALL 都往那儿拷 → `error MSB3073`。修法：删掉 `app/build/windows/x64/CMakeCache.txt` 重新 configure。

另外一个**不用管**的噪音：控制台刷 `[ERROR:flutter/shell/platform/common/accessibility_bridge.cc(114)] Failed to update ui::AXTree, error: N will not be in the tree and is not the new root`。这是 Flutter 引擎在 Windows 上的已知 bug（[#98099](https://github.com/flutter/flutter/issues/98099)、[#182444](https://github.com/flutter/flutter/issues/182444)，后者给的复现条件是 **`ListView` + Material `Tooltip`**），**只是 debug 控制台的日志**，界面和功能都不受影响，release 版没有控制台。仓库里的 `Tooltip` 全部来自还没迁到 Cupertino 的 `IconButton(tooltip: ...)`，**逐页迁移做完它自然就没了**。

### 第一次登录

1. App 打开就是登录页，自动生成一个设备码（`A1B1-C1D1`）
2. 点「打开登录页并复制设备码」→ 弹出系统浏览器并已复制设备码
3. 在网页里粘贴设备码 → 注册新账户或登录已有账户 → 批准
4. App 每 2 秒轮询，批准后自动进入，密钥写进系统安全存储；**Windows / Linux 上会额外在本机普通设置里留一份明文副本**（见下面「设备与鉴权」的取舍说明），移动端不留

## 后端配置

见 `server/.env.example`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TONICUISC_HOST` | `127.0.0.1` | 监听地址。默认**只监听 IPv4 回环**——家里部署时由 Caddy 反代过来，服务端不该直接对外。想让局域网 / 外网直连（不装 Caddy）再显式改成 `0.0.0.0` |
| `TONICUISC_ENV` | 空 | 当前环境（`uisc` 正式 / `tuisc` 测试）。决定对外域名、启动横幅、DDNS 目标；不填时**首次启动会问一次**并写进 `server/.env`。改：`python -m tonicuisc_server env tuisc`，或 `env --ask` 现场问一遍 |
| `TONICUISC_PORT` | `8000` | 端口 |
| `TONICUISC_SOURCES` | `migu,kuwo` | 启用音源 |
| `TONICUISC_SEARCH_SIZE` | `5` | 每个音源的抓取下限（实际按「要几条 ÷ 音源数」分摊） |
| `TONICUISC_SEARCH_SIZE_PER_PAGE` | `1` | 每个请求取几条的**下限**；实际每页 = `max(该值, ceil(要几条 ÷ 并发数))` |
| `TONICUISC_SEARCH_THREADS` | `10` | 每个音源的并发请求数（也是「要 15 条 → 每页 2 条 → 8 个请求」里的分母）。实测酷我 15 条：10 路比 5 路快约 30%，上游抖动时差距更大；怕被限流设回 `5` |
| `TONICUISC_SEARCH_PAGE_SIZE` | `15` | 客户端一页多少条（滑到底再要下一页） |
| `TONICUISC_SEARCH_MAX` | `60` | 一次搜索最多抓多少条（分页上限） |
| `TONICUISC_AUTH` | `1` | 设备 API Key 校验（`0` 关闭） |
| `TONICUISC_DEVICE_CODE_TTL` | `600` | 设备码有效期（秒） |
| `TONICUISC_TRUST_PROXY` | `1` | 限速是否按反代写的 `X-Forwarded-For` 取来源 IP |
| `TONICUISC_QQ_COVER` | `1` | 用 QQ 音乐补封面（咪咕/酷我的封面经常糊） |
| `TONICUISC_QQ_COVER_THREADS` | `8` | 补封面时的并发数（QQ 单次要 3~4 秒，8 路比 4 路快一倍） |
| `TONICUISC_QQ_COVER_WAIT` | `25` | `/api/covers` 最多等正在跑的封面查询多久（秒）；到点还没结果的 id 先不返回，客户端过几秒再问 |
| `TONICUISC_QQ_COVER_CACHE` | `2048` | QQ 封面查询的 LRU 记忆条数 |
| `TONICUISC_SEARCH_TTL` | `86400` | 搜索结果缓存在 Redis 里保留多久（秒） |
| `TONICUISC_SEARCH_SOFT_TTL` | `600` | 软刷新窗口：客户端带 `refresh=true` 时，比这新的缓存直接返回并后台更新 |
| `TONICUISC_REDIS_URL` | 空 | 完整 Redis 地址；填了服务端只连不管（不启动也不关闭） |
| `TONICUISC_REDIS_HOST` | `127.0.0.1` | 没填 URL 时连的地址（也是托管实例的 bind 地址） |
| `TONICUISC_REDIS_PORT` | `6390` | 没填 URL 时连的端口（避开常见 6379，少和别人打架） |
| `TONICUISC_REDIS_DB` | `0` | 用哪个库 |
| `TONICUISC_REDIS_SERVER` | 空 | 指定 `redis-server` 可执行文件 |
| `TONICUISC_REDIS_DIR` | `<仓库>/redis` | 二进制的存放目录（自动下载到其下的 `win/` 或 `linux/`） |
| `TONICUISC_REDIS_AUTOSTART` | `1` | 启动服务时自动拉起 Redis（`0` = 只连现成的） |
| `TONICUISC_REDIS_DOWNLOAD` | `1` | 找不到 `redis-server` 时自动下载 |
| `TONICUISC_REDIS_APPENDONLY` | `1` | 托管实例开 AOF 持久化 |
| `TONICUISC_PUBLIC_URL` | 空 | 对外地址，启动横幅打印登录链接用（配了就以它为准） |
| `TONICUISC_TLS_CERT` | 空 | 证书链 PEM 路径；和 `TONICUISC_TLS_KEY` 两个都填才开**原生 HTTPS** |
| `TONICUISC_TLS_KEY` | 空 | 私钥 PEM 路径 |
| `TONICUISC_THREADS` | `64` | `asyncio.to_thread` 的线程池大小（搜索 / 下载 / Redis 调用都走它；默认只有 `min(32, cpu+4)`） |
| `TONICUISC_CACHE_DIR` | `server/.cache` | 下载缓存目录（Redis 数据在 `<CACHE_DIR>/redis`） |
| `TONICUISC_DB` | `<CACHE_DIR>/tonicuisc.db` | 旧 SQLite 库位置，只给 `scripts/import_sqlite.py` 迁移用 |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态（**免鉴权**） |
| GET | `/health` | 上面那个的兼容别名（本机探活按约定打这个路径，**免鉴权**） |
| POST | `/api/device/start` | App 登记设备码，发起登录（**免鉴权**；带 `device_id` = 重新配对，服务端就地复用旧记录、旧 key 作废，不新开条目） |
| POST | `/api/device/status` | App 轮询登录状态，批准后返回一次 `api_key`（**免鉴权**；`user_code` + `poll_secret` 都在 **JSON 请求体**里，不走 query——query 会进访问日志） |
| GET / POST | `/login` | 设备码登录网页，含注册 / **忘记密码（恢复码重置）**（**免鉴权**） |
| GET | `/api/me` | 当前账户 + 本设备信息 |
| GET | `/api/devices` | 已登录设备列表（**只列本账户的** + 没有账户的旧设备；别的账户看不到） |
| DELETE | `/api/devices/{device_id}` | 吊销设备（**直接删除记录**，它的 key 立刻失效；只能删本账户/无主的，别人的设备回 404） |
| GET | `/api/library/summary` | 三个列表的数量 + 喜欢/收藏的 id |
| GET | `/api/library/{kind}` | 列表内容（`kind` = `like` / `favorite` / `history`） |
| POST | `/api/library/{kind}` | 加入列表（喜欢/收藏幂等，历史累加播放次数；歌曲不在库里回 404） |
| DELETE | `/api/library/{kind}/{song_id}` | 从列表移除 |
| DELETE | `/api/library/{kind}` | 清空列表 |
| GET | `/api/sources` | 可用音源 |
| GET | `/api/search?keyword=&sources=migu,kuwo&limit=50&refresh=false` | 搜索（`refresh=true` = 想刷新：缓存比 `TONICUISC_SEARCH_SOFT_TTL` 新就直接返回并后台更新） |
| GET | `/api/artist?name=周杰伦&sources=&limit=50` | 歌手主页：这个歌手在音源上能搜到的歌 |
| GET | `/api/history?limit=20` | 搜索历史（按关键词聚合） |
| GET | `/api/url/{id}` | 音源直链（best effort） |
| GET | `/api/lyric/{id}` | 歌词（LRC 文本） |
| GET | `/api/stream/{id}` | 音频流，支持 HTTP Range |
| GET | `/api/download/{id}` | 附件下载 |

除标注「免鉴权」的以外，所有 `/api` 接口都必须带请求头 `X-API-Key`。

`{id}` 形如 `MiguMusicClient:600929000000096577`，由 `/api/search` 返回；内存缓存 3 小时，重启后从 Redis 恢复。

## 登录（设备码流）

App 自己生成设备码，用户在网页上批准，**服务端从不生成也不打印任何码**。

```
App                         浏览器(/login)                 服务端
 │ 生成 A1B1-C1D1 + poll_secret
 │ POST /api/device/start ───────────────────────────────► 记录 pending(10 分钟)
 │ 显示设备码，按钮弹出浏览器并复制码
 │                       粘贴设备码 ──────────────────────► 校验设备码
 │                       填用户名+密码 / 注册新账户 ──────► 建账户、建设备、发 key（暂存）
 │ POST /api/device/status{code, poll_secret}（1s→2s→3s）─► approved + api_key
 │ 存进系统安全存储          ◄──────────────────────────── 立刻清掉暂存的明文
```

要点：

- **设备码由 App 生成**（`A1B1-C1D1`，字母表去掉了容易看错的 `I/O/0/1`，服务端 `auth/codes.py` 用的是同一张表），10 分钟有效、用过即废；
- **轮询密钥 `poll_secret` 只有 App 知道**，而且**走 POST 请求体**（以前走 query，会原样进 uvicorn / Caddy 的访问日志，等于把这个秘密写进了日志）；别人猜到设备码也拿不到 key；
- **轮询节奏自适应**：刚登记 1 秒一次（批准完马上能进），越等越慢到 3 秒；连续几次网络失败会把原因显示出来，而不是永远停在「等待批准…」；
- **`claimed` 会被认出来**：批准过但 key 已被取走过一次（比如上次拿到了 key 却没存住）时，登录页会立刻提示「这次登录已经完成过了，点换一个设备码」，不会让用户干等十分钟；
- 账户密码用 **PBKDF2-HMAC-SHA256（20 万次迭代 + 随机盐）**存储；
- **忘记密码走恢复码**：`K7MP2-QX9RT-3WZHB-ND5LF` 这样 4 组 × 5 位（100 bit，去掉了看混的 `0/O/1/I`），同样用 PBKDF2 存、明文只在生成时给一次；登录页点「忘记密码」输入它就能重设密码，**用过一次自动作废并换新的**。没接邮件/短信，这是唯一能证明「你是账户主人」又不落到局域网手里的凭证，所以**丢了就得回服务器上重新生成**（见下面的 `recovery-code`）；
- 设备 API Key 256 bit 随机，`devices` 表里**只有 `sha256(key)`**；批准到领取之间的明文只暂存在 `device_requests` 行里，App 取走立刻清空；
- 登录页**不列出服务器上有哪些账户**，用户名靠手填，避免泄露账户名单；**注册也过同一把防爆破锁**（撞名撞多了会被临时拒绝），否则「用户名已被占用」本身就是个现成的枚举接口，登录那边费力用假哈希抹平耗时就白做了；
- App 收到 401 自动清凭据并回到登录页——**搜索 / 翻页 / 列表增删 / 歌手页 / 播放页的红心 / 歌词 / 设备列表**每个入口都会走这条路（并发来好几个 401 也只清一次、只提示一次）；
- **桌面端明文副本的取舍**：Windows / Linux 的安全存储偶发「这个进程写进去、下个进程读不回来」，会导致每次启动都得重新配对，所以 `credentials.dart` 读回校验失败会退回普通存储，并**在桌面端额外留一份明文副本**。代价是能读到 App 沙盒设置的人也能看到这把 key（`X-API-Key` 在 HTTP 明文链路上本来就能被抓走，见下面「这套东西的边界」）。「我的」页会在真退回普通存储时给出提示，安全存储恢复后提示自动消失。

本机管理（不需要 key）。**完整清单看 `python -m tonicuisc_server --help`**：

```bash
# 设备 / 账户
python -m tonicuisc_server devices            # 设备 + 账户列表（简写 list）
python -m tonicuisc_server revoke <device_id> # 吊销某台设备（= 删记录）
python -m tonicuisc_server set-password <用户名>   # 重置密码（不用问旧密码，交互输入两次）
python -m tonicuisc_server recovery-code <用户名>  # 生成新的恢复码（旧的作废，明文只显示一次）
python -m tonicuisc_server cleanup            # 清理无账户 / 已吊销的设备记录

# 运行时
python -m tonicuisc_server redis              # 看一眼 Redis（地址 / 版本 / 是不是托管实例）
python -m tonicuisc_server clear-cache        # 清掉搜索结果缓存（下次搜索重新联网）

# 部署 / HTTPS（详见下面「HTTPS」一节）
python -m tonicuisc_server env [uisc|tuisc]   # 看 / 改当前环境
python -m tonicuisc_server caddy --status     # Caddy：--download / --generate / --validate
                                              #         --start / --log
python -m tonicuisc_server acme --status      # Let's Encrypt 证书：--issue / --force / --staging
python -m tonicuisc_server ddns set-key       # 阿里云解析：set-key / check / run / install
                                              #              / status / remove
python -m tonicuisc_server cert <域名或IP>      # 生成**自签**证书（内网 HTTPS 用）
```

启动后控制台底部会一直留一个 `>` 提示符（跟 MC 服务端那样），直接敲命令——简写、
全名、唯一前缀都行，`h` 随时看帮助：

```text
h  帮助          l   看设备和账户        r <ID>  吊销设备（ID 可只输前几位，唯一即可）
p <用户> 重置密码  c <用户> 生成恢复码    x   清理无主 / 已吊销的设备记录
rd  Redis 状态    cc  清搜索缓存          log off 关掉请求日志（控制台更清净）
q   停止服务（Ctrl+C 等价）
```

日志把 `>` 顶掉的问题也处理了：控制台空闲时，任何输出（uvicorn 的 `INFO:`、应用的
`[access]` / `[timing]`）安静约 60ms 后都会把 `>` 自动补画回行尾——多行输出只在末尾
补一个，命令执行期间不补，敲字回显不会被打断。

### 限速（防爆破）

| 限制 | 默认 | 作用 |
| --- | --- | --- |
| `/login` 提交 | 30 次/分钟/来源 IP | 防刷页面、防脚本 |
| `/api/device/start` | 30 次/分钟/来源 IP | 防刷设备码 |
| 密码错误 | 5 次/5 分钟/(IP + 用户名) | **防爆破**，锁定期内即使密码正确也拒绝（**恢复码填错也记在这里**） |

超限返回 429 并带 `Retry-After`，网页上显示「密码错误次数过多，请在 N 秒后再试」。

来源 IP 默认取反向代理写的 `X-Forwarded-For`（`TONICUISC_TRUST_PROXY=1`）。**如果把端口直接暴露到公网、前面没有反代，要设成 `0`**，否则可以伪造这个头绕过限速。

关闭鉴权：`TONICUISC_AUTH=0`（任何人可调，仅限完全可信环境）。这时没有「设备 / 账户」概念，收藏类接口把数据归到固定的 `local` 账户下——否则中间件不写设备信息，`/api/library/*` 会一律 400。

**这套东西的边界**（很重要）：

- 它解决「谁能调接口」+「按设备吊销」。**HTTP 明文下，链路上抓包的人可以直接拿走 key**——要真安全必须上 HTTPS。
- 设备被物理接触（越狱 / root / 调试器）就能读出 key，这是所有客户端凭据的共性。

## HTTPS（家里部署：外部 v4 + v6 双栈 → 内网 IPv4）

家里部署、不买服务器，外面**同时**用 IPv4 和 IPv6（哪个通用哪个），内部转发走 IPv4 回环：

```
手机 / 浏览器 ──公网 v4 或 v6 : 443──► Caddy（部署机）──IPv4 127.0.0.1:8000──► tonicuisc_server
```

**服务端自己只监听 IPv4 回环，永远不直接对外；外面只能从 Caddy 的 443 进来。**
`deploy/Caddyfile` 由 `deploy/Caddyfile.template` 按当前环境生成。

**Caddy 会跟着服务端一起启动**（`TONICUISC_CADDY_AUTOSTART`，默认开），不用再开第二个窗口：

```bash
python -m tonicuisc_server            # 起服务端，顺手把 Caddy 也拉起来
python -m tonicuisc_server caddy --start   # 或者单独起 Caddy（前台，日志更直观）
```

起不来**不影响服务端**，只打印原因（端口被占 / 入站没放行都能边跑边修）；
服务端退出时会把 Caddy 一起收摊。只想局域网直连、不挂 HTTPS 就设 `TONICUISC_CADDY_AUTOSTART=0`。

> **证书存在项目里**（`deploy/.caddy/`，已 gitignore），不是 `%AppData%\Caddy`。
> 这是刻意的：受限环境下 `%AppData%` 建不了目录，Caddy 会**直接放弃申请证书**——
> 表现是 443 握不了手、浏览器报 `ERR_SSL_PROTOCOL_ERROR`，看着像「证书不行」，其实压根没申请。
> 放在项目里还能顺带让备份 / 搬机器 / 删干净都只动一个目录。

### 证书：Let's Encrypt 签发，但走 DNS-01（不需要入站端口）

**区别只在「怎么向 Let's Encrypt 证明域名是你的」**，证书本身都是 Let's Encrypt 签的：

| 验证方式 | 谁来做 | 要求 |
| --- | --- | --- |
| HTTP-01 / TLS-ALPN-01 | Caddy 自带 | **外网必须能连到 80 / 443**（要端口映射 + 运营商没封） |
| **DNS-01**（默认） | lego（ACME 客户端） | 只要能往解析里写 TXT 记录——**有阿里云 AK 就行，零入站要求** |

大陆家用宽带经常封 80/443，所以默认走 DNS-01：

```bash
python -m tonicuisc_server acme --status                   # 看证书 / lego / AK 状态
python -m tonicuisc_server acme --issue                    # 签发（复用 deploy/.aliyun.env 的 AK）
python -m tonicuisc_server acme --issue --force --staging   # 先用测试环境试流程
```

签好后 Caddy 只终止 TLS（Caddyfile 里写 `tls <crt> <key>`），**Caddy 不再自己跑 ACME**——
顺带的好处是它那串 `challenge failed` + 几十行 Go 调用栈的日志彻底消失
（另外 Caddyfile 里也加了 `stacktrace delete`，把这类噪音统一掐掉）。

> **为什么不让 Caddy 直接做 DNS-01**：Caddy 官方发行版**不含任何 DNS 模块**
> （`caddy list-modules` 里 `dns.providers` 是 0），要做就得自己编译一个。
> **lego 不是 CA**，它只是 ACME 客户端（相当于 certbot 那个角色），
> 证书仍然是 Let's Encrypt 签发的。

### Caddy 的日志去哪了

**默认写文件**（`deploy/.caddy/runtime.log`），不刷控制台——启动时会打十几行 INFO，
最前面还有两行 `maxprocs` / `GOMEMLIMIT`，混在服务端横幅里很乱。

```bash
python -m tonicuisc_server caddy --log      # 看运行时日志最后 40 行
python -m tonicuisc_server caddy --start    # 前台启动（默认显示日志；加 --quiet 只写文件）
```

> 那两行 Go 运行时信息是**运行时直接写 stderr 的**，不经过 Caddy 的日志系统，
> 所以只调 Caddyfile 里的日志级别**掐不掉**，必须重定向子进程的输出。
> 想让后台启动也实时刷屏：设 `TONICUISC_CADDY_LOG=console`。
> 起不来时会把日志尾部直接附在错误信息里，不用自己去翻文件。

### 两个域名

| 域名 | 用途 | 绑哪台机器 | 记录 |
| --- | --- | --- | --- |
| `uisc.tonicaphid.cn` | **正式**，App 全部访问走它 | 部署机（清凉那台） | **A = 中转节点 IP**（**手填**，必须是**境外**节点）+ **AAAA = 家里稳定 v6** |
| `tuisc.tonicaphid.cn` | **测试**，自己调试用 | 这台测试机 | **目前一条记录都没有**（要用得先加 A / AAAA） |

> `uisc` 是 **A + AAAA 混着用**，两族都走 **443**（客户端地址**不带端口**），但**走的路不一样**：
>
> - **v6 → 直连家里**（`curl -6` 实测 `remote_ip=240e:318:3d2d:2900:4637:e6ff:fec1:c0c5`）
> - **v4 → 进 frp 隧道**（连节点的 443，节点按 SNI 送进隧道 → 家里）
>
> ⚠️ **A 现在指的是你自己的杭州 ECS `47.97.176.166`，而它被备案拦了**（2026-10-18 实测：
> 明文 80 回 `403 Non-compliance ICP Filing`；443 对**不发 PQ 密钥共享**的客户端直接 RST）。
> **备案批下来 A 就能用**；在那之前，没有 v6 的客户端（手机蜂窝）会连不上，
> 而同一条路上的**浏览器可能照样能开**（Chrome 发 PQ，把 SNI 挤出第一个包，检查器读不到就放过）——
> **别把「浏览器能开」当成「App 该能开」**。想立刻能用：A 换成**境外**节点。
>
> **A 为什么直接写节点 IP、不用 CNAME**：因为 **CNAME 不能和 AAAA 共存**，而 AAAA 这条正是
> 「v6 不过隧道、直连家里」的关键。`curl -4` 通也就顺带证明：家里 v4 的 443 被封**不影响**
> 没 v6 的客户端 —— 它们走的是**节点**的 443。
>
> 客户端只认 `uisc` 这一个名字，两族谁先谁后由 `app/lib/core/dual_stack.dart` 决定（**v6 优先**、250ms 回落 v4）。
> 想改成「纯直连 + 非标端口 8443」见下面「家里的两个坑」——**那是备选，没启用**。

### 一次性准备

1. **DNS（阿里云解析）**：`tonicaphid.cn` 的 NS 是 `dns13/dns14.hichina.com`，记录在**阿里云云解析 DNS** 里改。
   `uisc` 现在是 **A 手填节点 IP + AAAA 脚本刷家里 v6**（见上表）——**A 不能写成 CNAME**，
   否则 AAAA 加不上。要走「纯直连 + 8443」就两条都填家里地址（`--only both`）；
   要退回「纯隧道」就删掉 AAAA（或改回 CNAME）。**CNAME 和 A/AAAA 不能共存**。
2. **放行入站 443（和 80）**：
   - Windows 防火墙：`pwsh -File deploy/open-firewall.ps1`（管理员），只加两条具名规则，可 `-Remove` 撤销；
   - 光猫 / 路由器：**IPv6 防火墙放行 TCP 80/443** —— 现在 v6 是**直连家里**的，必须放行。
     **IPv4 的 443 端口映射不需要**：家宽 443 入站本来就被运营商封，v4 客户端走的是 frp 隧道
     （家里主动连出去，不依赖入站）。（哪天整体退成「纯隧道」，两族入站就都不需要了。）
3. **服务端 `server/.env`**：
   ```
   TONICUISC_HOST=127.0.0.1                        # 只监听 IPv4 回环
   TONICUISC_PUBLIC_URL=https://uisc.tonicaphid.cn  # 启动横幅 / 登录链接用它
   ```
4. **装 Caddy 并启动**（这台机器没有 winget/scoop/choco，手动下载）：
   从 https://caddyserver.com/download 下 Windows amd64 的 `caddy.exe`，放到 `deploy/`（或加进 PATH），然后
   ```
   caddy run --config deploy/Caddyfile
   ```
   Caddy 会自动向 Let's Encrypt 申请证书并续期。

验证：从手机（**走流量，别连家里 Wi-Fi**）打开 `https://uisc.tonicaphid.cn/api/health`。

### 地址会变，要定期刷 DNS

家宽的地址不是永久的：**IPv6 前缀**重播就可能换一段，**IPv4** 也可能变。所以记录要定期刷新：

```bash
python deploy/update_dns.py                                  # uisc，只探测并打印
python deploy/update_dns.py --record tuisc.tonicaphid.cn      # 测试域名
python deploy/update_dns.py --update                          # 真的写进阿里云解析
python deploy/update_dns.py --only v6                         # 只维护 AAAA
```

**⚠ 要在托管那个域名的那台机器上跑**：脚本探测的是「本机」的公网地址。
uisc 在部署机跑、tuisc 在这台测试机跑——两台机器的 IPv6 后缀不同（可能共用同一个 IPv4），
在一台上跑另一个域名的更新会把记录写成那台机器的地址。

阿里云 AK 放在 `deploy/.aliyun.env`（已 gitignore）。**一条命令填好并当场验证**：

```bash
python -m tonicuisc_server ddns set-key     # 交互输入 AK ID / Secret（Secret 不回显）
python -m tonicuisc_server ddns check       # 只读：看这个域名现有记录（验证 AK + 权限）
```

`set-key` 写完会立刻用只读接口试一次，所以两类常见问题当场就能分清：
`InvalidAccessKeyId` = ID/Secret 抄错了；`Forbidden` = 用户没给解析权限。

去哪拿 AK：**RAM 控制台 → 用户 → 点你的用户 → 认证管理 → 创建 AccessKey**
（Secret 只显示一次）；再去 **权限管理 → 新增授权 → 搜 `AliyunDNSFullAccess`**。
建议用 **RAM 子账号**、权限只给云解析 DNS。脚本用阿里云 DNS API 的 HMAC-SHA1 签名，不需要装 SDK。

> **定时任务必须用文件里的 AK**：Windows 计划任务 / systemd / cron 都看不到你 shell 里
> 临时设的环境变量，所以 `ddns install` 会检查文件。密钥也不会被抄进计划任务定义或
> crontab 里（脚本自己读文件），换 AK 不用重装任务。

**⚠ 稳定地址 vs 临时地址（最容易填错的地方）**：Windows 开了隐私扩展后网卡上有两个全局 IPv6——
**稳定地址**（`SuffixOrigin=Link`，如 `...:fe45:2163:abb8:c9c0`）接口标识固定，**对外服务要用它**；
**临时地址**（`SuffixOrigin=Random`，如 `...:e95a:b195:7846:d960`）会轮换，而且**系统默认用它出网**，
所以 `api6.ipify.org` 探测到的是临时地址。两者**前缀相同**（同一 `/64`）属于正常，**不代表挂了代理**；
脚本以**本机网卡的稳定地址**为准，只有**前缀都不同**时才警告（那才是走了 VPN / 代理出口）。

### 家里的两个坑

- **80 / 443 入站被运营商封**：住宅宽带很常见（工信部政策——家宽开 443 得企业专线 + 备案，办不下来）。

  **⚠️ 现状先说清楚（2026-10-18 实测）**：`uisc` 上是
  **A = 中转节点 IP（手填）+ AAAA = 家里稳定 v6**，两族都走 **443**、**不带端口**：

  - **有 IPv6** → 直连家里（`curl -6` 实测 `remote_ip=240e:318:3d2d:2900:4637:e6ff:fec1:c0c5`）✅
  - **没有 IPv6** → 连节点的 `443`，节点按 SNI 送进隧道 → 家里。**取决于 A 指的是谁**：
    **境外节点 ✅ / 大陆节点 ✗**（备案拦截，见 `deploy/free-frp-relay.md`）

  这样「家里 v4 的 443 被封」就绕过去了 —— **没 v6 的客户端走的是节点的 443**，不是家里的。

  > **维护要点**：DDNS 定时器只能用 **`--only v6`**（只刷 AAAA）：
  > `python -m tonicuisc_server ddns install --only v6`。
  > `ddns.py` 里也是这么写的：「家里没公网 v4 时可以 `ddns run --only v6`：只刷 AAAA、不碰 A」。
  > 要是用默认的 `both` / `v4` 把 **A 刷成家里的 v4**，没 v6 的客户端会**全部连不上**。
  > 节点 IP 换了要自己手改 A（TTL 600，10 分钟内生效）。

  另一条路（**备选，没启用**）：**家里 v4 直连 + 非标端口 8443** —— 家里 v4 也有公网、而且
  愿意开非标端口时才值得做。要切 8443，下面六步**缺一不可**：

  1. `server/.env` 里加两行。**证书不用重签**——lego 走 DNS-01，跟端口无关：
     ```
     TONICUISC_HTTPS_PORT=8443
     TONICUISC_PUBLIC_URL=https://uisc.tonicaphid.cn:8443
     ```
  2. 重新生成 + 重启 Caddy（站点地址会变成 `uisc.tonicaphid.cn:8443`）：
     ```
     python -m tonicuisc_server caddy --generate    # 先看一眼生成的 deploy/Caddyfile
     python -m tonicuisc_server caddy --start
     ```
  3. **路由器 / 光猫：两件事，缺一不可**
     - **IPv4**：端口映射（有的叫「虚拟服务器」）——`外部端口 8443` → `192.168.5.43` + `内部端口 8443`，
       协议 **TCP**。只填 IP 不填端口不算数。
     - **IPv6**：**不需要端口映射**（IPv6 没有 NAT，端到端直连），只要在路由器的 **IPv6 防火墙**里
       放行**入站 TCP 8443** 到部署机那个 IPv6 地址。
  4. **DNS**：把 A 从「节点 IP」改成「家里的公网 v4」，AAAA 保持家里 v6 —— 也就是 `--only both`：
     ```
     python deploy/update_dns.py --update --only both
     ```
     （不加 `--update` 只打印。）
     > ⚠️ 改完 A 就不再是节点 IP → **隧道那条路从域名上消失了**（一个名字只能指一个地方）。
     > 回滚 = 把 A 手改回那个能用的节点 IP（见 `deploy/free-frp-relay.md`）。
     > ⚠️ DDNS 也要跟着换口径：现在跑的是 `--only v6`（只刷 AAAA），切 8443 要换成默认的 `both`：
     > `python -m tonicuisc_server ddns install`（`ddns status` 确认）。
  5. **改客户端默认地址**：`app/lib/core/server_config.dart` 的 `kDefaultServerUrl` 改成
     `https://uisc.tonicaphid.cn:8443`，重新发版（App 里临时改：我的 → 长按版本号 → 服务器地址）。
  6. **从手机走流量验证**（别连家里 Wi-Fi，不然测的是内网）：`https://uisc.tonicaphid.cn:8443/api/health`。

  > ⚠️ **顺序别搞反**：前 1–4 步没做完，**不要**先改客户端地址——隧道节点的 8443 是关的，
  > 改了就是 App 全连不上（实测节点上的非标端口都是拒绝连接的）。

  > ⚠️ **前提是有公网 IPv4**。路由器 WAN 口 IP 若落在 `100.64.x.x`（CGNAT）/ `10.x` /
  > `172.16–31.x` / `192.168.x`，就是没有公网地址 —— 端口映射做了也没用，得先打客服要
  > （说「家里装了监控 / NAS，要远程访问」，**别说建网站**，说了会直接进备案流程被拒）。
  > 注意这跟云服务器上的「未备案域名拦截」不是一回事：那是云厂商自己的防火墙做的域名比对，
  > 家里是 ISP 的 IP，ISP 只封端口、不做域名比对。
  > 另外这套 8443 是**「全走家里」**的：切过去之后 A 不再是节点 IP，**没 v6 的客户端也只能走家里的
  > v4:8443**，隧道就此不在域名上。回滚 = 把 A 改回**那个能用的境外节点**（隧道一直活着，可以用
  > `curl --resolve` 单独测它）。
- **纯 IPv4 或纯 IPv6 的网络**：双栈记录本身覆盖了两族，但如果你只放行了其中一族，另一族就连不上
  （IPv4 需要路由器端口映射，IPv6 需要防火墙放行，两件事都要做）。

### 不想让 Caddy 管证书（内网 / 纯局域网）

服务端也能自己跑 HTTPS，两个环境变量都填上就开：

```bash
python -m tonicuisc_server cert 192.168.5.37     # 生成自签证书（.cache/tls/ 下）
# 把打印出来的两行写进 server/.env：
#   TONICUISC_TLS_CERT=...192.168.5.37.crt
#   TONICUISC_TLS_KEY=...192.168.5.37.key
python -m tonicuisc_server                        # 现在直接是 https://192.168.5.37:8000
```

- 用一个给一个不算数：**只填一个当作没配**，避免「以为开了 HTTPS，其实还是明文」；
- 证书里会带上 `localhost` / `127.0.0.1` / `::1`，本机自测不用另开一张；
- 自签证书手机 / 浏览器会提示不受信任，把 `.crt` 装到设备上并手动信任即可（iOS 还要在「关于本机 → 证书信任设置」里打开）；App 这边在「我的」页**长按版本号**的弹窗里打开「允许自签证书」（默认关，公网域名不需要）；
- 生成优先用 `cryptography`（在 `requirements.txt` 里），没装就退回调 `openssl`。

两种方式**不要同时用**：让 Caddy 终止 TLS 时，服务端跑明文 HTTP（只在 `127.0.0.1` 上）即可，套两层 TLS 只会更慢。

公网暴露后注意：`/login` 是免鉴权的，80 只留给 Caddy 做跳转和证书校验（8000 别对公网开），密码别用弱口令。

## 封面（QQ 音乐）

咪咕 / 酷我给的封面经常是小图或者糊的，所以默认**用 QQ 音乐补一次**：拿「歌名 + 歌手」搜一下 QQ 音乐，从结果的 `albummid` 拼出 500×500 的专辑图 `https://y.gtimg.cn/music/photo_new/T002R500x500M000{albummid}.jpg`。

**搜索接口不等封面**——先按音源原图把列表画出来，同一时间把这批歌丢给后台线程池（默认 8 路）并发查 QQ 封面并写进 Redis；已经查过的封面**直接写进搜索结果**，所以重复搜同一个词第一帧就是 QQ 封面。客户端渲染完再调 `POST /api/covers {"ids":[...]}`（`lib/shared/cover_cache.dart`）：要么命中缓存，要么就等同一批已经在跑的请求，**同一首歌绝不会问两遍 QQ**；服务端还没拿到定论的 id 会缺席，客户端隔两秒再问（最多 3 轮），查到就换上去。所以补封面慢一点也不会拖慢搜索。

封面策略是「**先给能看的，再换成更好的**」：

| `tonicuisc:qcover:<id>` | 返回给客户端的 `cover_url` |
| --- | --- |
| 有地址 | QQ 封面 |
| `''`（查过、QQ 没有） | 音源原图兜底（原图也没有就返回**空串** = 查过了、确实没有） |
| 不存在（还没查过） | **音源原图**（先画出来，后台查到 QQ 封面再换上） |
| 这次没等到定论 | **不返回这个 id**（= 还在查），客户端过几秒再问一次 |

> 查询**失败**（QQ 超时/挂了）和「查过、没有」是两回事：失败**不写缓存**，下次还会重试；
> 只有真的查过并且没有，才会记成 `''`。否则一次网络抖动就让这首歌永远只剩糊图。
> 所以 `/api/covers` 的返回值是「**有这个 key = 结论，没这个 key = 待定**」：
> 把原图当成结论塞回去，客户端就再也不会重问，QQ 封面永远换不上。

所以刚搜出来时列表显示音源原图（加载不出来就退回音源首字），几百毫秒后 QQ 封面到了就换上；列表和全屏播放页**共用同一个缓存**，里外一致。

- 服务端并发查（默认 8 线程），失败或不匹配就走上面的兜底；
- QQ 搜不到时会返回「最接近」的结果，所以做了匹配校验：歌名要相等、或短的那个（≥4 字）被长的包含，**并且歌手要对得上**——否则搜「不存在的歌名xyzabc」会被 `XY&Z` 这种短名字骗到；
- 结果缓存在 `tonicuisc:qcover:<id>`：key 不存在 = 没查过、`''` = 查过没有、其它 = 地址。同一首歌只真的查一次（搜索预热和 `/api/covers` 共用同一批任务），重启也不丢；
- 歌词里 `[by:]`、`[offset:0]`、`[ti:]` 这类 LRC 元信息标签会被丢掉，不会当歌词显示；
- 不想要就设 `TONICUISC_QQ_COVER=0`。

## 歌手主页

列表里**点歌手名**（搜索结果、全屏播放页）进歌手主页：`GET /api/artist?name=<歌手>`。

**先说清楚它能做到什么、做不到什么**：音源只有关键词搜索，**没有「歌手 → 专辑 → 全部作品」这种接口**，所以这里是「搜歌手名，再只留歌手字段里真的包含这个名字的结果」（人名支持 `/`、`、`、`&` 分隔），同一个人名会多抓一些（2 倍且至少 60 条）再过滤。因此：

- ✅ 能拿到这个歌手在咪咕/酷我上**关键词搜索排得比较靠前**的歌，比在搜索框里手打歌手名看到的多；
- ❌ **不等于该歌手的完整曲库**。冷门歌、没上架的歌不会有，可能混进同名/翻唱，也可能因为音源只返回前 N 条而丢掉一部分。

想要真·完整曲库，得接专门的歌手接口（比如 QQ 音乐的 `singer_mid` → 歌曲列表），但那些歌的**播放地址还得回咪咕/酷我搜一遍**，成本和复杂度都上一个台阶，暂时没做。

## 数据（Redis）

所有数据都在 **Redis** 里，服务端启动时自动把本地实例拉起来（找不到就自动下载，见下面的「Redis」一节）。

| key | 内容 |
| --- | --- |
| `tonicuisc:song:<id>` / `tonicuisc:songs` | 搜到的歌曲元信息 + musicdl `SongInfo` 的 JSON（zset 按 last_seen 排序，方便裁剪） |
| `tonicuisc:search:<n>` / `tonicuisc:searchrecent` | 搜索历史与当时的结果列表（默认保留最近 500 条） |
| `tonicuisc:user:<id>` / `tonicuisc:username:<名字>` | 账户（PBKDF2 密码哈希 + 恢复码哈希；用户名索引不区分大小写） |
| `tonicuisc:device:<id>` / `tonicuisc:devicehash:<哈希>` | 设备与 `sha256(api_key)`、绑定账户 |
| `tonicuisc:dreq:<码>` | 设备码登录的中间状态（自带 TTL） |
| `tonicuisc:lib:<用户>:<类型>` / `tonicuisc:libmeta:*` | 喜欢 / 收藏 / 播放历史 |
| `tonicuisc:sc:<sha1>` | 搜索结果缓存（默认 24 小时） |

**直链不当长期有效**：酷我 / 咪咕的 `download_url` 是带签名的临时 token，库里给它记了保守的 30 分钟有效期（`URL_TTL_SECONDS`）。重启服务后：

1. 内存缓存空了 → 从 `tonicuisc:song:*` 恢复歌曲；
2. 直链还在有效期 → 直接播；
3. 直链过期 → 用「歌名 + 歌手」自动重搜一次换新链接；
4. 重搜也失败（歌下架了）→ 返回 404/502，客户端重新搜索即可。

自动裁剪：搜索历史保留最近 500 条、歌曲保留最近 5000 首，**被收藏/喜欢/历史引用的歌不会被裁掉**。

**从旧 SQLite 迁移**：老版本的数据用 `python scripts/import_sqlite.py` 一次性搬过来（账户、设备、收藏、搜索历史都保留，可重复执行）。

## Redis

数据全在 Redis 里，所以服务端**自己管 Redis**，不用你另外装：

- 启动 `python -m tonicuisc_server` 时：先试着连 `TONICUISC_REDIS_URL` / `127.0.0.1:6390`；连不上就按 `TONICUISC_REDIS_SERVER` → `redis/win/`（Linux 是 `redis/linux/`）→ `PATH` 里的 `redis-server` 依次找，**都没有就自动下载**到那个目录；
- 拉起时用生成的 `redis.conf`（只 bind 本地、开 AOF 持久化、日志写 `.cache/redis/redis.log`），数据落在 `.cache/redis/`；
- **Python 退出时把「自己拉起来的」Redis 一起关掉**（先发 `SHUTDOWN` 让它把 AOF 刷盘，刷不掉才 terminate/kill；另有 `atexit` + Job Object 兜底）；如果连的是你自己已经跑着的 Redis（或给了 `TONICUISC_REDIS_URL`），那就只连不管，绝不去关它；
- `python -m tonicuisc_server redis` 可以看当前用的地址/版本/是不是托管实例；`clear-cache` 可以清掉搜索结果缓存。

两个目录的分工：

| 目录 | 谁用 | 里面是什么 |
| --- | --- | --- |
| `redis/win/` | Windows | 自动下载的 `redis-server.exe`（首次启动时出现） |
| `redis/linux/` | Linux | 自动下载/解包的 `redis-server`（首次启动时出现） |

仓库里只放脚本和说明（二进制不进版本库，`.gitignore` 已经排掉），离线部署可以提前手动把 `redis-server` 丢进对应目录，用 `TONICUISC_REDIS_SERVER` 指过去也行。

## 搜索过程

- **只请求选中的音源**：musicdl 的 `MusicClient.search()` 会把配置里所有音源都打一遍，所以服务端直接调用选中音源的 client；没勾的音源不会被请求。
- **拆成并发请求**：`search_size_per_source = 要几条`、`search_size_per_page = ceil(要几条 / TONICUISC_SEARCH_THREADS)`，于是 musicdl 生成对应数量的 URL、用同样多的线程并发抓。**不要**把它压成 1 个请求：musicdl 一个请求内部是按结果**串行**解析的（酷我要逐首请求直链），实测「1 个请求拿 15 条」要 18~23s，拆开就好得多——**5 路（每页 3 条）3.6s、15 路（每页 1 条）2.0s**；上游抽风（单首要 4s）时 5 路会拖到 12.7s，15 路仍只要 ~5s。所以并发数是拿上游负载换稳定时延，默认 `10`（每页 2 条），需要更快就设 `15`。
- **按音源分摊**：想要 15 条、开了 2 个音源 → 每个音源只拿 8 条（`ceil(15/2)`），不再各自都拉 15 条。`TONICUISC_SEARCH_SIZE`（默认 5）是下限。
- **音源之间并行**：多个音源同时发，各自再铺满 `TONICUISC_SEARCH_THREADS` 个请求。
- **搜索不等封面**：结果一出来就带**音源原图**返回，同一时间把这批歌丢给常驻的封面线程池（`TONICUISC_QQ_COVER_THREADS`，默认 8）在后台并发查 QQ 封面并写进 Redis。已经查过的封面会**直接写进搜索结果**，所以重复搜同一个词第一帧就是 QQ 封面。App 随后调 `POST /api/covers` 时，要么已命中缓存、要么就等**同一批正在跑的请求**（按歌曲 id 去重，绝不会问两遍 QQ）——`lib/shared/cover_cache.dart` 不用改。
- **分页**：`GET /api/search?offset=&limit=` 返回一页 + `has_more`。App 滑到底自动再要 15 条，**按 id 去重**，不会出现重复的歌；同一关键词的结果在 Redis 里缓存 24 小时（`TONICUISC_SEARCH_TTL`），翻页和**重启服务**都不需要重新搜。客户端的 `offset` 按「服务端已经返回过多少条」推进，**不受去重影响**——去重只改显示条数，否则会反复要同一段、翻不动页。首页即使缓存里少几条也先给（后台补全），翻页时则必须凑够，不然那一页是空的。
- **不打印进度条**：给 musicdl 传一个 `disable=True` 的 rich `Progress`，它就不再往控制台刷进度条。
- **耗时可见**：服务端每个 `/api` 请求打一行 `[access] GET /api/search 200 5.482s`；`/api/search` 另外在响应里返回 `elapsed`（服务端耗时）。客户端自己再量一次总耗时（含网络往返），显示成「搜索完成 · 用时 5.5 秒（服务端 2.9 秒）· 共 20 首」。

## 缓存

- **搜索缓存**：相同关键词 + 音源直接返回 Redis 里的结果，不再联网（默认 24 小时，`TONICUISC_SEARCH_TTL`）；并发相同请求只会真正搜一次。重启服务也不会重新搜；翻页时缓存不够才重新抓。
- **软刷新（App 不用改）**：App 每次搜索都带 `refresh=true`，真按「强制刷新」办就是每搜一次联网 5 秒。所以缓存比 `TONICUISC_SEARCH_SOFT_TTL`（默认 10 分钟）新就直接返回（几十毫秒），同时在后台重新搜一遍更新缓存；超过这个窗口才阻塞着真去搜。`python -m tonicuisc_server clear-cache` 可以立刻清空。
- **QQ 封面**：`(歌名, 歌手) → albummid` 的 LRU 记 2048 条，同一首歌重复查直接命中（实测 3 首 7.56s → 0.000s）；拿到 albummid 就直接拼 URL，不发第二次请求；网络失败不写缓存，下次还会重试。查过的结果同时写进 `tonicuisc:qcover:*`，重启也不丢。
- **音频缓存**：下载后的文件统一放在 `.cache/files/<音源>_<id>.<ext>`，同一首歌不会重复下载。
- musicdl 每次搜索都会新建 `.cache/music/<音源>/<时间戳> <关键词>/`，服务端下载完会把文件搬走、顺手清掉 `search_results.pkl` 之类的记录文件（搜索前也会清理闲置 10 分钟以上的空壳目录）。

## 播放链路

1. 先请求 `/api/url/{id}` 拿音源直链，直接连 CDN 播放（最快出声，服务端不中转）；请求时带上返回的 headers（部分 CDN 需要 UA / Cookie）。
2. 直链拿不到、或播放器报错，自动回退 `/api/stream/{id}`；**回退请求带着 `X-API-Key`**（`just_audio` 自己发 HTTP，不带的话开着鉴权的服务端会回 401，整首歌放不出来）。
3. `/api/stream/{id}` 未缓存时**边下边播**：从音源拉数据的同时写缓存并吐给播放器（实测首字节 ~0.5s）；中断会丢掉半截文件；缓存完成后转为按 Range 读本地文件。

播放后端：iOS / macOS 用 just_audio 原生实现，Windows / Linux 通过 `just_audio_media_kit`（media_kit）。

手机端锁屏 / 控制中心控制由 `just_audio_background` 提供，iOS 还依赖 [tool/platform_patches/ios/Runner/Info.plist](tool/platform_patches/ios/Runner/Info.plist) 里的 `UIBackgroundModes: audio`（构建时由 `apply_patches` 打上）。

## 客户端界面

- **苹果风（Cupertino）外观，全平台统一**：整个界面用 Cupertino 控件（`lib/app/theme.dart` 里是 iOS 系统蓝 `#007AFF` + 浅色 / 深色两套主题），Windows / Linux 上也一样——页面转场、返回手势、开关 / 滑块 / 分段控件、弹出层都是苹果那套原生动画。`CupertinoApp` 没有 `darkTheme` 参数，深色模式在 `app.dart` 的 `builder` 里按系统亮度自己挑一套 `CupertinoTheme`。**迁移是逐页做的**：还没迁到 Cupertino 的页面暂时被 `lib/app/legacy_material_host.dart` 罩在一层 Material 环境里（否则 `TextField` / `ListTile` / `ScaffoldMessenger` 找不到祖先会直接抛断言）；全部迁完后这个文件和 `app.dart` 里的 `builder` 一起删。
  - **已迁**：外壳（含悬浮胶囊 tab 栏）、底部播放条、**搜索页**。
  - **待迁**：列表 / 我的 / 全屏播放 / 播放队列 / 歌手主页 / 登录页。
  - 迁完的页面顺带消掉一类噪音：Material 的 `IconButton(tooltip: ...)` 在 Windows 上会触发引擎的 AXTree 报错刷屏（见上面「Windows 本机第一次跑」）。
- **iOS 26「液态玻璃」（Liquid Glass）**：`lib/shared/glass.dart` 里的 `GlassSurface` 是这套材质的底座 —— `BackdropFilter` 模糊 + 半透明底色 + 0.5px 高光描边 + 淡投影（少了描边就像一块塑料板）。iOS 26 最有辨识度的**悬浮胶囊 tab 栏**已经落地（见下）。
  > ⚠️ **「内容从玻璃栏下穿过」这一半还没做**：Flutter 的 `ListView` 只在 `padding == null` 时才自动吃 `MediaQuery.padding`，而现有页面都写了显式 `padding`，所以内容暂时还不会滑到玻璃底下去。要做成完全体得按顺序来：**① 先逐页迁移**，让每个页面的滚动容器把 `MediaQuery.paddingOf(context)` 加进自己的 padding；**② 再把外壳改成 `Stack`**，让顶栏 / 侧边栏 / tab 栏**叠在**内容上面（现在它们和内容是 `Column` 里的兄弟节点，根本压不到一起）。顺序反了会「内容被玻璃栏盖住」——那正是当初 `barBackgroundColor` 必须不透明的原因。
- **双栈连接（IPv6 优先）**：域名同时有 `AAAA` / `A` 时，客户端**先连 IPv6**；250ms 还没连上就把 IPv4 也发出去，谁先连上算谁（`lib/core/dual_stack.dart`，RFC 8305 Happy Eyeballs 的最小实现，挂在唯一的 `HttpClient` 工厂上所以全 App 生效）。自己接管这一步是因为 `dart:io` 默认把整个地址列表交给系统排序，而且**一个地址连不通时是干等超时**，不会立刻换下一个——手机从 WiFi 切到只有 v4 的网络时最能感觉到。实测差别很大：v6 直连约 20ms，走 v4 隧道要 3.2 秒（首连的 TLS 握手要绕香港再回家）。
  > ⚠️ **这个实现有个必须记住的坑：装了 `HttpClient.connectionFactory` 之后，dart:io 就不再代劳 TLS 了。** `_http/http_impl.dart` 里是 `if (cf != null) { 直接用它返回的 socket } else { isSecure ? SecureSocket.startConnect(...) : ... }`——它**完全信任**你给的 socket，直接在上面写明文 HTTP。所以 `dual_stack.dart` 必须自己 `SecureSocket.secure(socket, host: url.host)`（`host` 必须是**域名**：SNI 决定节点怎么路由、证书也按域名校验）。漏掉这一步的现象是「明文请求打到 443」：家里 v6 那条回 `400 Client sent an HTTP request to an HTTPS server.`，frp 节点那条直接把连接关掉（`Connection closed before full header was received`）。判定规则抽成了纯函数 `needsOwnTls()` 并有测试盯着；真机验证就跑 `app/tool/probe_net.dart`。**走代理时不握手**——那种情况 dart:io 会自己发 `CONNECT` 再握手，`badCertificateCallback` 也只在那条路上还会被调用。
  >
  > **回落做到了握手层**：`attemptBothFamilies()` 在「TCP 连上了但 TLS 握手被掐」时（中间盒/拦截的典型手法，大陆节点上实测就是这样），会把两个家族**换个顺序整套再来一遍**，而不是就此认输 —— 因为「TCP 通」根本不等于「这条路能用」。
- **两套布局（一套代码）**：窗口宽度 ≥ `840` **且**短边 ≥ `480` 时切**桌面布局**——左侧苹果风宽侧边栏（`lib/app/nav_sidebar.dart`，图标 + 文字，208px，悬停 / 选中都有过渡）+ 宽内容区 + **横跨整个窗口**的底部播放条（`lib/app/layout.dart` 里的 `useDesktopLayout`）；否则是**手机布局**——`CupertinoNavigationBar` 顶栏 + 底部**悬浮胶囊 tab 栏**（`lib/app/glass_tab_bar.dart`，iOS 26 那种：不贴边、圆角胶囊、玻璃材质、选中项背后一块淡底色；底部 home indicator 的安全区由外壳补，胶囊自己不吃，否则会被撑高）。宽屏下内容两侧平分留白、收在 1200 逻辑像素内（只加 `Padding`，不改页面拿到的约束，所以各页面一行都不用动）。窗口在断点两侧拖动时 `IndexedStack` 里那三页**原地复用**，搜索到一半的结果不会丢。
- **登录页**（未登录时的首屏，`lib/features/auth/login_page.dart`）：进页面自动登记设备码，主按钮「打开登录页并复制设备码」弹出系统浏览器并把码放进剪贴板；后台**自适应轮询**（1s→3s），批准后自动完成登录。连不上服务器、码过期、被拒绝、已经领过一次（`claimed`）都会给人话提示；**没登记成功的码不会摆给用户复制**（点了会先重试登记）。
- **搜索 tab**（`lib/features/search/search_page.dart`）：关键词搜索、音源勾选、显示「正在进行…」和用时、试听、下载、点红心加进「我喜欢」；**点歌手名进歌手主页**；**滑到底自动再加载 15 条**（按 id 去重，不会重复）。
- **歌手主页**（`lib/features/search/artist_page.dart`）：这个歌手能搜到的所有歌、「全部播放」、逐首点红心（完整度见上面的「歌手主页」一节）。
- **列表 tab**（`lib/features/library/library_page.dart`）：最上面是**播放列表**（显示正在播放的歌、队列长度、播放模式），下面是 **我喜欢 / 收藏 / 播放历史**，各自显示数量，点进去可播放、单条移除、清空。
- **播放队列**（`lib/features/player/queue_page.dart`）：全屏播放页点列表按钮弹出，或在「列表」tab 里整页打开。
- **播放模式**：顺序播放 / 列表循环 / 单曲循环 / 随机播放，在播放页和队列页都能切；播完自动按模式走下一首（队列只有一首时随机播放也重头再来，顺序播放才停下）。快速连点「下一首」只会让最后一次点歌生效，不会被上一次没加载完的覆盖。
- **我的 tab**（`lib/features/profile/profile_page.dart`）：账户名、本机设备名与设备 ID、重新登录、退出登录、版本号（**长按改服务器地址 + 允许自签证书**，入口平时隐藏）、已登录设备列表（可逐台吊销，删完即从列表消失）。
- **底部播放条**：封面缩略图、播放/暂停、进度拖动；点一下展开全屏播放页。
- **全屏播放页**（`lib/features/player/now_playing_page.dart`）：大封面 + 喜欢/收藏 + 歌词跟随高亮（点歌词跳转）+ 进度条 + 上一首 / 播放暂停 / 下一首。**拖进度条时音频继续播，滑块跟手指走，松手才跳到那个位置**。
- **封面**：所有列表都用搜到的 `cover_url`（`lib/shared/song_avatar.dart`），QQ 补的封面到了会自动换上去；加载失败或没有封面才退回音源首字。
- **屏幕常亮**：App 在前台时不让系统自动息屏（`wakelock_plus`），切到后台自动放开，不会后台耗电。

## CI

- **`server-ci.yml`**：安装依赖 → `compileall` → 模块 import 检查 → `pytest`（232 个用例，不联网、不需要 musicdl）。
- **`build.yml`**：
  1. 只在 **tag（`v*`）和手动触发**时跑全平台；普通分支 push 不触发，改由 `macos.yml` 只构建 macOS；
  2. `静态检查` job 先跑 `flutter analyze` + `flutter test`；
  3. `Windows / Linux / iOS` **`needs: analyze`**，静态检查过了才开始烧构建时间（出错 1 分钟就能拿到反馈，不会几个平台白跑）；
  4. Flutter 版本固定在 `env.FLUTTER_VERSION`（固定版本缓存才会命中，否则每次都要重新保存 1GB+ 缓存）；
  5. 全部成功后由 `发布 Release` job 汇总产物上传到 GitHub Release。
- **`macos.yml`**（macOS 单独构建）：见下。

### 为什么 macOS 单独一个 workflow

1. **架构**：GitHub 的 `macos-latest` 现在是 **Apple Silicon（arm64）**，只出 arm64 的话
   **Intel Mac 跑不起来**。所以支持两个架构：`macos-latest` → `tonicuisc-macos-arm64.dmg`，
   `macos-13` → `tonicuisc-macos-x64.dmg`。
   **Intel 那条默认不跑**（手动触发时勾 `intel` 才跑）：`macos-13` 容量紧张——实测排了一个多小时
   还在队列里，而 `needs: build` 要等**全部**矩阵腿，它会把挂包那步一起卡死。
   勾了 intel 就接受「挂包要等它」这个代价。
2. **成本**：macOS runner 计费是 Linux 的 10 倍，全平台 matrix 跑一次要 40 分钟。改客户端时
   只想验证 macOS，用 `workflow_dispatch` 手动触发这个就够了。
3. 产物是 **DMG**：里面是 `tonicuisc.app` + 指向 `/Applications` 的快捷方式，拖进去就是安装。
   打包前会做一次 **ad-hoc 签名**（保留沙盒/网络 entitlements），再用 `lipo -archs` 校验
   **产物名写的架构是不是真的在里面**。

触发方式与产物：

| 触发 | 行为 |
| --- | --- |
| 推送 `main` / `master` | **只构建 macOS arm64**，只出 artifact，不建 Release |
| `Build` 成功之后（`workflow_run`） | 自动接上：构建 arm64 并**挂到 Release** |
| 手动 `workflow_dispatch` | 只出 artifact；勾 `publish` 才挂 Release，勾 `intel` 才出 x64 |
| 推送 `v*` tag | `build.yml` 全平台构建，macOS 由 `workflow_run` 接上并挂 Release |

> 为什么用 `workflow_run` 而不是 `push: tags`：Release 是 `build.yml` 用 **GITHUB_TOKEN**
> 建的 tag，GitHub 规定「GITHUB_TOKEN 触发的事件不会再触发别的 workflow」，
> 所以 `push: tags` **根本不会响**（实测：一次都没被触发）。`workflow_run` 不受这条限制，
> 而且它在 `Build` **完全跑完**后才启动，天然不会和建 Release 抢。

**ad-hoc 签名 + Gatekeeper**：CI 会做一次 ad-hoc 签名（不需要 Apple 开发者账号），但没有 Apple
公证，用浏览器下载后 macOS 仍会拦（可能提示「已损坏，无法打开」）。
装好后执行一次即可（workflow 的 Summary 里也会打印这段）：

```bash
xattr -dr com.apple.quarantine /Applications/tonicuisc.app
```

或者右键 App → 打开 → 弹窗里再点「打开」。要彻底不弹，得买 Apple Developer 账号做签名 + 公证。

Windows 安装包用 **Inno Setup** 生成：装到 `Program Files\Tonicuisc`，带开始菜单快捷方式、
可选桌面快捷方式和卸载项；没有代码签名证书，首次运行可能弹 SmartScreen，点「更多信息 → 仍要运行」即可。
不想安装也可以下 `tonicuisc-windows-x64-portable.zip` 直接解压运行。

版本号：`tool/apply_version.py` 从 **tag 或提交标题**里取 `v0.0.7` / `v0.1.0` / `v1.0.0`，后面跟 `fix` 也认（`v0.0.7fix`），写两处：

- `app/lib/version.dart` —— 界面显示的版本（带 fix）
- `app/pubspec.yaml` —— 只能是合法 semver（`x.y.z+build`）；**build number = `github.run_number`**，`fix` 只体现在上面那行显示标签里，不会进 pubspec

匹配**必须带 `v` 前缀**：否则提交信息里随口一句「依赖升到 1.2.3」「python 3.11」就会被当成版本号写进产物。本地手写也一样：`python tool/apply_version.py v0.1.5 --build=3`；不带版本号时回退到 `app/pubspec.yaml` 里现有的值。

Release 命名：**标题 = 提交标题**（`run-name` 也是），**标签 = 提交标题里的 `v0.0.x`**。没写版本号时依次回退：推送的 tag → 手动输入 → `app/pubspec.yaml`。重复同一版本会更新标题并覆盖同名产物，不需要手动打 tag。

产物：`tonicuisc-windows-x64-setup.exe`（安装包）、`tonicuisc-windows-x64-portable.zip`、
`tonicuisc-linux-x64.tar.gz`、`tonicuisc-macos-arm64.dmg`、`tonicuisc-macos-x64.dmg`、`tonicuisc-ios-unsigned.ipa`。

> `build.yml` 建 Release 时先挂 Windows / Linux / iOS 三个平台的包，macOS 那两个 DMG 由
> `macos.yml` 在自己跑完后补上去（workflow 之间不能互相 `needs`，所以两边都用了
> 「没有就建、有就改」的幂等写法）。

iOS 产物为未签名 `.ipa`，安装需要自行用 Xcode 重签。

## 本地自检

改完代码推之前先跑这几条：

```bash
# 服务端测试（232 个用例，秒级，不联网）
cd server && python -m pytest -q

# 客户端：静态检查 + 测试（38 个用例：歌词解析、模型、文件名过滤、响应式布局、外壳、双栈连接）
cd app && flutter analyze && flutter test

# 连不上服务器时先跑这个探针：先做「备案体检」（明文 80 看有没有阿里云拦截页），
# 再把四条路各测一遍（原生对照 / App 真实路径 / 强制 v6 / 强制 v4）
# 全通退出码 0，有失败返回 1（可直接塞进脚本）
cd app && dart run tool/probe_net.dart

# 包里每个模块都要能 import（compileall 只查语法，查不出「import 的模块被搬走了」）
cd server && python scripts/check_imports.py

# Dart 括号配平（CI 里的 analyze 只能等远端，这个本地就能查低级错误）
python tool/check_dart_balance.py

# Dart import 是否都指到真实文件（搬目录 / 重构后最容易踩的坑）
python tool/check_dart_imports.py
```

手动冒烟脚本（需要联网 + musicdl）：

```bash
cd server
python scripts/smoke.py 周杰伦        # 只搜一次，看看能不能出结果
python scripts/smoke_api.py 莫问归期   # 搜索 → 边下边播 → Range → 下载
python scripts/smoke_db.py            # 验证重启后还能从 Redis 恢复并播放
python scripts/import_sqlite.py       # 老版本：把 .cache/tonicuisc.db 里的数据搬到 Redis
```

## 备注

- Linux 构建依赖：`clang cmake ninja-build pkg-config libgtk-3-dev liblzma-dev libstdc++-12-dev libmpv-dev libsecret-1-dev libjsoncpp-dev`（CI 已装；`libsecret` 是 `flutter_secure_storage` 要的）。
- Linux 运行时如果没有 secret service（无桌面 keyring），凭据读写会失败——桌面端主要在 Windows / macOS 上没这个问题。
- `flutter analyze` 若因依赖版本报错，先执行 `flutter pub upgrade`。
- 服务端不要同时跑两个实例（会抢同一个 Redis 的 key 命名空间；端口也会冲突，会看到 `error while attempting to bind on address ('0.0.0.0', 8000)`）。要共用同一个 Redis 就让两个实例错开端口 + `TONICUISC_REDIS_PORT`。
