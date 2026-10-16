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

   > **备案**：建隧道页顶部会提示「自定义域名解析到中国境内节点(**中国特别行政区除外**)建站，您的域名必须在工信部备案」——
   > **香港属于「特别行政区」**，所以**免备案**，`tonicaphid.cn` 不用去工信部备案。

### OpenFrp

`控制台 → 创建隧道`，**选带 `√ HTTPS` 标记的海外节点**：类型 `HTTPS`、本地 `127.0.0.1`、端口 `443`、绑定域名 `uisc.tonicaphid.cn`、**强制 HTTPS 开**。
`管理隧道 → 详情` 里记下 **节点地址**（形如 `xxx.ofalias.com`）和**隧道 ID**。

---

## 二、阿里云侧（**顺序很重要**）

### 1. 先停掉 DDNS 定时器

`uisc.tonicaphid.cn` 马上要变成 CNAME，而 **CNAME 不能和 A/AAAA 共存**。
家里那个 `ddns` 定时器还在给 `uisc` 刷 AAAA，**不停掉它就会把 CNAME 顶掉**：

```bash
cd /opt/Tonicuisc
python -m tonicuisc_server ddns remove
python -m tonicuisc_server ddns status      # 确认没了
```

> RAM 密钥不会丢：`uisc` 不再需要 A/AAAA，但 lego 签证书（走 `_acme-challenge`）和别的域名照常用得着它。

### 2. 删掉 `uisc` 现有的 A / AAAA

阿里云 → 云解析 → `tonicaphid.cn` → 把 `uisc` 的 **AAAA**（和 A，如果有）删掉。

### 3. 加 CNAME

| 记录类型 | 主机记录 | 记录值 |
| --- | --- | --- |
| `CNAME` | `uisc` | 面板给的**节点域名**（例：`xg-2.frp.one`） |

「绑定域名」和这里的主机记录**必须完全对得上**。核对：

```bash
nslookup -type=CNAME uisc.tonicaphid.cn 223.5.5.5
```

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

# 2) 域名 CNAME 指到了节点
nslookup -type=CNAME uisc.tonicaphid.cn 223.5.5.5

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

## 本次实际部署（2026-10）

- **服务商**：ChmlFrp（免费用户，**未实名**）
- **主用节点**：`北京多线`（`vip.bj.frp.one`，**家里 ping 实测 26 ms**，目前最快）
  - ⚠️ **北京是「中国境内节点」** —— ChmlFrp 规则：境内节点建站需域名已备案。本域名**未备案**，实测暂时没被拦，但**随时可能失效**（甚至封号风险）。
  - 因此**保留香港-2 作热备**：另起一个 frpc 实例常连 `xg-2.frp.one`，北京被掐时把 CNAME 记录值改回 `xg-2.frp.one` 即可，**1 分钟回滚**。
- **隧道**：`tonicuisc`，本地 `127.0.0.1:443`（HTTPS 类型），连接地址 `uisc.tonicaphid.cn`
- **CNAME 目标**：`vip.bj.frp.one`（北京）；热备 `xg-2.frp.one`（香港-2）
- **续签**：✅ 已实测 —— `uisc` 转 CNAME 后，`_acme-challenge.uisc` 仍能被公网解析器查到（见第四节），DNS-01 续签正常，不需要通配符
- **待做**：手机蜂窝端到端验证

> 节点实测（**家里电信** ping 节点）：**北京 26 ms** ≪ **香港-2 197 ms** ≈ **伦敦 254 ms**。
> 三个节点的教训：**延迟取决于该节点对电信的线路质量，不是「香港就一定快」**。
> 伦敦 → 香港-2 提升有限（254→197，那台香港机对电信绕路）；香港-2 → 北京才是质变（197→26）。
> 顺带：**ping 只能证明「能 ping 到节点」，不能证明隧道通** —— 判定隧道要 `curl --resolve`。

## 相关文件

- `deploy/vps-relay.md` —— 甲骨文 + 自建 frp（要有 Visa）
- `deploy/cloudflare-tunnel.md` —— Cloudflare Tunnel（要换 NS）
- `server/tonicuisc_server/core/ddns.py` —— `ddns remove`（**这次必须停**）
- `server/tonicuisc_server/core/acme.py` —— lego DNS-01（第四节续签问题在这里）
- `deploy/Caddyfile.template` —— 第五节 PROXY protocol 改这里
- 官方文档：[ChmlFrp 映射教程](https://docs.chmlfrp.net/docs/use/mapping)、[ChmlFrp 自启](https://docs.chmlfrp.net/docs/use/autostart)、
  [OpenFrp HTTPS 隧道](https://docs.openfrp.net/use/configuration/others)、[OpenFrp 自启](https://docs.openfrp.net/use/boot-on-startup)、[OpenFrp 真实 IP](https://docs.openfrp.net/use/proxy-protocol)
