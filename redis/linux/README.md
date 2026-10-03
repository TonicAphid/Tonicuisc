# redis/linux/ —— Linux 版 redis-server

这个目录**一开始只有这份 README**。Linux 上第一次 `python -m tonicuisc_server`
（或手动跑 `python scripts/fetch_redis.py`）之后，这里会多出一个 `redis-server` 可执行文件。

> **这个二进制不要提交进仓库。** 仓库的 `.gitignore` 里已经写了 `redis/linux/*` +
> `!redis/linux/*.md`，只留下说明文档，见 [../README.md](../README.md) 第六节。

## 自动下载会把这个目录填成什么样

```
redis/linux/
├── redis-server   ← 可执行文件（脚本会 chmod 755）
└── README.md      ← 这份说明，永远保留
```

脚本按两条路走，前面那条不行才走后面那条：

### 路线一（首选）：官方 deb 里抠二进制

下载 Ubuntu noble 的官方包 —— **Redis 7.2.5**，约 84 KB：

```
https://packages.redis.io/deb/pool/noble/r/re/redis-server_7.2.5-1rl1~noble1_amd64.deb
```

然后 `ar x redis-server.deb` → `tar xf data.tar.*` → 把里面的
`usr/bin/redis-server` 拷成 `redis/linux/redis-server`。这条路需要系统里有 `ar`
（`binutils`，绝大多数发行版自带）和 `tar`。

> 注意地址里是 `pool/<发行版代号>/r/re/`。网上常见的
> `pool/main/r/redis/redis-server_<版本>_amd64.deb` 现在会返回 **403**，别照抄。

### 路线二（兜底）：下载源码现场编译

只有 deb 那条路失败（没有 `ar`、下载不通、包结构变了）才走这儿：

```
https://download.redis.io/releases/redis-7.2.5.tar.gz
```

`tar xzf` → `make -j<核数>` → 把 `src/redis-server` 拷成 `redis/linux/redis-server`。
**需要系统里有 `gcc` 和 `make`**，编译可能要几分钟；两个都没有就直接报错，不会白等。

## 手动准备（apt / 自己编译 / 从别的机器拷）

三条路任选：

```bash
# 1) 最省事：系统装一个，让服务端直接用 PATH 里的
sudo apt install redis-server
#    服务端找不到 redis/linux/redis-server 时会自动去 PATH 找

# 2) 从别的机器拷过来（两边 glibc 版本要兼容）
scp build-host:/tmp/redis-server ./redis/linux/redis-server
chmod +x redis/linux/redis-server

# 3) 自己下 deb 或源码解出来，把 redis-server 放进本目录
```

参数名和位置都不用改：服务端认的就是 `redis/linux/redis-server`。
也可以直接指定路径，或者干脆连外部的 Redis：

```bash
export TONICUISC_REDIS_SERVER=/usr/bin/redis-server
export TONICUISC_REDIS_URL=redis://127.0.0.1:6379/0   # 服务端就不管进程了
```

## 离线准备（内网机器）

在能联网的机器上先跑：

```bash
cd server
python scripts/fetch_redis.py --platform linux
```

把整个 `redis/linux/` 目录拷到离线机器上，**别忘了可执行权限**：

```bash
chmod +x redis/linux/redis-server
redis/linux/redis-server --version
```

（在 Windows 上也能执行 `--platform linux`，但 Windows 没有 `ar`/`make`，
所以那条命令只在你确实想准备 Linux 包、并且当前系统有这些工具时才有用；
真要给 Linux 备货，建议直接在 Linux 机器上跑。）

## 怎么确认服务端真的用上了它

```bash
cd server
python -m tonicuisc_server.redis_runtime --stop
```

会打印 `server_path`（就是本目录的 `redis-server`）、`started_by_us`（True），
然后 `PING -> True` 并把这个托管实例关掉。

## 注意

- 服务端用 `dir "<路径>"` 指定数据目录，数据落在 `server/.cache/redis/`，不在本目录。
- 退出时先发 `SHUTDOWN` 请 Redis 自己退（它会借机把 AOF 刷盘，尽量不丢最近一秒的写入），
  不退再 `terminate()`、10 秒还不退才 `kill()`；如果是 `SIGKILL` 被杀，
  `atexit` 来不及跑，靠的是启动时写下的 pid 文件 —— 下次启动发现端口已被占用，
  会**直接复用**那个 Redis 而不是再拉一个。
- 你手工起的 / 系统服务里的 Redis，服务端**不会**去动它：只关自己拉起来的那个。
