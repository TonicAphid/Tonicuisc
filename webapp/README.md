# webapp —— 落地页 + 网页播放器

**现在由 Python 自己托管**，`server.js` 是可选的旧入口。

```
浏览器 ──HTTPS──► Caddy :443 ──► Python :8000（tonicuisc_server）
                                  ├─ /            落地页        ← webapp/public/index.html
                                  ├─ /music       网页播放器    ← webapp/public/music/
                                  ├─ /music/api/session*  网页会话（HttpOnly Cookie）
                                  ├─ /download/*  查最新 Release 后 302
                                  └─ /api /login /health  接口与登录页
```

一条命令全跑起来：

```bash
cd server
python -m tonicuisc_server
```

打开 `http://127.0.0.1:8000/` 就是落地页，`/music` 是播放器。

## 为什么不需要 Node 了

以前是「Node 托管静态页 + 反代 `/api` 给 Python」，为的是把 API Key 留在服务端、
浏览器只拿 Cookie。现在这段逻辑搬进了 Python（`tonicuisc_server/api/routes/site.py`
和 `web/sessions.py`），会话和接口在同一个进程里，**连反代都省了**：

- 浏览器只拿 `tuisc_session`（HttpOnly、SameSite=Lax，HTTPS 时带 Secure）；
- `api/middleware.py` 里的 `web_session` 在鉴权闸门之前把 Cookie 换成 `X-API-Key`，
  key 依旧不出服务端；
- 音频流是 `GET /api/stream/{id}` 同源请求：已缓存时 Python 直接给 `206 Partial Content`；
  **还没缓存时也能拖**——浏览器发来的 `Range` 会透给音源，从中途开始拉（这段不写缓存）；
- 歌词高亮是**逐词**填充的（每个词一个 span），长句折行不会让第二行从头重新填充；

## 目录

```
webapp/
  public/
    index.html           落地页
    style.css
    app.js
    music/
      index.html         网页播放器
      music.css
      music.js
  server.js              旧的 Node 入口（可选，默认不用）
```

改页面直接改 `public/` 下的文件，刷新浏览器就能看到（HTML / CSS / JS 都是
`no-cache`，会回源校验，不会出现「新 HTML + 旧 JS」）。图片等静态资源缓存 5 分钟。

## 环境变量（都在 `server/.env`）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TONICUISC_WEB_BUILTIN` | `1` | 网页由 Python 托管。设 `0` 才回到 Node 拓扑 |
| `TONICUISC_WEB_PUBLIC` | `webapp/public` | 静态目录，单独打包部署时指过去 |
| `TONICUISC_WEB_SESSION_TTL` | `604800` | 网页会话有效期（秒，默认 7 天） |
| `TONICUISC_WEB_REMEMBER` | `1` | 「记住这台浏览器」：重启后按指纹自动恢复登录、设备列表不重复 |
| `TONICUISC_WEB_REMEMBER_TTL` | `2592000` | 上面那条绑定的有效期（秒，默认 30 天），每次恢复自动续期 |
| `TONICUISC_WEB_PORT` | 空 | **只在 `TONICUISC_WEB_BUILTIN=0` 时有意义**：Node 的监听端口 |

## 网页登录（设备码）

1. 打开 `/music`，服务端生成设备码（`A1B1-C1D1`）并登记；
2. 页面上的「打开登录页批准」跳到 `/login?code=...`，**直接显示登录 / 注册表单**
   （`?code=` 由 Python 自己解析，不用再提交一次表单）；
3. 批准后前端轮询 `/music/api/session` 拿到 `ready`，会话里存着设备 key；
4. 浏览器只拿到 `tuisc_session` Cookie。

不想走设备码时，登录页底部可以**手动粘贴 API Key**（在 App 的「我的」页能找到）。

## 两枚 Cookie：会话 + 浏览器指纹

| Cookie | 有效期 | 作用 |
| --- | --- | --- |
| `tuisc_session` | 7 天（`TONICUISC_WEB_SESSION_TTL`） | 短会话，只活在进程内存里，**重启就没了** |
| `tuisc_web_id` | 1 年 | 浏览器指纹，相当于「网页播放器的 MAC 地址」 |

`tuisc_web_id` 解决两个老问题：

- **服务端重启不再掉线**：Redis 里有一条「指纹 → 设备」的绑定（`storage/web_logins.py`），
  重启后内存会话空了，前端第一次轮询就能按指纹**自动恢复**，不用重新配对。
  恢复时是按设备记录**重新签发**一把 key、旧 key 同时作废——存的是设备指针，不是 key 本身。
- **设备列表不再越登越多**：指纹会作为 `device_id` 带进设备码流程，批准时复用同一条记录。
  同一台机器的同一个浏览器，永远只占「网页播放器」一行。

主动点「退出登录」会删掉那条绑定（指纹保留），下次登录还是同一台设备；
把设备从列表里删掉 / 吊销，绑定也立刻失效——**删掉就等于下次必须重新配对**。

> 为什么不用真的 MAC 地址：浏览器拿不到网卡地址（隐私限制，WebRTC 拿到的还是 mDNS
> 混淆过的假地址）。所以用服务端发的随机 ID + 长期 Cookie 模拟一个稳定硬件标识，
> 效果等价：同一台机器认得出、清掉 Cookie 就变成新设备。

想关掉这套机制（每次都重新配对）就设 `TONICUISC_WEB_REMEMBER=0`。

## 旧拓扑：仍然用 Node（可选）

```bash
# server/.env
TONICUISC_WEB_BUILTIN=0
TONICUISC_WEB_PORT=8080
```

```bash
cd server && python -m tonicuisc_server   # 先起 Python
node webapp/server.js                     # 再起 Node（127.0.0.1:8080）
```

这时 Caddy 反代到 **8080**，Node 再把 `/api`、`/login`、`/health` 转给 8000。
`server.js` 的行为和以前完全一致（会话在 Node 内存里、Range 透传、下载 302）。
两条路只选一条，不要同时开着——同一套页面被两个入口各管一份，会话会互相打架。

## 排错

| 现象 | 先看这里 |
| --- | --- |
| 页面 404「页面不存在」 | 静态目录不对：确认仓库里有 `webapp/public/`，或用 `TONICUISC_WEB_PUBLIC` 指过去 |
| 接口 401「请先在 App 里配对设备」 | 网页没配对成功：打开 `/music` 重新走一次设备码；设备被删 / 吊销过也会这样 |
| 服务端重启后还是要重新配对 | 浏览器把 `tuisc_web_id` 清掉了（无痕窗口 / 清了 Cookie），或者设了 `TONICUISC_WEB_REMEMBER=0` |
| 设备列表里有多台「网页播放器」 | 升级前的老记录（那时没有指纹），删掉多余的即可；新登录只会复用一条 |
| 登录后立刻回到设备码页 | Cookie 没存住：HTTPS 下必须走域名（Secure Cookie），别用 IP + 自签证书混着访问 |
| 设备码一直「等待批准」 | `/login` 打开的是不是同一台服务？批准后前端最多 2 秒就能拿到 key |
| 音频不能拖 | 第一次播放要先等缓存；缓存完再拖就是 206 |
| 改了 JS 没生效 | 强制刷新一次；确认改的是 `webapp/public/` |
