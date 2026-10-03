# redis/ —— 托管 Redis 的二进制存放目录

Tonicuisc 服务端把 Redis 当存储用（搜索结果缓存、设备/账户、收藏列表……）。
为了「下载下来就能跑」，服务端在**第一次启动**时会自己找一个 `redis-server`；
找不到就自动下载到本目录，用的时候再拉起来，**Python 退出时一起关掉**。

这个仓库里**只放脚本和文档，不放二进制**。下面两个子目录一开始只有 `README.md`，
首次启动（或手动跑一次预下载脚本）之后才会多出可执行文件——那些文件**不要提交**。

```
redis/
├── README.md      ← 你正在看的
├── win/           ← Windows 用：redis-server.exe 等
│   └── README.md
└── linux/         ← Linux 用：redis-server
    └── README.md
```

## 一、正常情况下：什么都不用做

```powershell
cd server
python -m tonicuisc_server        # 第一次启动会看到 [redis] 正在下载 ... 然后自动拉起
```

服务端会按这个顺序找 `redis-server`：

1. 环境变量 `TONICUISC_REDIS_SERVER` 指定的完整路径；
2. `redis/win/redis-server.exe`（Windows）或 `redis/linux/redis-server`（Linux）；
3. 系统 `PATH` 里的 `redis-server`；
4. 都没有 → 自动下载（`TONICUISC_REDIS_DOWNLOAD=0` 可以关掉，关掉就直接报错）。

如果 Redis 不在本机、或者你不希望服务端管进程，直接给一个完整地址就行，这时
**只连接，不启动、不关闭**：

```powershell
$env:TONICUISC_REDIS_URL = "redis://:密码@10.0.0.5:6379/0"
```

## 二、手动预下载（推荐在打包 / 部署前跑一次）

不想让「第一次启动」卡在下载上，就提前跑脚本：

```powershell
cd server
python scripts/fetch_redis.py             # 给当前平台下好，放进 redis/win 或 redis/linux
python scripts/fetch_redis.py --force     # 已经有了也重新下一遍
python scripts/fetch_redis.py --check     # 只检查下面那些下载地址现在还通不通
python scripts/fetch_redis.py --platform win   # 在 Linux/macOS 上给 Windows 备一份（zip 解压是纯 Python）
```

脚本是纯手动工具，不会启动 Redis，也不会碰 `.cache/redis` 里的数据。

## 三、离线准备（内网机器 / 不能联网的部署环境）

在**能联网**的机器上：

```powershell
cd server
python scripts/fetch_redis.py --platform win
python scripts/fetch_redis.py --platform linux
```

然后把整个 `redis/win/` 或 `redis/linux/` 目录拷到离线机器上对应的位置。
Linux 上记得保留可执行权限：

```bash
chmod +x redis/linux/redis-server
```

也可以完全不用自动下载，自己装一份然后告诉服务端去哪找：

```bash
# Linux 最省事
sudo apt install redis-server
export TONICUISC_REDIS_SERVER=/usr/bin/redis-server

# 或者直接连一个已经跑着的 Redis（服务端就不管进程了）
export TONICUISC_REDIS_URL=redis://127.0.0.1:6379/0
```

## 四、下载地址（代码里就是这么写的，`--check` 会去验）

| 平台 | 内容 | 地址 |
| --- | --- | --- |
| Windows | Redis 5.0.14.1 x64 免安装 zip（约 12 MB） | `https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip` |
| Linux | 官方 deb，Redis 7.2.5（Ubuntu noble，amd64） | `https://packages.redis.io/deb/pool/noble/r/re/redis-server_7.2.5-1rl1~noble1_amd64.deb` |
| Linux | 兜底：官方源码包，Redis 7.2.5 | `https://download.redis.io/releases/redis-7.2.5.tar.gz` |

下载地址变了的话，改 `server/tonicuisc_server/redis_runtime.py` 顶部那几个
`*_URL` 常量，顺便把这几个 README 里对应的行也改掉。

> Windows 版是 **Redis 5.0.14.1**（tporadowski 的移植版，Windows 上最省心的一个）。
> 它比较老，所以配置文件只用 5.0 就有的指令；客户端也固定按 RESP2 连
> （redis-py 8 默认发 `HELLO 3`，Redis 5 不认，会报 `unknown command 'HELLO'`）。

## 五、数据放哪

二进制在 `redis/<平台>/`，**数据不在这里**，而是在：

```
server/.cache/redis/
├── redis.conf        # 自动生成的配置，手改会被覆盖
├── appendonly.aof    # AOF 数据（默认开，重启不丢）
├── redis.log         # Redis 自己的日志，起不来先看它
└── redis.pid         # 托管实例的 pid（只作线索，退出时删掉）
```

想换个位置就设 `TONICUISC_CACHE_DIR`。

**退出时是「先请它自己走」**：服务端停止时先给 Redis 发 `SHUTDOWN`（`stop()` 里
先 `_shutdown_gracefully()` 再 `_kill_process()`），Redis 会借这个机会把 AOF 刷盘，
`appendfsync everysec` 下最多 1 秒的写入不会丢——不然「点完喜欢就关服务」再启动会少一条。
刷不掉（卡住 / 无响应）才退化成 terminate → kill，Windows 上再兜一层 `taskkill /T /F`。

## 六、.gitignore（已经加好了，这里备个案）

仓库根的 `.gitignore` 里已经有这一段，保证本目录只提交 README：

```gitignore
# ---- Redis：二进制首次启动自动下载，不进仓库（两个目录只保留说明文档）----
redis/win/*
redis/linux/*
!redis/win/*.md
!redis/linux/*.md
```

（`!` 开头的两条是「反忽略」，让子目录里的 `.md` 还能被提交。）
如果你以后往这两个目录里放别的说明文件，注意别被上面的规则连坐。

## 七、相关环境变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `TONICUISC_REDIS_URL` | 空 | 完整地址；非空就只连外部的这一个，不启动也不关闭 |
| `TONICUISC_REDIS_HOST` | `127.0.0.1` | 没给 URL 时连哪台 |
| `TONICUISC_REDIS_PORT` | `6390` | 端口（也是托管实例的监听端口） |
| `TONICUISC_REDIS_DB` | `0` | 库号 |
| `TONICUISC_REDIS_SERVER` | 空 | 指定 `redis-server` 可执行文件，优先级最高 |
| `TONICUISC_REDIS_DIR` | `<仓库根>/redis` | 本目录的位置 |
| `TONICUISC_REDIS_AUTOSTART` | `1` | 是否允许自动拉起（`0` = 连不上就报错） |
| `TONICUISC_REDIS_DOWNLOAD` | `1` | 是否允许自动下载（`0` = 找不到就报错） |
| `TONICUISC_REDIS_APPENDONLY` | `1` | 托管实例开不开 AOF 持久化 |
| `TONICUISC_CACHE_DIR` | `server/.cache` | 数据目录的上一级 |

## 八、起不来怎么查

1. 看 `server/.cache/redis/redis.log`，里面通常写得很清楚；
2. 看端口是不是被别的东西占了：`netstat -ano | findstr 6390`（Windows）/
   `ss -lntp | grep 6390`（Linux）；
3. 手动跑一下探活：

   ```powershell
   cd server
   python -m tonicuisc_server.redis_runtime --stop    # 拉起 → PING → 顺手关掉
   ```

4. 还不行就绕开托管，自己起一个 Redis，然后 `TONICUISC_REDIS_URL` 指过去。
