# v0.2.1 —— 结构分层 / 登录重做 / 提速 / 原生 HTTPS

> 这份文档原来叫 `CHANGES-v0.1.7.md`，但实际随 **v0.2.0 / v0.2.0fix / v0.2.1** 一起发出去的，
> 名字对不上版本，已改名。内容从 v0.1.6 之后的所有改动都在这儿。

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

## 九、v0.2.0 之后的修复（实际启动时才暴露的）

几处只有真跑起来才看得见的问题：

- **默认监听改回环**：`TONICUISC_HOST` 的默认值从 `0.0.0.0` 改成 **`127.0.0.1`**。
  以前本机没写这个变量时 uvicorn 直接绑所有网卡，**外网能绕过 Caddy 直连服务端**
  （TLS 和鉴权链一起跳过）。家里部署就该由 Caddy 反代到回环，这也和 `.env.example`
  里写的值一致了（原先两处默认值不一致，代码那份是错的）。
  启动时会打印 `[env] 监听 <host>:<port>`；绑到所有网卡时额外警告一句。
- **刚选的环境没生效**：`Settings` 的默认值是**类定义时**读环境变量的，所以启动时
  选完环境再往 `os.environ` 写值不会自动生效——横幅和登录链接会退回
  `http://127.0.0.1:8000/login`。现在选完显式重建（`config.apply_runtime_env()`）。
- **`EOF` 不再替用户做决定**：后台 / 重定向跑时 `input()` 立刻 EOF，早先被当成
  「回车用默认」，于是**默默写了 `.env`**。现在 EOF 和 Ctrl+C 都只按默认跑这一次、
  不落盘，并提示用 `env` 命令指定。
- **通配地址不当主机名**：横幅以前会打 `http://0.0.0.0:8000/login` 这种点不开的链接。
  抽出 `config.display_host()`（`0.0.0.0`/`::`/`*` → `127.0.0.1`），横幅和 Caddy
  的反代目标共用同一套映射，不再各写一份。
- **新增 `python -m tonicuisc_server env --ask`**：随时把「首次启动那个询问」再跑一遍
  （`env` 不带参数是看当前，`env tuisc` 是直接切）。

> 另外清掉一处测试污染：调试时写进 `server/.env` 的 `TONICUISC_ENV` 会让
> 「首次启动询问」被跳过，看起来像「它不问」。

## 十一、填阿里云 AK 变简单了（`ddns set-key` / `ddns check`）

以前要在 `deploy/.aliyun.env` 里手写两行，Secret 还可能被贴到聊天记录或命令行历史里。
现在：

- **`python -m tonicuisc_server ddns set-key`** —— 交互输入，Secret 走 `getpass`
  （**不回显、不进历史记录**），写完立刻用只读接口验证一次；
- **`python -m tonicuisc_server ddns check`** —— 只读，列出该域名现有 A / AAAA 记录，
  用来验证「AK 对不对 + 权限给没给」。阿里云的原话会透出来：
  `InvalidAccessKeyId` = 抄错了；`Forbidden` = 没挂 `AliyunDNSFullAccess`；
- `update_dns.py` 加了 `--check`（只读路径，绝不调 Add/Update）；
- **密钥不再被抄进定时任务**：Windows 计划任务的 `.cmd`、systemd unit、crontab 里
  以前会带一份 AK，现在都不放了（脚本自己读 `deploy/.aliyun.env`）——
  少一处泄露面，而且**换 AK 不用重装任务**；
- `ddns install` 现在**要求 AK 落在文件里**（定时任务看不到你 shell 里的环境变量），
  只设了环境变量会明确提示；
- 顺带修掉一个会误导排查的小问题：`--check` 的输出曾把「文件里的 AK」报成
  「来自环境变量」（因为调用时把 AK 塞进了子进程环境）——现在不塞了，来源是真的。

实测（假 AK）：整条链路跑通——写入文件 → 读取 → 签名 → 调阿里云 →
回 `InvalidAccessKeyId.NotFound`，并提示两类常见原因。

## 十、CI 实测后的两处调整（v0.2.0fix 那次跑完才看清）

`v0.2.0fix` 那次 CI 的实测结果：`Build` 成功、`Server CI` 成功，
**macOS workflow 被 `workflow_run` 正确触发并且 arm64 构建成功**（artifact 27.5 MB，
产物名和里面的架构一致）。但暴露两个问题：

- **`push: tags` 永远不会响**：Release 是 `build.yml` 用 GITHUB_TOKEN 建的 tag，
  而 GitHub 规定「GITHUB_TOKEN 触发的事件不会再触发别的 workflow」。实测该 workflow
  一次都没被触发过 → 改用 `workflow_run`（不受这条限制，且在 Build 完全跑完后才启动，
  不会和建 Release 抢）。
- **Intel 那条腿卡住 publish**：`macos-13` 排了一个多小时还在队列里，而 `needs: build`
  要等**全部**矩阵腿，于是「挂 macOS 包」这步一直不执行——
  Release `v0.2.0fix` 里只有 Windows / Linux / iOS 三个包。
  改成 **Intel 默认不跑**（手动触发时勾 `intel`）：既不再挡住挂包，也省掉一次排不上的等待。
  arm64 仍是必跑腿。

## 十二、Caddy 证书「看起来不行」的真因 + 随服务自动启动

**症状**：浏览器 `ERR_SSL_PROTOCOL_ERROR`，看着像证书签不出来。**其实证书压根没申请**：

```
ERROR unable to create folder for config autosave
      {"dir": "C:\\Users\\Dell\\AppData\\Roaming\\Caddy", "error": "Access is denied."}
ERROR tls  job failed
      {"error": "failed storage check: mkdir ...Caddy: Access is denied. - storage is probably misconfigured"}
```

Caddy 默认把证书 / ACME 账号放在 `%AppData%\Caddy`（Linux 是 `~/.local/share/caddy`）。
受限环境下那个目录建不了，Caddy **直接放弃申请证书** → 443 上握不了手。

修法不是去改系统 ACL（还会被拦），而是**让 Caddy 的状态落在项目里**：

- 生成的 Caddyfile 里显式写 `storage file_system <项目>/deploy/.caddy/data`；
- 启动 Caddy 时把子进程的 `APPDATA` / `XDG_CONFIG_HOME` / `XDG_DATA_HOME` 一起指过去
  （autosave.json 也留在项目里），目录先 `mkdir` 好；
- 顺带的好处：备份 / 搬机器 / 删干净都只动一个目录，证书文件一眼能找到；
- `deploy/.caddy/` 已 gitignore。

**另外，服务端启动时会顺手把 Caddy 也拉起来**（`TONICUISC_CADDY_AUTOSTART`，**默认开**）：

```
[caddy] 已生成配置：...（域名 tuisc.tonicaphid.cn → 127.0.0.1:8000）
[caddy] Caddy 已启动（pid 12345）：tuisc.tonicaphid.cn → 127.0.0.1:8000
[caddy] 证书在 ...\deploy\.caddy\data；首签要等 Let's Encrypt 校验
```

- 起不来**不影响服务端**（端口被占 / 入站没放行都能边跑边修），只打印原因；
- 服务端退出时把 Caddy 一起收摊（不留孤儿占着 443）；
- **管理端点是全机唯一的**：已经有一个 Caddy 在跑时（比如你手动开的）不再起第二个，
  而是明确提示——实测踩过，两个 Caddy 撞 `127.0.0.1:2019` 会报
  `bind: Only one usage of each socket address`，看起来像端口/权限问题；
- 想关掉（比如只在局域网直连、不挂 HTTPS）：`TONICUISC_CADDY_AUTOSTART=0`。

## 十三、证书改用 DNS-01 签（**不需要入站 80/443**）+ 干掉刷屏日志

**你看到的**：满屏 `challenge failed` + 几十行 `github.com/mholt/acmez/...` 调用栈，
以及 `Timeout during connect (likely firewall problem)`。

**两个问题一起解决**：

1. **签不下来**：Caddy 自带的 ACME 只会 HTTP-01 / TLS-ALPN-01，两种都要求
   Let's Encrypt 能从公网连到 80 / 443。大陆家用宽带常封这两个端口，任何一环不通就签不下来。
   改成 **DNS-01**：只要往解析里写一条 TXT 记录即可，**完全不需要入站端口**，
   也不需要路由器做映射。证书仍然是 **Let's Encrypt 签发**的，只是「怎么证明域名是你的」换了方式。
   实测（本机真实签发成功）：

   ```
   dns01: trying to solve the challenge. domain=tuisc.tonicaphid.cn
   The server validated our request.
   Server responded with a certificate.
   ```

2. **日志刷屏**：Caddyfile 里加 `format filter { wrap console; stacktrace delete }`，
   把 Go 的 `stacktrace` 字段删掉——真正有用的那行（比如 `Timeout during connect`）就露出来了。

实现方式：

- 新增 `core/acme.py`：用 **lego**（ACME 客户端，不是 CA）走 `--dns alidns` 签证书。
  **为什么不用 Caddy 自己**：官方发行版**不含任何 DNS 模块**（`caddy list-modules` 里
  `dns.providers` 为 0），要做就得自己编译。lego 的 `alidns` provider 用
  `ALICLOUD_ACCESS_KEY` / `ALICLOUD_SECRET_KEY`（名字和我们脚本的 `ALIYUN_*` 不同，已做映射），
  直接复用 `deploy/.aliyun.env` 里那把 AK；
- 新增 `core/download.py`：把「GitHub 镜像回退」抽出来给 Caddy 和 lego 共用（原来只有 Caddy 有）；
- 证书落在 `deploy/.caddy/certs/`，签好后 Caddyfile 里写 `tls <crt> <key>`，
  Caddy 就**不再自己跑 ACME**（日志里会明确说
  `skipping automatic certificate management because ... certificates are already loaded`）；
- 新增命令 `python -m tonicuisc_server acme [--status|--issue|--force|--staging]`；
- 服务端启动时自动「需要就续签 → 生成配置 → 起 Caddy」（`TONICUISC_ACME_DNS01`，默认开）。

**踩过并修掉的坑**（都是实测才暴露的）：

- lego v5 的 `.crt` **本身已含完整链**（实测 4 条 = 叶子 + 3 张中间证书），
  而 `.issuer.crt` 是其中 3 张的重复。早先无脑拼接 → 7 条、**重复 3 条**，每次握手白传几 KB；
- 先写 `.crt` 再写 `.key`，中途出错会留下 **0 字节证书**，Caddy 照样去加载它然后握手失败。
  现在先校验内容再落盘，并且 `needs_renewal` 把「空文件」也算成需要重签；
- 选项解析只看 `args[0]`：`acme --issue --force` 里的 `--force` 在 `args[1]`，会被漏掉（已改成全扫）；
- `Caddyfile.template` 的**注释里**写了 `{TLS}` 占位符原文，纯文本替换把注释行拆坏，
  校验直接报 `Did you mean to define a second site?`（注释里改成不带花括号的写法）。

## 十四、Caddy 启动日志不再刷屏

**问题**：`python -m tonicuisc_server` 一启动就吐十几行 Caddy 日志，把服务端横幅冲掉了。

**关键点**：最前面那两行

```
maxprocs: Leaving GOMAXPROCS=8: CPU quota undefined
GOMEMLIMIT is updated  {"GOMEMLIMIT": 15114114662, ...}
```

是 **Go 运行时直接写 stderr** 的，**不经过 Caddy 的日志系统**——
所以只调 Caddyfile 里的 `log { level }` **掐不掉**，必须**重定向子进程的输出**。

做法：

- `start_caddy` 默认把 Caddy 的 stdout/stderr 重定向到 `deploy/.caddy/runtime.log`
  （追加模式，上次的日志留着），控制台只剩我们自己的 `[caddy]` 行：

  ```
  [env] tuisc —— 测试（这台机器） / https://tuisc.tonicaphid.cn
  [env] 监听 127.0.0.1:8000
  [caddy] 启动 Caddy：caddy.exe run --config Caddyfile
  [caddy] 日志在 ...\deploy\.caddy\runtime.log（实时刷屏：TONICUISC_CADDY_LOG=console）
  [caddy] Caddy 已启动（pid 13860）：tuisc.tonicaphid.cn → 127.0.0.1:8000
  ```

- **起不来时自动把日志尾部附在错误里**（不然用户只看到「退出了」无从下手）；
- 想看实时刷屏：`TONICUISC_CADDY_LOG=console`；
- 新增 `python -m tonicuisc_server caddy --log` 看日志尾部；
- `caddy --start`（前台）默认显示日志（人就盯着这个窗口），加 `--quiet` 只写文件。

> 注意运行时日志叫 `runtime.log`，和 Caddyfile 里 `output file caddy.log` 那个
> **访问日志**（每来一个请求一行）不是一回事，故意分开命名。

## 十五、控制台提示符不再被日志「吃掉」

**症状**（用户原话：「这里 `>` 有的会直接放到 `> []` 这样了」）：

```
> [timing] 音源搜索: '缘分一道桥 王维维' 每源 5 条 / 每页 1 条 = 5 个请求并行（1 个音源并行）
> [access] GET /api/url/KuwoMusicClient:36878203 200 2.975s
```

**原因**：`PromptingStdout` 的补画机制只在输出**之后**把 `> ` 补回行尾，
但**输出之前**没有处理已经画在行尾的那个 `> `——异步日志（uvicorn 线程 / 音源线程）
一写就直接接在提示符后面，看着像提示符被吃掉了。

**修法**：写日志前先把提示符「收掉」——

- 终端支持 ANSI（Windows 10+ 的 conhost / Windows Terminal）→
  用 `\r\033[K` 擦掉整行，提示符原地消失、日志顶上来，最干净；
- 不支持 → **先换行**保底（上一行留个孤零零的 `> `，但日志至少各自成行）。
  绝不做的事：把 `\033[K` 原样打出来（那比不擦还难看）。

> Windows 上 `ENABLE_VIRTUAL_TERMINAL_PROCESSING` **默认不一定开**，所以
> `enable_ansi()` 会先探测、开不了就退回换行。探测失败（不是终端 / 拿不到句柄）
> 一律当「不支持」处理——宁可保守。

实测渲染（假 tty，转义序列可视化后）：

```
[timing] 音源搜索: '缘分一道桥 王维维' 每源 5 条 ...
[access] GET /api/url/KuwoMusicClient:36878203 200 2.975s
[timing]   KuwoMusicClient: 5 条 / 2.97s
>
```

日志独占一行，末尾只有一个 `>`。
