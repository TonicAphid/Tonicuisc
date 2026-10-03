# redis/win/ —— Windows 版 redis-server

这个目录**一开始只有这份 README**。Windows 上第一次 `python -m tonicuisc_server`
（或手动跑 `python scripts/fetch_redis.py`）之后，这里会多出几个可执行文件和 DLL。

> **这些二进制不要提交进仓库。** 仓库的 `.gitignore` 里已经写了 `redis/win/*` +
> `!redis/win/*.md`，只留下说明文档，见 [../README.md](../README.md) 第六节。

## 自动下载会把这个目录填成什么样

来源：[tporadowski/redis](https://github.com/tporadowski/redis) 的 Windows 免安装包
—— **Redis 5.0.14.1 x64**，约 12 MB：

```
https://github.com/tporadowski/redis/releases/download/v5.0.14.1/Redis-x64-5.0.14.1.zip
```

脚本只从 zip 里挑需要的文件解出来，其余（`redis-benchmark.exe` 之类）丢掉：

```
redis/win/
├── redis-server.exe   ← 真正要用的
├── redis-cli.exe      ← 顺手带上，排查问题很方便
├── *.dll              ← zip 里带的运行库（Redis 5 这版一般只有 EventLog.dll）
└── README.md          ← 这份说明，永远保留
```

实测（2026-02）：下载 12.0 MB，解出 `redis-server.exe` + `redis-cli.exe` + `EventLog.dll`，
`redis-server --version` → `Redis server v=5.0.14.1 sha=00000000:0 malloc=jemalloc-5.1.0 bits=64`。

## 手动放二进制的办法（下载失败 / 内网机器）

1. 在有网的机器上打开上面那个 zip 地址（或者去
   <https://github.com/tporadowski/redis/releases> 找最新版）；
2. 解压，把 `redis-server.exe` 拷到本目录（`redis-cli.exe` 想要也一起拷）；
3. 确认这个文件真的在：

   ```powershell
   redis\win\redis-server.exe --version
   ```

   服务端就认 `redis/win/redis-server.exe` 这个位置和名字，不用改配置。

也可以不放在这里，改成告诉服务端去哪找（任选其一）：

```powershell
# 指定可执行文件的完整路径
$env:TONICUISC_REDIS_SERVER = "C:\tools\redis\redis-server.exe"

# 或者干脆连一个已经跑着的 Redis，服务端就不管进程了
$env:TONICUISC_REDIS_URL = "redis://127.0.0.1:6379/0"

# 或者用系统 PATH 里已经装好的
# （服务端找不到本目录的 exe 时会自动去 PATH 找 redis-server）
```

## 怎么确认服务端真的用上了它

```powershell
cd server
python -m tonicuisc_server.redis_runtime --stop
```

会打印 `server_path`（就是本目录的 exe）、`started_by_us`（True），
然后 `PING -> True` 并把这个托管实例关掉。

## 注意

- 这版 Redis 是 **5.0.14.1**，比较老：配置文件里别写 7.x 才有的指令；
  客户端要按 **RESP2** 连（redis-py 8 默认会发 `HELLO 3`，Redis 5 不认）。
  `redis_runtime.py` 已经处理好了，自己手写客户端的话记得加 `protocol=2`。
- 服务端用 `dir "<路径>"` 这种带引号的正斜杠写法指定数据目录，数据落在
  `server/.cache/redis/`，不在本目录。
- 关服务时先给 Redis 发 `SHUTDOWN`（它会借机把 AOF 刷盘，尽量不丢最近一秒的写入），
  它自己退了就完事；赖着不走才 `terminate()`，10 秒还不退再 `taskkill /PID <pid> /T /F`；
  你手工起的 Redis 服务端**不会**去动它（只关自己拉起来的那个）。
