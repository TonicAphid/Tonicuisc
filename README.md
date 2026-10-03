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
- 安全：密码 PBKDF2-HMAC-SHA256、限速防爆破、按设备吊销

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
    config.py                      环境变量、音源别名
    service.py                     musicdl 封装：搜索 / 下载 / 缓存 / 边下边播
    storage.py                     Redis：歌曲、搜索历史、搜索缓存、账户、设备、收藏列表
    redis_runtime.py               Redis 的定位 / 自动下载 / 拉起 / 退出回收
    auth.py                        账户、设备码登录、API Key 校验
    ratelimit.py                   滑动窗口限速、密码错误锁定
    weblogin.py                    /login 网页（无模板引擎、无 multipart 依赖）
    main.py                        FastAPI 路由 + 鉴权中间件 + 访问日志
    __main__.py                    python -m tonicuisc_server（含管理子命令）
  scripts/                         smoke.py / smoke_api.py / smoke_db.py / import_sqlite.py
  tests/                           pytest（不联网、不需要 musicdl、不需要真的 Redis）
  start.ps1 / start.sh             一键启动
  requirements.txt / requirements-dev.txt
  .env.example                     环境变量样例

redis/                             Redis 二进制（首次启动自动下载，不进版本库）
  win/                             Windows：redis-server.exe
  linux/                            Linux：redis-server

app/                               Flutter 客户端
  lib/
    main.dart                      入口（桌面端初始化 media_kit、移动端初始化后台播放）
    version.dart                   构建时由 tool/apply_version.py 写入
    api/api_client.dart            HTTP 客户端、错误翻译、默认服务器地址
    api/credentials.dart           设备凭据（系统安全存储 + 桌面端兜底）
    models/song.dart               歌曲模型
    models/lyric.dart              LRC 解析
    player/player_controller.dart  队列、播放模式、上/下一首
    state/library_state.dart       喜欢/收藏/历史的本地缓存（乐观更新）
    pages/main_shell.dart          三个 tab 的外壳
    pages/login_page.dart          设备码登录页
    pages/search_page.dart         搜索 tab
    pages/library_page.dart        列表 tab（播放列表 + 三个歌单）
    pages/queue_page.dart          播放队列（整页 / 底部弹层）
    pages/now_playing_page.dart    全屏播放页（封面 / 歌词 / 进度）
    pages/profile_page.dart        我的 tab（账户 / 设备 / 版本）
    widgets/player_bar.dart        底部播放条
    widgets/song_avatar.dart       封面（失败退回音源首字）
    widgets/play_mode_icons.dart   播放模式图标
  test/                            纯 Dart 单测（歌词解析、模型、文件名过滤）

tool/                              构建与自检脚本
  bootstrap_platforms.ps1/.sh      生成各平台原生工程 + 打补丁 + pub get
  apply_patches.ps1/.sh            覆盖 macOS 权限、iOS Info.plist
  apply_version.py                 从 tag / 提交标题写版本号
  check_dart_balance.py            本地自检 Dart 括号配平
  platform_patches/                原生配置补丁源文件

deploy/Caddyfile                   nip.io + Let's Encrypt 反代样例
.github/workflows/                 server-ci.yml（后端）/ build.yml（客户端）
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
  登录页: http://192.168.5.37:8000/login
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

**默认服务器地址是 `http://192.168.5.37:8000`**，定义在 `app/lib/api/api_client.dart` 的 `kDefaultServerUrl`——要换服务器改这一行即可。用户在「我的 → 服务器地址」改过之后会存在本机，不会再回到默认值。

### 第一次登录

1. App 打开就是登录页，自动生成一个设备码（`A1B1-C1D1`）
2. 点「打开登录页并复制设备码」→ 弹出系统浏览器并已复制设备码
3. 在网页里粘贴设备码 → 注册新账户或登录已有账户 → 批准
4. App 每 2 秒轮询，批准后自动进入，密钥写进系统安全存储

## 后端配置

见 `server/.env.example`：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TONICUISC_HOST` | `0.0.0.0` | 监听地址 |
| `TONICUISC_PORT` | `8000` | 端口 |
| `TONICUISC_SOURCES` | `migu,kuwo` | 启用音源 |
| `TONICUISC_SEARCH_SIZE` | `5` | 每个音源的抓取下限（实际按「要几条 ÷ 音源数」分摊） |
| `TONICUISC_SEARCH_SIZE_PER_PAGE` | `1` | 每个请求取几条的**下限**；实际每页 = `max(该值, ceil(要几条 ÷ 并发数))` |
| `TONICUISC_SEARCH_THREADS` | `5` | 每个音源的并发请求数（同时也是「要 15 条 → 每页 3 条 → 5 个请求」里的分母） |
| `TONICUISC_SEARCH_PAGE_SIZE` | `15` | 客户端一页多少条（滑到底再要下一页） |
| `TONICUISC_SEARCH_MAX` | `60` | 一次搜索最多抓多少条（分页上限） |
| `TONICUISC_AUTH` | `1` | 设备 API Key 校验（`0` 关闭） |
| `TONICUISC_DEVICE_CODE_TTL` | `600` | 设备码有效期（秒） |
| `TONICUISC_TRUST_PROXY` | `1` | 限速是否按反代写的 `X-Forwarded-For` 取来源 IP |
| `TONICUISC_QQ_COVER` | `1` | 用 QQ 音乐补封面（咪咕/酷我的封面经常糊） |
| `TONICUISC_QQ_COVER_THREADS` | `8` | 补封面时的并发数（QQ 单次要 3~4 秒，8 路比 4 路快一倍） |
| `TONICUISC_QQ_COVER_WAIT` | `25` | `/api/covers` 最多等正在跑的封面查询多久（秒） |
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
| `TONICUISC_PUBLIC_URL` | 空 | 对外地址，启动横幅打印登录链接用 |
| `TONICUISC_CACHE_DIR` | `server/.cache` | 下载缓存目录（Redis 数据在 `<CACHE_DIR>/redis`） |
| `TONICUISC_DB` | `<CACHE_DIR>/tonicuisc.db` | 旧 SQLite 库位置，只给 `scripts/import_sqlite.py` 迁移用 |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务状态（**免鉴权**） |
| POST | `/api/device/start` | App 登记设备码，发起登录（**免鉴权**） |
| GET | `/api/device/status` | App 轮询登录状态，批准后返回一次 `api_key`（**免鉴权**） |
| GET / POST | `/login` | 设备码登录网页（**免鉴权**） |
| GET | `/api/me` | 当前账户 + 本设备信息 |
| GET | `/api/devices` | 已登录设备列表 |
| DELETE | `/api/devices/{device_id}` | 吊销设备（**直接删除记录**，它的 key 立刻失效） |
| GET | `/api/library/summary` | 三个列表的数量 + 喜欢/收藏的 id |
| GET | `/api/library/{kind}` | 列表内容（`kind` = `like` / `favorite` / `history`） |
| POST | `/api/library/{kind}` | 加入列表（喜欢/收藏幂等，历史累加播放次数） |
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
 │ GET /api/device/status?code&poll_secret（每 2s）───────► approved + api_key
 │ 存进系统安全存储          ◄──────────────────────────── 立刻清掉暂存的明文
```

要点：

- **设备码由 App 生成**（`A1B1-C1D1`，字母表去掉了容易看错的 `I/O/0/1`），10 分钟有效、用过即废；
- **轮询密钥 `poll_secret` 只有 App 知道**，别人猜到设备码也拿不到 key；
- 账户密码用 **PBKDF2-HMAC-SHA256（20 万次迭代 + 随机盐）**存储；
- 设备 API Key 256 bit 随机，`devices` 表里**只有 `sha256(key)`**；批准到领取之间的明文只暂存在 `device_requests` 行里，App 取走立刻清空；
- 登录页**不列出服务器上有哪些账户**，用户名靠手填，避免泄露账户名单；
- App 收到 401 自动清凭据并回到登录页。

本机管理（不需要 key）：

```bash
python -m tonicuisc_server devices            # 设备 + 账户列表
python -m tonicuisc_server revoke <device_id> # 吊销某台设备（= 删记录）
python -m tonicuisc_server cleanup            # 清理无账户 / 已吊销的设备记录
python -m tonicuisc_server redis              # 看一眼 Redis（地址 / 版本 / 是不是托管实例）
python -m tonicuisc_server clear-cache        # 清掉搜索结果缓存（下次搜索重新联网）
```

### 限速（防爆破）

| 限制 | 默认 | 作用 |
| --- | --- | --- |
| `/login` 提交 | 30 次/分钟/来源 IP | 防刷页面、防脚本 |
| `/api/device/start` | 30 次/分钟/来源 IP | 防刷设备码 |
| 密码错误 | 5 次/5 分钟/(IP + 用户名) | **防爆破**，锁定期内即使密码正确也拒绝 |

超限返回 429 并带 `Retry-After`，网页上显示「密码错误次数过多，请在 N 秒后再试」。

来源 IP 默认取反向代理写的 `X-Forwarded-For`（`TONICUISC_TRUST_PROXY=1`）。**如果把端口直接暴露到公网、前面没有反代，要设成 `0`**，否则可以伪造这个头绕过限速。

关闭鉴权：`TONICUISC_AUTH=0`（任何人可调，仅限完全可信环境）。

**这套东西的边界**（很重要）：

- 它解决「谁能调接口」+「按设备吊销」。**HTTP 明文下，链路上抓包的人可以直接拿走 key**——要真安全必须上 HTTPS。
- 设备被物理接触（越狱 / root / 调试器）就能读出 key，这是所有客户端凭据的共性。

## HTTPS（nip.io）

`deploy/Caddyfile` 里已经按公网 IP 写好：

```bash
caddy run --config deploy/Caddyfile     # https://106-35-196-104.nip.io
```

规则是把 IP 里的点换成横杠（`106.35.196.104` → `106-35-196-104.nip.io`），Caddy 自动申请并续期 Let's Encrypt 证书。**前提是 80/443 能从公网回连**——nip.io 只是把域名解析到那个 IP，Let's Encrypt 校验时是从公网反连你的机器，所以纯内网 IP（`192.168.x.x`）签不下来。

内网自签就用 mkcert：`mkcert 192.168.5.37`，把根证书装到手机上（iOS 要在「关于本机 → 证书信任设置」里手动打开），Caddyfile 里注释掉的部分有示例。

公网暴露后注意：`/login` 是免鉴权的，建议只开放 443、给 Caddy 加上访问限速，密码别用弱口令。

## 封面（QQ 音乐）

咪咕 / 酷我给的封面经常是小图或者糊的，所以默认**用 QQ 音乐补一次**：拿「歌名 + 歌手」搜一下 QQ 音乐，从结果的 `albummid` 拼出 500×500 的专辑图 `https://y.gtimg.cn/music/photo_new/T002R500x500M000{albummid}.jpg`。

**搜索接口不等封面**——先按音源原图把列表画出来，同一时间把这批歌丢给后台线程池（默认 8 路）并发查 QQ 封面并写进 Redis；已经查过的封面**直接写进搜索结果**，所以重复搜同一个词第一帧就是 QQ 封面。客户端渲染完再调 `POST /api/covers {"ids":[...]}`（`lib/state/cover_cache.dart`，**不用改**）：要么命中缓存，要么就等同一批已经在跑的请求，**同一首歌绝不会问两遍 QQ**。所以补封面慢一点也不会拖慢搜索。

封面策略是「**宁可先空着，也别先糊一张**」：

| `tonicuisc:qcover:<id>` | 返回给客户端的 `cover_url` |
| --- | --- |
| 有地址 | QQ 封面 |
| `''`（查过、QQ 没有） | 音源原图兜底 |
| 不存在（还没查过） | **空**（先不显示，等后台查完 / `/api/covers` 补） |

所以刚搜出来时列表可能短暂没有封面（显示音源首字），几百毫秒后 QQ 封面到了就换上；列表和全屏播放页**共用同一个缓存**，里外一致。

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
| `tonicuisc:user:<id>` / `tonicuisc:username:<名字>` | 账户（PBKDF2 密码哈希；用户名索引不区分大小写） |
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
- **拆成并发请求**：`search_size_per_source = 要几条`、`search_size_per_page = ceil(要几条 / TONICUISC_SEARCH_THREADS)`，于是 musicdl 生成 `search_threads` 个 URL、用同样多的线程并发抓。**不要**把它压成 1 个请求：musicdl 一个请求内部是按结果**串行**解析的（酷我要逐首请求直链），实测「1 个请求拿 15 条」要 18~23s，拆成 5 个请求各 3 条只要 ~5s。
- **按音源分摊**：想要 15 条、开了 2 个音源 → 每个音源只拿 8 条（`ceil(15/2)`），不再各自都拉 15 条。`TONICUISC_SEARCH_SIZE`（默认 5）是下限。
- **音源之间并行**：多个音源同时发，各自再铺满 `TONICUISC_SEARCH_THREADS` 个请求。
- **搜索不等封面**：结果一出来就带**音源原图**返回，同一时间把这批歌丢给常驻的封面线程池（`TONICUISC_QQ_COVER_THREADS`，默认 8）在后台并发查 QQ 封面并写进 Redis。已经查过的封面会**直接写进搜索结果**，所以重复搜同一个词第一帧就是 QQ 封面。App 随后调 `POST /api/covers` 时，要么已命中缓存、要么就等**同一批正在跑的请求**（按歌曲 id 去重，绝不会问两遍 QQ）——`lib/state/cover_cache.dart` 不用改。
- **分页**：`GET /api/search?offset=&limit=` 返回一页 + `has_more`。App 滑到底自动再要 15 条，**按 id 去重**，不会出现重复的歌；同一关键词的结果在 Redis 里缓存 24 小时（`TONICUISC_SEARCH_TTL`），翻页和**重启服务**都不需要重新搜。首页即使缓存里少几条也先给（后台补全），翻页时则必须凑够，不然那一页是空的。
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
2. 直链拿不到、或播放器报错，自动回退 `/api/stream/{id}`。
3. `/api/stream/{id}` 未缓存时**边下边播**：从音源拉数据的同时写缓存并吐给播放器（实测首字节 ~0.5s）；中断会丢掉半截文件；缓存完成后转为按 Range 读本地文件。

播放后端：iOS / macOS 用 just_audio 原生实现，Windows / Linux 通过 `just_audio_media_kit`（media_kit）。

手机端锁屏 / 控制中心控制由 `just_audio_background` 提供，iOS 还依赖 [tool/platform_patches/ios/Runner/Info.plist](tool/platform_patches/ios/Runner/Info.plist) 里的 `UIBackgroundModes: audio`（构建时由 `apply_patches` 打上）。

## 客户端界面

- **登录页**（未登录时的首屏，`lib/pages/login_page.dart`）：进页面自动登记设备码，主按钮「打开登录页并复制设备码」弹出系统浏览器并把码放进剪贴板；后台每 2 秒轮询，批准后自动完成登录。连不上服务器时给人话提示（不是原始异常）。
- **搜索 tab**（`lib/pages/search_page.dart`）：关键词搜索、音源勾选、显示「正在进行…」和用时、试听、下载、点红心加进「我喜欢」；**点歌手名进歌手主页**；**滑到底自动再加载 15 条**（按 id 去重，不会重复）。
- **歌手主页**（`lib/pages/artist_page.dart`）：这个歌手能搜到的所有歌、「全部播放」、逐首点红心（完整度见上面的「歌手主页」一节）。
- **列表 tab**（`lib/pages/library_page.dart`）：最上面是**播放列表**（显示正在播放的歌、队列长度、播放模式），下面是 **我喜欢 / 收藏 / 播放历史**，各自显示数量，点进去可播放、单条移除、清空。
- **播放队列**（`lib/pages/queue_page.dart`）：全屏播放页点列表按钮弹出，或在「列表」tab 里整页打开。
- **播放模式**：顺序播放 / 列表循环 / 单曲循环 / 随机播放，在播放页和队列页都能切；播完自动按模式走下一首。
- **我的 tab**（`lib/pages/profile_page.dart`）：账户名、本机设备名与设备 ID、服务器地址、重新登录、退出登录、版本号、已登录设备列表（可逐台吊销，删完即从列表消失）。
- **底部播放条**：封面缩略图、播放/暂停、进度拖动；点一下展开全屏播放页。
- **全屏播放页**（`lib/pages/now_playing_page.dart`）：大封面 + 喜欢/收藏 + 歌词跟随高亮（点歌词跳转）+ 进度条 + 上一首 / 播放暂停 / 下一首。**拖进度条时音频继续播，滑块跟手指走，松手才跳到那个位置**。
- **封面**：所有列表都用搜到的 `cover_url`（`lib/widgets/song_avatar.dart`），QQ 补的封面到了会自动换上去；加载失败或没有封面才退回音源首字。
- **屏幕常亮**：App 在前台时不让系统自动息屏（`wakelock_plus`），切到后台自动放开，不会后台耗电。

## CI

- **`server-ci.yml`**：安装依赖 → `compileall` → `pytest`（62 个用例，不联网、不需要 musicdl）。
- **`build.yml`**：
  1. `静态检查` job 先跑 `flutter analyze` + `flutter test`；
  2. `Windows / Linux / macOS / iOS` **`needs: analyze`**，静态检查过了才开始烧构建时间（出错 1 分钟就能拿到反馈，不会 4 个平台白跑）；
  3. Flutter 版本固定在 `env.FLUTTER_VERSION`（固定版本缓存才会命中，否则每次都要重新保存 1GB+ 缓存）；
  4. 全部成功后由 `发布 Release` job 汇总产物上传到 GitHub Release。

版本号：`tool/apply_version.py` 从 **tag 或提交标题**里取 `v0.0.7` / `v0.1.0` / `v1.0.0`，后面跟 `fix` 也认（`v0.0.7fix`），写两处：

- `app/lib/version.dart` —— 界面显示的版本（带 fix）
- `app/pubspec.yaml` —— 只能是合法 semver，fix 体现在 build number 上

Release 命名：**标题 = 提交标题**（`run-name` 也是），**标签 = 提交标题里的 `v0.0.x`**。没写版本号时依次回退：推送的 tag → 手动输入 → `app/pubspec.yaml`。重复同一版本会更新标题并覆盖同名产物，不需要手动打 tag。

产物：`tonicuisc-windows-x64.zip`、`tonicuisc-linux-x64.tar.gz`、`tonicuisc-macos.zip`、`tonicuisc-ios-unsigned.ipa`。

iOS 产物为未签名 `.ipa`，安装需要自行用 Xcode 重签。

## 本地自检

改完代码推之前先跑这两条：

```bash
# 服务端测试（62 个用例，秒级，不联网）
cd server && python -m pytest -q

# Dart 括号配平（CI 里的 analyze 只能等远端，这个本地就能查低级错误）
python tool/check_dart_balance.py
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
