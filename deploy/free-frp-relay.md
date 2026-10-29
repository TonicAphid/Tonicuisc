# 免费 frp 中转（ChmlFrp / OpenFrp）—— 只加一条 CNAME，不动 NS

> 解决的事：**在外面用蜂窝（尤其移动）连不上家里的服务端**。
>
> `deploy/vps-relay.md`（甲骨文，要 Visa）和 `deploy/cloudflare-tunnel.md`（CF，要换 NS）的免费替代。
> 免费 frp 服务只需要你在阿里云**加一条 CNAME**，**完全不用动 NS** ——
> `tonicaphid.cn` 的权威 DNS 还是阿里云，所以**你自己的域名、自己的证书、RAM 密钥、lego 全部照常**。

## 先选一家（这一步决定后面所有细节）

| | **ChmlFrp** | **OpenFrp** |
| --- | --- | --- |
| 流量 | ✅ **官方明确「不限流量」** | ❌ 免费档 **1 GB + 每日签到** |
| 带宽/隧道数 | 国外 32 Mbps / 国内 8 Mbps，免费 4 条 | 12 Mbps，免费 4 条 |
| 实名认证 | **实测免实名**（香港-2 建 HTTPS 隧道没被拦）；官方只提「国内隧道」要实名 | 仅**大陆节点**要；**海外节点不用** |
| 备案 | 境内节点要（海外不要） | 境内节点要（海外不要） |
| 官网 | <https://chmlfrp.net> / 面板 <https://panel.chmlfrp.net> | <https://www.openfrp.net> / 控制台 <https://console.openfrp.net> |

**怎么选：**

- **首选 ChmlFrp 的香港节点**（本文档默认按它写）：**实测免实名**、延迟低（~30~60 ms）、官方明确「**不限流量**」。
  （官方文档写「国内隧道」要实名，但**香港-2 建 HTTPS 隧道实际没被拦** —— 以创建时面板为准。）
- **想更保守、避开第三方实名政策** → OpenFrp 海外节点（香港 / 日本）：同样免实名 + 低延迟，但免费档只有 **1 GB + 签到**。
- **延迟权衡（重要）**：境外节点里 **香港 / 日本 ≈ 20~60 ms**，**英国 / 美国 ≈ 180~250 ms+**。
  本 App 是**点播**（搜一次、拖一下进度条都是一次新请求），**延迟比带宽重要**（一路 128 kbps 音频才 0.13 Mbps，带宽怎么都够）。
  **能选香港就别选伦敦** —— 伦敦下 ping 节点 ~250 ms，每次请求往返 **400~600 ms**，搜索类会明显发钝。

> 另一个「不限流量」的 **Fnat**（<https://www.fnat.cc>，5~20 Mbps）资料还少、看着较新，可以当备选，但别只依赖它。
> **别用** `freenat.win / .bid / .club` 那类个人自建 + 旧版 frp（0.9.3）：老客户端连不上新版、安全性未知、随时消失。

## 原理

```
   手机/电脑 ──443──► 服务商海外节点 ──frp 隧道（家里主动连出）──► 家里 frpc ──► 127.0.0.1:443
                                                                      Caddy（你自己那张证书）
                                                                        └──► 127.0.0.1:8000
   uisc.tonicaphid.cn  CNAME 指到「节点/映射地址」
```

### 三家通用的三条铁律

| 铁律 | 为什么 |
| --- | --- |
| **隧道类型选 `HTTPS`**（不是 `HTTP`） | `HTTPS` 按 SNI 透传，TLS 在**你家里**终结，**用你自己那张证书**，中转方看到的是密文；`HTTP` 隧道由对方终结 TLS，**你的登录密码对方能看到**。ChmlFrp 原文也写了：SSL 证书「需要挂载到您本地的网站上，而不是映射上」 |
| **节点选境外**（香港 / 日本 / 新加坡…） | 境内节点做网页服务要**已备案域名**（ChmlFrp 原文：「如果您使用了中国境内节点，映射任何网页服务，您都需要解析自己的已备案域名」）。**香港属于「特别行政区」，同样免备案**（建隧道页原话：「中国境内节点(**中国特别行政区除外**)」），而且对国内延迟最低，优先选它 |
| **自备证书** | 现在这张 lego 签的 Let's Encrypt 证书就是（见下面「证书续签的坑」） |

---

## 一、建隧道

### ChmlFrp

1. 注册（ChmlFrp 的实名**只卡「国内隧道」**：香港 / 大陆要实名；**「国外隧道」如英国伦敦实测免实名**，以创建时面板是否放行为准）；
2. `隧道管理 → 隧道列表 → 添加隧道`：

   | 配置项 | 填什么 |
   | --- | --- |
   | 隧道名称 | `tonicuisc` |
   | 本地 IP | `127.0.0.1` |
   | 节点 | **境外**节点。**必须带 `建站` 标签**（没这个标签的节点不允许跑网站服务）；优先选标了「**大陆移动优化**」的大带宽节点，推荐 `中国香港-*`（免备案 + 延迟最低） |
   | 端口类型 | **`HTTPS`** |
   | 内网端口 | `443`（家里 Caddy） |
   | 域名类型 | `自定义` |
   | 域名 | `uisc.tonicaphid.cn` |
   | 外网端口 | HTTPS 下一般自动固定/隐藏；若仍要求填，点「随机外网端口」 |
   | 高级设置 | **不用动**（官方：「非必要不建议修改」） |

3. 点「确定」后，**面板会直接把 CNAME 目标告诉你**，例如选香港-2 就是：

   > 请使用自定义域名需要将您的 `uisc.tonicaphid.cn` 域名通过 **CNAME 解析至 `xg-2.frp.one`** 才能正常访问。
   >
   > ⚠️ **这句只当参考**：面板给的是最省事的 CNAME 做法，但 **CNAME 不能和 AAAA 共存**。
   > 我们这套要「A = 节点 IP + AAAA = 家里 v6」，所以**实际填的是 A 记录**，
   > 把 `xg-2.frp.one` 解析出来的 IP 手填进去 —— 见下面第二节。

   > **备案**：建隧道页顶部会提示「自定义域名解析到中国境内节点(**中国特别行政区除外**)建站，您的域名必须在工信部备案」——
   > **香港属于「特别行政区」**，所以**免备案**，`tonicaphid.cn` 不用去工信部备案。

### OpenFrp

`控制台 → 创建隧道`，**选带 `√ HTTPS` 标记的海外节点**：类型 `HTTPS`、本地 `127.0.0.1`、端口 `443`、绑定域名 `uisc.tonicaphid.cn`、**强制 HTTPS 开**。
`管理隧道 → 详情` 里记下 **节点地址**（形如 `xxx.ofalias.com`）和**隧道 ID**。

---

## 二、阿里云侧（**顺序很重要**）

> **⚠️ 面板说的「CNAME 解析至 `xg-2.frp.one`」不是唯一的做法，而且在我们这套里不能用。**
> 我们要的是 **A + AAAA 共存**（v6 直连家里、v4 进隧道），而 **CNAME 不能和 AAAA 共存** ——
> 所以节点这条改成 **手写 A 记录指向节点 IP**。下面两步就是全部。

### 1. A 记录 = 节点 IP（不是 CNAME）

| 记录类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| `A` | `uisc` | 面板节点域名**解析出来的 IP**（例：`xg-2.frp.one` → `23.147.68.16`） |

「绑定域名」和这里的主机记录**必须完全对得上**（面板绑 `uisc.tonicaphid.cn`，主机记录就填 `uisc`）。核对：

```bash
nslookup uisc.tonicaphid.cn 223.5.5.5     # 应该出节点 IP
nslookup xg-2.frp.one 223.5.5.5           # 对比，两者应该一致
```

> ⚠️ **节点 IP 变了要手改这一条**（TTL 600，10 分钟内生效）。写死 IP 的代价就是这个，
> 换来的是能同时挂 AAAA。

### 2. AAAA 记录 = 家里的稳定 IPv6（**「v6 不过隧道」靠的就是它**）

给 `uisc` 再挂一条 **AAAA**，值由家里的 DDNS 定时器刷：

```bash
cd /opt/Tonicuisc
python -m tonicuisc_server ddns install --only v6   # 只刷 AAAA，绝不碰 A
python -m tonicuisc_server ddns status
```

> RAM 密钥照旧要用（刷 AAAA + lego 签证书走 `_acme-challenge`）。
>
> ⚠️ **必须是 `--only v6`**：默认的 `both` / `v4` 会顺手把 **A 刷成家里的 v4** ——
> 家宽 443 入站被运营商封，那条 A 等于废的，**没 v6 的客户端会全部连不上**。
> （`ddns.py` 里也是这么注释的：「家里没公网 v4 时可以 `ddns run --only v6`：只刷 AAAA、不碰 A」。）

### 于是两族各走各的

| 客户端 | 连到 | 实际路径 |
| --- | --- | --- |
| **有 IPv6** | AAAA（家里 v6） | **直连家里 443**，不过中转，延迟最低 |
| **没有 IPv6** | A（节点 IP） | 节点 443 → 按 SNI 送进 frp 隧道 → 家里 |

客户端两族谁先谁后由 `app/lib/core/dual_stack.dart` 决定：**v6 优先，250ms 回落 v4**。

---

## 三、家里侧：装 frpc + 开机自启

### ChmlFrp

> ⚠️ **下载页有两个入口，别点错**：要的是**按架构分**的命令行版（**系统选 `Linux`、架构选 `amd64`** → 得到 `ChmlFrp-*.tar.gz`）；
> 另一个「**图形客户端**」（`ChmlFrpLauncher`，`.deb` / `.AppImage` / `.exe`）是 Electron GUI —— **服务器没桌面环境跑不了，也不适合做常驻服务**。

```bash
# 从面板「软件下载」拿 Linux amd64 包（版本以面板为准）
wget <面板给的下载地址> -O chmlfrp.tar.gz
mkdir -p /opt/chmlfrp && tar -zxf chmlfrp.tar.gz -C /opt/chmlfrp --strip-components=1
cd /opt/chmlfrp
ls                                  # 应看到 frpc（可能还有 frpc.ini）
chmod +x frpc

# 从面板「隧道管理 → 配置文件」生成并拷回来，写到 frpc.ini
vi /opt/chmlfrp/frpc.ini

# 先手动试跑（出现「映射启动成功」就对了），Ctrl+C 停掉
./frpc -c frpc.ini
```

自启：

```bash
sudo tee /etc/systemd/system/chmlfrp.service >/dev/null <<'EOF'
[Unit]
Description=ChmlFrp Frpc
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Restart=always
RestartSec=5s
WorkingDirectory=/opt/chmlfrp
ExecStart=/opt/chmlfrp/frpc -c /opt/chmlfrp/frpc.ini
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now chmlfrp
systemctl status chmlfrp --no-pager
```

### OpenFrp

```bash
cd /usr/local/bin
wget <面板里复制的下载地址> -O frpc_linux_amd64.zip
unzip frpc_linux_amd64.zip && chmod 755 frpc_linux_amd64
./frpc_linux_amd64 --version
```

```bash
sudo tee /etc/systemd/system/openfrp.service >/dev/null <<'EOF'
[Unit]
Description=OpenFrp Frpc Daemon
After=network-online.target
Wants=network-online.target

[Service]
User=root
Restart=always
RestartSec=5s
WorkingDirectory=/usr/local/bin
ExecStart=/usr/local/bin/frpc_linux_amd64 -u <token> -p <隧道ID>
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now openfrp
systemctl status openfrp --no-pager
```

> 起不来：`journalctl -u chmlfrp -n 50`（或 `openfrp`）；本地 `443` 必须真的有东西在听（家里 Caddy：`curl -sk https://127.0.0.1/api/health`）。

---

## 四、证书续签（**已实测：没问题**）

> **前提已经变了（2026-10-17）**：`uisc` 现在是 **A + AAAA**，**不是 CNAME** ——
> 下面这段「CNAME 会不会拦住子记录」的探索**现在用不上了**：`_acme-challenge.uisc` 就是个普通子记录，
> DNS-01 天然正常。留着是因为它记录了一个反直觉的实测结论（万一哪天改回 CNAME 才用到）。

`uisc` 变成 CNAME 后，DNS-01 要写的验证记录 `_acme-challenge.uisc.tonicaphid.cn` 就落在 CNAME 节点下面。
按「CNAME 会拦住子记录」的常见说法，续签应该失败 —— **但实测证明并不会**。

**实测方法（安全，不碰证书）**：在阿里云手动加一条测试 TXT，再从公网解析器查它：

| 记录类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| `TXT` | `_acme-challenge.uisc` | `test123` |

```bash
nslookup -type=TXT _acme-challenge.uisc.tonicaphid.cn 8.8.8.8
```

**结果：查得到 `"test123"`** —— 阿里云允许建这条记录，公网解析器也能直接查到。
按 DNS 解析算法（RFC 1034 §4.3.2），递归解析器是拿着**完整名字**去问权威服务器的，
服务器在 `_acme-challenge.uisc` 节点上有数据就直接答 —— **父节点的 CNAME 不会拦子节点查询**。

所以 **DNS-01 续签正常，不需要通配符**。（测完把那条测试 TXT 删掉即可，不删也无害。）

> ⚠️ **别用 `acme --force` 来"验证"这一条**：证书刚签过的话，Let's Encrypt 那边还留着
> **30 天的授权缓存**，lego 会打印 `Authorization is already valid; skipping the challenge`、
> **直接跳过 DNS-01 挑战** —— 那次"成功"什么都没证明。要真测就用上面的 TXT 法。

### 万一哪天真挂了：两条后路

| 方案 | 做法 | 代价 |
| --- | --- | --- |
| **A. 通配符证书** | 改签 `*.tonicaphid.cn`，挑战记录落在根域 `_acme-challenge.tonicaphid.cn`，**天然绕开 `uisc` CNAME**。前提：根域 `tonicaphid.cn` 自己**不能是 CNAME**。改 `core/acme.py` 加个域名参数即可 | 需重签一次；通配符密钥泄露波及所有子域 |
| **B. 让 Caddy 自己签（TLS-ALPN-01）** | 去掉 Caddyfile 里的 `tls <crt> <key>`，让 Caddy 自动申请：Let's Encrypt 连 `uisc.tonicaphid.cn:443`，经隧道到家里完成校验，**完全不用 DNS** | **续签时隧道必须在线**；绕开现有 lego 流程 |

---

## 五、（可选）让服务端拿到真实客户端 IP

走隧道后，家里 Caddy 看到的对端是 frpc（本机），**所有用户会被限速当成同一个**。
用 **Proxy Protocol V2**，两步都要做（**只改一边会让隧道完全不可用**）：

1. 隧道 → **高级设置** → 打开 **`Proxy Protocol V2`**（OpenFrp 有这个开关；ChmlFrp 在「高级设置」里，非必要不建议改的那栏）；
2. 家里 `deploy/Caddyfile.template` 的全局块里让 Caddy 认这个头：

   ```
   {
       storage file_system {STORAGE}
       servers {
           listener_wrappers {
               proxy_protocol
               tls                 # ← 必须写回来！这个块会覆盖默认的 tls 包装器
           }
       }
       log { ... }
   }
   ```

> **坑**：`listener_wrappers` 一旦写了就**替换**默认的 `tls` 包装器，漏写 `tls` → 全部 HTTPS 握手失败。
>
> **坑**：开了 `proxy_protocol` 后，**所有**连到 443 的连接都必须带 PROXY 头 —— 局域网直连家里 Caddy 也会被拒。
> 不开也能用：限速按「一个 IP」算（个人自用基本无所谓）。

---

## 六、验证

```bash
# 1) frpc 在跑
systemctl status chmlfrp --no-pager      # 或 openfrp

# 2) A 指向节点、AAAA 指向家里（两族都要在）
nslookup -type=A    uisc.tonicaphid.cn 223.5.5.5     # → 23.147.68.16（节点）
nslookup -type=AAAA uisc.tonicaphid.cn 223.5.5.5     # → 240e:...（家里）

# 2b) 两条路各验一遍（★ 别只看 ping，必须 curl）
curl -4 -s https://uisc.tonicaphid.cn/api/health     # 通 → 隧道那条路 OK
curl -6 -s https://uisc.tonicaphid.cn/api/health     # 通 → 家里 v6 直连 OK

# 3) 从外面（手机关 WiFi 用蜂窝）打开
#    https://uisc.tonicaphid.cn/api/health
#    ★证书必须是**你自己那张 Let's Encrypt**（不是服务商的）—— 这才说明是端到端，没被对方解密

# 4) 家里证书状态
python -m tonicuisc_server acme --status
```

第 3 条的证书检查最重要：**如果是服务商的证书，说明 TLS 被它终结了，别用**。

---

## 七、风险（免费公益服务通病）

- **不限流量 ≠ 不限速**：带宽还是 8~32 Mbps（音频够用），且可能被限速/踢；
- **稳定性**：免费节点可能人满、掉线，服务也可能随时调整或停运；
- **条款**：公益服务常禁止「大流量 / 流媒体 / 建站商用」，被判滥用会封号；
- **实名**：ChmlFrp 要实名（提交身份证信息），**介意就别选它**，走 OpenFrp 海外节点；
- **安全**：隧道 token / frpc.ini 等于你家服务的钥匙，别外传。

> 真嫌不稳，回头是那两条更硬的：**端口映射直连**（免费、无第三方）或**甲骨文自建 frp**（`deploy/vps-relay.md`，有卡时）。

---

## 大陆节点：备案拦截（2026-10-18 实测 → 2026-10-24 已解除）

> ✅ **2026-10-24 更新：这层拦截已经不存在了。** 备案号 `蒙ICP备2026011401号-1` 已下来，
> 阿里云对 `uisc.tonicaphid.cn` 的 443 拦截停止，80 端口现在是**普通 404**（不再是
> `Server: Beaver` + `Non-compliance ICP Filing`）。所以「大陆节点 + 已备案域名」是
> **可以用**的组合，不用再为了躲拦截去选境外节点。
>
> 下面这一节保留当时的 A/B 证据 —— 它解释的是「**浏览器打得开 ≠ App 连得上**」这个
> 排查陷阱（PQ 密钥共享把 SNI 挤出第一个包），这个陷阱和备案状态无关，仍然成立。
> 再遇到同类症状，先按这一节的办法分清「拦截 / 证书 / 隧道」再动手。

用**自己的大陆服务器**（阿里云杭州 ECS `47.97.176.166`）跑 frps、A 指向它，结果
**浏览器能正常打开、App 连不上**。查下来不是证书、不是客户端代码，是**备案拦截**
（当时的证据，现已解除）：

```
$ curl -i -H 'Host: uisc.tonicaphid.cn' http://47.97.176.166/
HTTP/1.1 403 Forbidden
Server: Beaver                                   # ← 阿里云的拦截服务
<title>Non-compliance ICP Filing</title>
```

拦截器**只看 TCP 第一个包里能不能读到 SNI**（域名未备案 → RST / 403 页）：

| ClientHello | 结果 |
|---|---|
| openssl 默认（3.5 起默认带 **PQ 混合密钥** `X25519MLKEM768`，hello ≈1.6 KB、跨两个包，SNI 掉到第二个包里） | ✅ 握手成功 |
| openssl `-groups X25519` / `-groups P-256` / `-tls1_2`（hello 一个包装得下，SNI 被读到） | ❌ RST |
| **非 80/443 端口**（实测 `:7500` = frps 面板的明文 HTTP） | ✅ 301，**完全没被碰** |

三条结论：

1. **和证书无关**：同一个 IP、同一个 SNI、**同一条 4 张证书的链**，PQ 那条过、X25519 那条 RST。
   lego v5 的 `.crt` 本来就是 4 条（叶子 + 3 张中间/交叉签名，见 `CHANGES-v0.2.1.md`），
   **不要删**——最后那张 `ISRG Root X2` 正是给「信任库里还没有新 ISRG 根」的客户端接链用的，
   删了有把现在好好的 v6 也弄坏的风险，而且对 v4 一点用都没有。
2. **「浏览器能开、App 不行」就是这个原因**：Chrome / OpenSSL 3.5 默认发 **PQ 密钥共享** →
   hello 跨包 → 检查器读不到 SNI → 放过；**dart:io 不发 PQ**（Flutter 侧 BoringSSL 没开）→
   hello 小 → 被 RST。**App 侧修不了**：dart:io 没暴露曲线 / 密钥共享的接口。
   唯一能骗过检查器的招是把 hello 撑大、让 SNI 掉出第一个包（比如塞个超长 ALPN）——
   **那是刻意绕备案过滤器，而且检查器行为一变就挂，不做**。
3. **拦截只打 80 / 443**：`:7500` 的明文 HTTP 一点没被碰。
   → 想用自己大陆的机器，当时只能走**非标端口**（App 地址得带端口），或者把 A 换回**境外**节点。
   **现在不用了**：备案下来之后 443 直接可用（见本节开头）。

**自己复现这个 A/B**（同一个 IP、同一个 SNI、同一条证书链，**只差 ClientHello**）：

```bash
REQ='GET /api/health HTTP/1.1\r\nHost: uisc.tonicaphid.cn\r\nConnection: close\r\n\r\n'

# ① 浏览器式（openssl 3.5 默认带 PQ 混合密钥）：拿到完整 HTTP 响应
printf "$REQ" | openssl s_client -connect uisc.tonicaphid.cn:443 -4 \
  -servername uisc.tonicaphid.cn -quiet -ign_eof
# → {"status":"ok","sources":["KuwoMusicClient"],...}

# ② App 式（只给 X25519，就是 dart:io 的 hello）：连接直接被打掉
printf "$REQ" | openssl s_client -connect uisc.tonicaphid.cn:443 -4 \
  -servername uisc.tonicaphid.cn -groups X25519 -quiet -ign_eof
# → write:errno=113（Windows 上长这样；Linux 大概是 Connection reset by peer）
```

> 所以「**浏览器打得开**」不能拿来判定 v4 通不通 —— 它靠 PQ 把 SNI 挤出了第一个包。
> **判定只能用 `curl -4` 或 `dart run app/tool/probe_net.dart`**（两者都是非 PQ 的 hello，
> 跟 App 一个待遇）。

> 顺带两个安全提醒：`47.97.176.166` 的 **7500（frps 面板）** 和 **7000（frps 控制端口）**
> 都是公网可连的——面板务必改掉默认账号 + 强密码。

## 本次实际部署（2026-10）

- **服务商**：ChmlFrp（免费用户，**未实名**）
- **节点沿革（2026-10-16 实测）**
  - ❌ `vip.bj.frp.one` → `82.156.215.91`（北京 / 腾讯云）：**被腾讯云备案拦截，弃用。**
    > **2026-10-24 补注**：这条「弃用理由」已经过期 —— 域名备案下来了，**任何大陆节点都不该再拦**。
    > 但该节点**当天实测 443 仍然握手失败**（`openssl s_client` → `write:errno=113`），
    > 所以问题在节点 / 隧道本身，不是备案；服务器开机后再复测一次才能定论。
    证据：向 `82.156.215.91:443` 发明文 `GET /api/health`，回的是
    ```
    HTTP/1.1 302 OK
    Location: https://dnspod.qcloud.com/static/webblock.html?d=uisc.tonicaphid.cn
    ```
    即腾讯云（DNSPod）把**未备案域名** `uisc.tonicaphid.cn` 拦了，而且在 **443 端口上回明文 HTTP**
    → 客户端 TLS 握手看到非 TLS 响应，报 `HandshakeException: WRONG_VERSION_NUMBER(tls_record.cc:127)`。
    正是当初预判的「境内节点 + 未备案，随时可能掐」，**已兑现**。
  - ❌ `sz.frp.one` → `47.107.155.180`（深圳 / 腾讯云）：**已挂**。2026-10-16 实测
    `curl: (35) Recv failure: Connection was reset`。它当初是通的（`Server: uvicorn` +
    `Via: 1.1 Caddy` + `200`），**没撑多久就没了** —— 再次印证「境内节点 + 未备案 = 随时掐」。
  - ✅ **现役 `xg-2.frp.one` → `23.147.68.16`（香港-2，境外免备案）**：CNAME 已切到它。
    2026-10-16 实测（**别只看 ping，必须 `curl --resolve`**）：
    ```
    curl -s --resolve uisc.tonicaphid.cn:443:23.147.68.16 https://uisc.tonicaphid.cn/api/health
    # → 200 {"status":"ok","sources":[...],"auth":"enabled"}
    ```
    **隧道活着，路径通。**
- **现役路线 = 隧道（v4）+ 家里 v6 直连（v6）**（2026-10-17 实测）：`uisc` 上是
  **A = `23.147.68.16`（香港-2 节点，**手填**，不是 CNAME）+ AAAA = 家里的稳定 IPv6**，
  两族都走 **443**、客户端地址**不带端口**。实测：
  ```
  curl -6 -s https://uisc.tonicaphid.cn/api/health   # 200，remote_ip=240e:318:3d2d:2900:4637:e6ff:fec1:c0c5（家里）
  curl -4 -s https://uisc.tonicaphid.cn/api/health   # 200，remote_ip=23.147.68.16（节点 → 进隧道）
  ```
  - **为什么 A 写成节点 IP、不用 CNAME**：CNAME **不能和 AAAA 共存**，而 AAAA 这条正是
    「有 v6 就不过隧道、直连家里」的关键。
  - **好处**：v6 客户端延迟最低（不过中转）；v4 客户端也照样能用 —— 它走的是**节点**的 443，
    **绕开了家里 v4 的 443 封锁**。
  - **维护要点**：DDNS 定时器必须 **`--only v6`**（只刷 AAAA）。用默认的 `both` / `v4`
    会把 A 刷成**家里的 v4**（443 被封）→ 没 v6 的客户端**全部连不上**。节点 IP 变了要手改 A。
  - 客户端两族谁先谁后由 `app/lib/core/dual_stack.dart` 定：**v6 优先，250ms 回落 v4**。
- **「家里 v4 直连 + 8443」没启用**（2026-10-17 核过）：部署机 `ss -tlnp` 只有 **80 / 443**，
  `.env` / systemd unit 里搜不到 `8443`；隧道节点的 8443 是**关的**
  （实测 `23.147.68.16:8443 → 拒绝连接`），**所以别只改客户端那一行**。
  完整六步见 README「HTTPS → 家里的两个坑」——那是**备选方案，不是现状**。
- **回滚方式**：v6 那条路出问题 → 删掉 AAAA，全员走隧道；想整体退回「纯隧道」→
  删掉 AAAA（A 保留也行，指向节点就是隧道）。注意**一旦加回 CNAME，AAAA 就加不回来了**。
- **隧道**：`tonicuisc`，本地 `127.0.0.1:443`（HTTPS 类型），连接地址 `uisc.tonicaphid.cn`
- **节点**：`xg-2.frp.one` → **`23.147.68.16`**（香港-2）—— ⚠️ **2026-10-18 实测它 443 已经不说 TLS 了**
  （`openssl s_client` 报 `wrong version number`，TCP 能连上但握手拿不到证书），**别再往它指**；
  `vip.bj.frp.one`（北京）被拦、`sz.frp.one`（深圳）已挂。A 记录现在指的是
  **杭州 ECS `47.97.176.166`**（自己的机器；**2026-10-24 备案通过后 443 已放行**，见下面那节）
- **⚠️ 排查网络问题的坑**：CNAME 换过之后，**公共 DNS（如 223.5.5.5）在 TTL 内仍会返回旧节点的 IP**
  `vip.bj.frp.one` → `82.156.215.91`**（缓存残留）。App 里出现 TLS 报错时，先确认解析到的是哪个 IP：
  ```
  Resolve-DnsName uisc.tonicaphid.cn -Server 223.5.5.5 -Type A -DnsOnly
  ```
  手机上同理 —— **开关一次飞行模式 / 重连 WiFi** 清掉系统 DNS 缓存即可。
- **续签**：✅ 正常。第四节那个「CNAME 会不会拦子记录」的探索**已经用不上了**（`uisc` 现在是 A + AAAA，不是 CNAME），
  `_acme-challenge.uisc` 就是普通子记录，DNS-01 天然通
- **证书现状**（2026-10-17 实测）：现役 `uisc` 证书 `issuer=CN=YE2`（Let's Encrypt 生产），
  SAN 只有 `uisc.tonicaphid.cn`，**2027-01-14 到期**（当时剩 89 天）——自动续签由部署机上的
  `acme` 流程负责（`python -m tonicuisc_server acme --status` 可看）。
  同目录下还留着一张 `tuisc.tonicaphid.cn.crt`（已不用，无害）。
- **部署机**（2026-10-17 核）：`/opt/Tonicuisc`，systemd `tonicuisc.service`（enabled + active，
  venv Python 3.13.5）+ `chmlfrp.service`（隧道）。**它不是 git 仓库、也没装 git**，
  所以更新代码靠**拷文件**，别指望 `git pull`。
- **家宽公网 IPv4 会变**：2026-10-17 实测已从 `106.35.198.214` 变成 **`106.35.197.238`**。
  走 CNAME 时 DNS 不引用它，所以**当前无害**；但这也说明「地址会变」——
  哪天切直连，**必须先把 DDNS 定时器装回来**（现在是停的），否则一变就失联。
- **待做**：家里直连（8443）的六步切换 + 端到端验证（**当前未启用**，见上）。

> 节点实测（**家里电信** ping 节点）：**北京 26 ms** ≪ **香港-2 197 ms** ≈ **伦敦 254 ms**。
> 三个节点的教训：**延迟取决于该节点对电信的线路质量，不是「香港就一定快」**。
> 伦敦 → 香港-2 提升有限（254→197，那台香港机对电信绕路）；香港-2 → 北京才是质变（197→26）。
> 顺带：**ping 只能证明「能 ping 到节点」，不能证明隧道通** —— 判定隧道要 `curl --resolve`。

## 相关文件

- `deploy/vps-relay.md` —— 甲骨文 + 自建 frp（要有 Visa）
- `deploy/cloudflare-tunnel.md` —— Cloudflare Tunnel（要换 NS）
- `server/tonicuisc_server/core/ddns.py` —— `ddns remove`（走隧道时**必须停**：CNAME 和 A/AAAA 不能共存）
  / `ddns install`（改回家里直连时**必须装回来**，否则地址一变就再也连不上）
- `server/tonicuisc_server/core/acme.py` —— lego DNS-01（第四节续签问题在这里）
- `deploy/Caddyfile.template` —— 第五节 PROXY protocol 改这里
- 官方文档：[ChmlFrp 映射教程](https://docs.chmlfrp.net/docs/use/mapping)、[ChmlFrp 自启](https://docs.chmlfrp.net/docs/use/autostart)、
  [OpenFrp HTTPS 隧道](https://docs.openfrp.net/use/configuration/others)、[OpenFrp 自启](https://docs.openfrp.net/use/boot-on-startup)、[OpenFrp 真实 IP](https://docs.openfrp.net/use/proxy-protocol)
