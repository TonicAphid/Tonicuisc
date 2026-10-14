# v0.1.7 —— 结构分层 / 登录重做 / 提速 / 原生 HTTPS

这一版没有加新功能，做的是「把已有的东西摆正、把登录做扎实、把能省的来回省掉」。
对外不兼容的地方只有一处：**`/api/device/status` 从 GET 改成了 POST**（见下）。

## 一、目录分层

以前 `tonicuisc_server/` 下 16 个模块平铺，`auth.py` 一个类管 5 件事，`main.py` 620 行里
混着路由、中间件、流式响应，`storage.py` 844 行一个 `Storage` 类。

```
core/        config / constants / errors / logging / text / tls / runtime
storage/     base + songs / history / users / devices / device_requests / library / search_cache + redis_runtime
auth/        tokens / codes / accounts / device_flow / ratelimit + AuthManager 门面
services/    sources / searching / downloads / artwork / qqmusic + MusicService 门面
api/         app / deps / middleware / limiters / ranges + routes/*（health、account、library、media、search、login）
web/         pages（怎么画）+ login（点了之后干什么）
cli/         commands（管理命令）+ console（`>` 控制台）+ launch（uvicorn + TLS）
```

- `Storage` 拆成按领域的一组 mixin，`storage/__init__.py` 组合成原来的 `Storage`：老 import 不用改；
- `AuthManager` 保留原方法签名，内部交给 `AccountService` / `DeviceFlow`；
- `/login` 的 95 行 `if/elif` 链变成**派发表**（`HANDLERS`），每个 action 一个小函数，
  「设备码为空 / 查不到」和限速三件套（查锁定 / 记失败 / 清失败）各自只写了一遍。

客户端 `app/lib` 同样按功能分层：

```
app/         app.dart（根 widget）+ main_shell.dart（tab 外壳 + 登录闸门）
core/        api_client / api_errors / server_config / credentials / self_signed
features/    auth / search / library / player / profile
shared/      cover_cache / song_avatar
models/      song / lyric
```

## 二、登录

- **`poll_secret` 从 query 挪进 POST 请求体**：query 会原样写进 uvicorn / Caddy 的访问日志，
  而这个秘密的全部意义就是「只有这台 App 知道」；
- **状态常量化**：服务端 `auth.constants.DeviceStatus`，客户端 `DeviceLoginState`，
  两边共用同一批字面量，`claimed`（已批准但 key 已被取走过）以前会让界面干等十分钟；
- **轮询自适应**：1 秒 → 2 秒 → 3 秒（原来固定 2 秒），批准后立刻能进；
- **错误可见**：轮询阶段原来 `catch (_) {}` 全吞，服务端挂了就永远显示「等待批准…」；
  现在连续失败几次会把原因说出来，并且继续重试；
- **不再摆假码**：登记失败后界面显示的是没登记过的码——现在失败会提示并保持未登记状态，
  点复制 / 打开登录页会先重试登记；
- **设备名单一来源**：登录页填的名字会一路存进本地凭据（以前本地存的是默认名），
  按回车改名字也不会再把设备码作废；
- **注册也过防爆破锁**：否则「用户名已被占用」就是现成的用户名枚举接口，而登录那边
  费力用假哈希抹平响应耗时就白做了；
- 客户端凭据 `clear()` 保留 `device_id`，重新配对时服务端就地复用旧记录（一台设备一条）。

## 三、速度

- **`last_seen` 节流写**：每个带 key 的请求都会写一次 Redis，翻一屏列表就是几十次写；
  现在 60 秒内只写一次（`TOUCH_INTERVAL_SECONDS`）；
- **`/api/library/summary` 一次往返**：原来 3 次调用、5 个 Redis 命令，现在一个 pipeline 取齐；
- **线程池调大**：`asyncio.to_thread` 默认只有 `min(32, cpu+4)`，而搜索、边下边播的读、
  每一次 Redis 调用都挤在里面；现在 64（`TONICUISC_THREADS`），退出时自己收掉；
- 客户端启动时四个本地存储读取改成并发（`Future.wait`），少三个来回。

## 四、HTTPS

- **服务端原生 TLS**：`TONICUISC_TLS_CERT` / `TONICUISC_TLS_KEY` 两个都填就由 uvicorn 直接跑
  HTTPS（只填一个当没配），启动横幅自动显示 `https://`；
- **`python -m tonicuisc_server cert <域名或IP>`**：生成自签证书（优先 `cryptography`，
  没装退回 `openssl`），证书里带上 `localhost` / `127.0.0.1` / `::1`，并把要写进 `.env`
  的两行直接打出来；
- **客户端「允许自签证书」开关**：经 `HttpOverrides` 全局生效，默认关，入口在「我的」页
  长按版本号的弹窗里（公网域名不需要开）；
- Caddy 反代那条路照旧（公网推荐），README 里两条路的取舍写清楚了。

## 五、其他

- `tests/` 从 156 个用例增加到 **166 个**，新增 `test_tls.py`（证书装配、自签可加载）、
  `test_optimizations.py`（节流写、pipeline 汇总、POST 轮询、访问日志不泄密、注册限速）；
- 新增 `pytest.ini`（只在 `tests/` 里收集）、`scripts/check_imports.py`
  （walk 一遍所有模块，专抓「import 的模块被搬走了」这种 compileall 查不出的错，
  已接进 `server-ci.yml`）和 `tool/check_dart_imports.py`（本地没有 flutter 时也能查出
  指不到文件的 import）；
- 删掉死代码：`new_poll_secret`、`AuthManager._lock`、`PUBLIC_PATHS` 里两条永远匹配不到
  的路径（`/login`、`/health` 不在 `/api` 下，根本不经过鉴权中间件），
  以及重构中遗留的 `items.py`（它的内容早已拆进 `core/` 与 `services/song_item.py`，
  自己却还 import 着搬走的 `config`）与 `app/lib` 下 5 个空目录。

## 六、部署形态：家里部署（外部 v4 + v6 双栈 → 内网 IPv4）

拓扑定下来了（家里没有服务器，托管机放在清凉那台）：

```
手机 / 浏览器 ──公网 v4 或 v6 : 443──► Caddy（部署机）──IPv4 127.0.0.1:8000──► tonicuisc_server
```

两个域名、都是**双栈**（A + AAAA）：

| 域名 | 用途 | 绑哪台机器 |
| --- | --- | --- |
| `uisc.tonicaphid.cn` | 正式，App 全部访问走它 | 部署机 |
| `tuisc.tonicaphid.cn` | 测试，本机调试用 | 这台测试机 |

- `deploy/Caddyfile.template` 按这个拓扑写（文件头是拓扑图 + 四步准备），
  实际跑的 `deploy/Caddyfile` **按当前环境生成**（正式/测试域名不同，Caddy 必须拿自己的域名去签证书）；
  反代到 `127.0.0.1:8000`（IPv4 回环），日志改相对路径（Windows 上没有 `/var/log/caddy`）；
- 新增 `deploy/open-firewall.ps1`：管理员放行入站 TCP 80 + 443，只动两条具名规则，可 `-Remove` 撤销；
- 新增 `deploy/update_dns.py`：**A 和 AAAA 一起维护**（`--only v4|v6` 可只刷一族，
  `--record` 换域名，`--update` 才真写）。用阿里云 DNS API 的 HMAC-SHA1 签名，不需要装 SDK；
  AK 从 `deploy/.aliyun.env`（已 gitignore，见 `.example`）或环境变量读，建议用 RAM 子账号。
  **它以「本机网卡的稳定 IPv6」为准**，而不是探测服务的返回值——Windows 隐私扩展下系统默认用
  **临时地址**出网（实测：探测回 `...e95a:...`，网卡上还有 `...fe45:...` 稳定地址），
  两者**前缀相同**属于正常；只有**前缀都不同**才是走了 VPN / 代理，脚本会警告。
  IPv4 探测源 HTTP 优先（实测这台机器上部分 HTTPS 出网会被拦）；
- `.env.example` 默认改成 `TONICUISC_HOST=127.0.0.1`（只监听 IPv4 回环）；
- README 的 HTTPS 一节重写成上述流程（双栈、两个域名、在正确的机器上跑脚本），
  并说明「不想让 Caddy 管证书」时仍可用原生 TLS。

实测（本机）：服务端绑 `127.0.0.1:8123` 时只监听 IPv4 回环，
`http://127.0.0.1:8123/api/health` 通，`http://[本机全局IPv6]:8123` 与 `http://[::1]:8123` 都拒绝——
外网碰不到服务端本体，只能从 Caddy 进。

> 顺带修掉一个部署脚本的坑：带中文的 `.ps1` 在 Windows PowerShell 5.1 下会被按 GBK 解码，
> 引号被破坏后直接解析失败。`deploy/` 下的 `.ps1` 存成 **UTF-8 with BOM + CRLF**；
> Python 脚本则显式把 stdout 重配成 UTF-8（默认是 GBK），读 `.aliyun.env` 用 `utf-8-sig`
> （记事本存的 UTF-8 带 BOM，不处理的话第一行 key 会读不到）。

## 七、环境选择 / Caddy 自动下载 / DDNS 定时任务

这三样是为了「家里两台机器（正式 + 测试）用同一套代码」：

- **`.env` 以前根本没被加载**（实测：写进去的变量 `os.getenv` 读出来是 `None`，
  `start.ps1` 只是检查文件在不在）。现在 `core/environment.py` 在 `config` 之前把
  `server/.env` 灌进 `os.environ`，**真环境变量优先**（临时覆盖、systemd 里配的都还管用）；
- **环境选择**：首次启动问一次「这是哪套环境」（`uisc` 正式 / `tuisc` 测试），
  选完写进 `server/.env`，以后不再问；非交互（systemd / 计划任务 / 管道）自动跳过。
  环境决定**域名 + 启动横幅 + DDNS 目标**，端口/Redis 不动。
  随时可改：`python -m tonicuisc_server env tuisc`。
  踩过的坑：早先写成「PUBLIC_URL 没设过才写」，于是从 tuisc 切回 uisc 时旧域名残留，
  横幅和登录链接指向错的域名——现在切环境一定同步改；
- **Caddy 自动下载**（`core/caddy.py`）：照 Redis 那套写，Windows 下 zip、Linux 下 tar.gz
  （标准库直接解）。查找顺序 `TONICUISC_CADDY_SERVER` → `deploy/<win|linux>/` → PATH → 下载。
  **走 gh-proxy 等多源自动回退**（`gh-proxy.com` → `ghfast.top` → `ghproxy.net` → 直连兜底），
  实测 14.5 MB 第一个源就下完；`TONICUISC_GH_PROXY` 可换镜像或设空串强制直连；
- **Caddyfile 按环境生成**：`--generate` / `--validate` / `--start`。
  踩过的坑：反代目标一度生成成 `0.0.0.0:8000`——那是**监听**地址，拿去连是错的，
  现在映射成 `127.0.0.1`；
- **DDNS 定时任务**（`core/ddns.py`）：`ddns install --every 10m` / `status` / `remove` / `run`，
  自动识别系统——Windows 用计划任务，Linux 优先 systemd user timer（不需要 root）、
  没有 systemd 退回 cron。地址变化是低频事件，交给系统调度器比常驻线程省心。

## 八、CI：macOS 单独构建（`macos.yml`）

原来 macOS 混在 `build.yml` 的 desktop matrix 里，有两个问题：

1. **架构**：GitHub 的 `macos-latest` 现在是 **Apple Silicon（arm64）**，只出 arm64 的话
   **Intel Mac 跑不起来**。新 workflow 两个架构都出：`macos-latest` → `tonicuisc-macos-arm64.zip`、
   `macos-13` → `tonicuisc-macos-x64.zip`（Intel 那个标了 `continue-on-error`，
   因为 `macos-13` GitHub 随时可能下线，不该拖垮整个流程）；
2. **成本**：macOS runner 计费是 Linux 的 10 倍，全平台跑一次 40 分钟；
   只想验证 macOS 时用 `workflow_dispatch` 手动触发就够。

其它改进：

- 打包从 `zip` 换成 **`ditto`**：`.app` 里有符号链接，`ditto` 才能保住链接和签名；
- 构建后 **`lipo -archs` 校验产物名写的架构是不是真的在里面**（不然产物名就是骗人的）；
- 加了**沙盒权限检查**：少了 `network.client` 的 macOS 版连不上后端（沙盒直接掐断出网），
  现在缺了就当场失败，而不是等用户装了才发现；
- 加了几秒钟的**本地 Dart 自检**（括号配平 + import 解析），挡住「`flutter build` 跑十几分钟才炸」
  的低级错误；
- Summary 里直接打印 **Gatekeeper 绕过命令**（未签名 + 浏览器下载会被拦成「已损坏」）：
  `xattr -dr com.apple.quarantine /Applications/tonicuisc.app`；
- `build.yml` 摘掉了 macOS（避免同一平台构建两遍），改成只管 Windows / Linux / iOS。

> Release 归属：`build.yml` 建 Release 并挂 Windows / Linux / iOS；macOS 那两个包由 `macos.yml`
> 在自己跑完后补上去（workflow 之间不能互相 `needs`，所以两边都是「没有就建、有就改」的幂等写法）。

## 兼容性

- **`GET /api/device/status` → `POST /api/device/status`**（`user_code` + `poll_secret` 走 JSON body）。
  旧客户端登录会失败，需要一起升级；
- 其余接口、Redis key 结构、环境变量都不变。
