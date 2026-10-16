# Cloudflare Tunnel 中转 —— 免费、不要卡

> 这份文档解决的是同一件事：**在外面用蜂窝（尤其移动）连不上家里的服务端**。
> 上次选的甲骨文卡在「要 Visa 验卡」，这条换 **Cloudflare Tunnel**：不要信用卡、不要买机器，家里主动连出去，外面统一从 Cloudflare 的 443 进。
>
> 这是 `deploy/vps-relay.md`（甲骨文 + frp）的**平替**。哪天你有卡了，两份都还能用。

## 原理

还是「家里主动外连」那一套，只是中转机换成 Cloudflare 的边缘节点：

```
   手机 / 电脑 ──443──► Cloudflare 边缘 ──隧道（家里主动连出）──► cloudflared ──HTTP──► 127.0.0.1:8000
                                          （国内移动/联通/电信都能到 CF）
```

- 家里跑 `cloudflared`，**主动**连 Cloudflare，不需要家里有任何入站端口（80/443 封不封、有没有公网 IP 都无所谓）；
- **TLS 在 Cloudflare 边缘终结**，证书由 CF 自动签发（对客户端而言还是 `https://uisc.tonicaphid.cn`，**App 一行都不用改**）；
- Cloudflare Tunnel **免费版就能用**（Zero Trust 免费套餐），只要一个邮箱注册，**不要卡**。

### 三条要提前知道的代价

1. **域名的 NS 要从阿里云搬到 Cloudflare**（用自定义域名 `uisc.tonicaphid.cn` 就必须这样，免费版没有别的办法）。
   搬完之后 `tonicaphid.cn` 的解析就由 Cloudflare 管，**阿里云那边的记录不再生效**。
2. **所有流量都会走 Cloudflare**。搬到 CF 之后没法再「一部分客户端直连家里 v6」——`uisc.tonicaphid.cn` 这个域名只能指向 CF。电信/联通的用户延迟会比原来的 v6 直连高一些。
3. **CF 免费版大陆线路一般**，且条款（2.8 条）**限制用 CDN 大量跑非 HTML 内容**。个人小规模一般没事，但音频串流要**先实测播放顺不顺**。

---

## 一、把域名搬到 Cloudflare

### 0. 先备份现有解析

搬之前把阿里云上的记录列清楚（搬过去要核对，别漏了邮件 MX 之类）：

```bash
cd /opt/Tonicuisc
python -m tonicuisc_server ddns check          # 列出 uisc 现有的 A / AAAA
```

有别的记录（`tuisc`、`www`、MX、TXT…）就顺手在阿里云控制台截个图。

### 1. 在 Cloudflare 添加站点

- 注册 / 登录 <https://dash.cloudflare.com>（邮箱即可，不要卡）；
- `Add a site` → 输入 `tonicaphid.cn` → 选 **Free** 计划；
- CF 会自动扫描现有记录并导入。**逐条核对**（尤其是 `uisc` / `tuisc`，以及任何 MX / TXT）；
- 记下 CF 给你的**两个 NS**，形如 `xxx.ns.cloudflare.com` / `yyy.ns.cloudflare.com`。

### 2. 到阿里云改 NS

`阿里云控制台 → 域名 → 域名列表 → tonicaphid.cn → DNS 修改（修改 DNS 服务器）` →
把原来的阿里云 DNS 服务器换成 **CF 给的那两个**，保存。

> `.cn` 域名改 NS 一般**几十分钟到几小时**生效。CF 控制台里站点状态会从 `Pending` 变 `Active`。
> 等它变绿（Active）再做下一步——**没 Active 之前 CF 不接管流量**。

### 3. 生效后核对

```bash
# 用公共 DNS 查，确认权威 NS 已经是 Cloudflare
nslookup -type=NS tonicaphid.cn 223.5.5.5
```

看到 `*.ns.cloudflare.com` 就对了。这时阿里云解析里的记录已经**失效**（记录还在，回退时有用）。

---

## 二、建 Tunnel + 在服务器装 `cloudflared`

> 推荐**控制台 token 方式**：不用在无头服务器上开浏览器登录，最省事。

### 1. 家里服务器装 `cloudflared`（Debian）

```bash
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
  | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt-get update && sudo apt-get install -y cloudflared
cloudflared --version
```

> 装不上（源被墙 / 没 `lsb_release`）就下静态二进制，ARM 机器把 `amd64` 换成 `arm64`：
>
> ```bash
> curl -fL -o cloudflared \
>   https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
> sudo install -m755 cloudflared /usr/local/bin/cloudflared
> ```

### 2. 在 Cloudflare 建隧道

`Cloudflare 控制台 → Zero Trust → Networks → Tunnels → Create a tunnel`：

1. 选 **Cloudflared** → 起个名字（比如 `tonicuisc`）→ Save；
2. 页面会给出各系统的安装命令，**复制 Linux 那条**，长得像：

   ```bash
   sudo cloudflared service install eyJhIjoi...
   ```

   （那串 `eyJ...` 就是隧道 token，**别外传**——等于隧道的钥匙）。

### 3. 在服务器上跑它

```bash
# 用上面复制到的那条命令，它会顺便把自己装成 systemd 服务
sudo cloudflared service install eyJhIjoi...

systemctl status cloudflared --no-pager      # active (running) 就对了
journalctl -u cloudflared -n 30 --no-pager   # 能看到 "Registered tunnel connection" ×4
```

### 4. 绑定域名（Public Hostname）

回到刚才那个隧道页面，`Public Hostnames → Add a public hostname`：

| 字段 | 填什么 |
| --- | --- |
| Subdomain | `uisc` |
| Domain | `tonicaphid.cn` |
| Type | `HTTP` |
| URL | `127.0.0.1:8000` |

保存后 CF 会自动建一条 `uisc.tonicaphid.cn → <隧道ID>.cfargotunnel.com` 的代理（橙云）记录。

> **为什么直连 `127.0.0.1:8000` 而不是家里的 Caddy？**
> Tunnel 下的 TLS 由 CF 边缘终结，家里这段本来就是明文，绕 Caddy 少一层、也少一份证书麻烦。
> 家里 Caddy 那套留着不动就行（下面「衔接」一节说它什么时候该关）。

---

## 三、和现有部署怎么衔接（**重要，别漏**）

NS 搬到 Cloudflare 之后，之前那套「阿里云 DNS + DDNS + lego 证书」有几处会失效，要收拾一下：

### 1. 停掉 DDNS 定时器

`update_dns.py` 是往**阿里云**写记录的，NS 搬走后阿里云不再是权威，**再刷也没意义**（还会白跑）：

```bash
cd /opt/Tonicuisc
python -m tonicuisc_server ddns remove
python -m tonicuisc_server ddns status      # 确认定时器没了
```

> 别担心：如果哪天想回退，阿里云那边的记录**还在**，NS 改回去就恢复。

### 2. Caddy / lego 证书

- Tunnel 路径用不到家里的证书，**Caddy 可以关掉**（也可以留着，只是白跑）：
  ```bash
  # server/.env 里加：
  TONICUISC_CADDY_AUTOSTART=0
  ```
- ⚠️ **lego 的 DNS-01 也是往阿里云写 TXT 的**，NS 搬走后**续签会失败**。
  证书一般 90 天到期，短期不会立刻出问题；要干净就顺手把自动签发也关了
  （`TONICUISC_ACME_DNS01=0`），或者哪天仍需要「家里直连 v6」，就把 lego 的 DNS provider 换成 Cloudflare。

### 3. 服务端 `.env` 不用动（但要确认）

```bash
TONICUISC_HOST=127.0.0.1                  # 本来就对：服务端只在回环，外面从隧道进
TONICUISC_PUBLIC_URL=https://uisc.tonicaphid.cn   # 横幅/登录链接，还是这个
TONICUISC_TRUST_PROXY=1                   # 保持 1，CF 会写来源 IP（见第五节）
```

### 4. `tuisc.tonicaphid.cn`（测试域名）要单独处理

它也在 `tonicaphid.cn` 这个 zone 里，**跟着一起搬到 CF 了**，
而它现在靠阿里云 DDNS 刷新——搬走之后**同样失效**。两个选择：

- 在 CF 里给它建一条 **DNS only（灰云）** 的 `A`/`AAAA` 指测试机，地址变了手动改；或
- 干脆也给测试机建一个 Tunnel（步骤同上，换个 hostname）。

> 加了灰云就得注意：**灰云不经过 CF**，测试机自己得有能入站的端口。

---

## 四、验证

```bash
# 1) 隧道是否在线
systemctl status cloudflared --no-pager
journalctl -u cloudflared -n 30 --no-pager | grep -i connection

# 2) 域名解析到 Cloudflare（不再是家里 IP）
nslookup uisc.tonicaphid.cn 223.5.5.5

# 3) 从外面（手机关 WiFi 用蜂窝）打开
#    https://uisc.tonicaphid.cn/api/health
#    证书应该是 Cloudflare 签发的（不再是家里那张 Let's Encrypt）
```

三条都对，蜂窝就能连了。

---

## 五、真实客户端 IP 的说明

Tunnel 下，家里服务端看到的对端是 `cloudflared`（本机），真实 IP 由 CF 通过请求头带进来。请求头有两个：

- `CF-Connecting-IP` —— CF 写的**真实客户端 IP，最可靠**；
- `X-Forwarded-For` —— 现在代码取的是它**最左边**那一段。

⚠️ CF **会保留客户端自己带的 `X-Forwarded-For`**，只在后面追加真实 IP。所以严格说，`X-Forwarded-For` 最左边是**可以被伪造的**（能绕限速）。个人自用影响不大，要严谨的话有两个办法：

- 让服务端优先读 `CF-Connecting-IP`（就在 `server/tonicuisc_server/api/deps.py` 的 `client_key()` 里，加几行、加个开关）；
- 或者给 `/login` 之类敏感接口在 CF 上加一条 **Rate limiting 规则**兜底。

> 要的话我可以把 `CF-Connecting-IP` 的优先读取加上（带开关，默认行为不变）。

---

## 六、排错

| 现象 | 多半是 |
| --- | --- |
| 域名打不开 / NXDOMAIN | 站点还没 `Active`（NS 没生效），或 NS 改错了 |
| `502` / `1033` | 隧道不在线：`systemctl status cloudflared`；或 Public Hostname 的 URL 指错了 |
| 页面能开但接口 502 | 家里服务端没跑 / 不在 `127.0.0.1:8000`：`curl -s localhost:8000/api/health` |
| 蜂窝连不上、WiFi 能连 | 之前 DNS 缓存：`nslookup` 换 `1.1.1.1` 再试；或手机上还有旧地址缓存 |
| 播放音频卡 | CF 免费线路问题，见开头「代价 3」，先实测确认 |

---

## 七、回退（不喜欢就撤）

Cloudflare 免费、无负担，撤起来也简单：

1. 阿里云控制台把 NS **改回**原来的阿里云 DNS 服务器；
2. 阿里云上原来的 A / AAAA 记录**还在**，恢复 DDNS 定时器：
   ```bash
   python -m tonicuisc_server ddns install --only v6 --every 10m   # 或 --only both
   ```
3. 家里把 `TONICUISC_CADDY_AUTOSTART` 改回 `1`（如果用得着），CF 这边的隧道可以留着不用或删掉。

---

## 相关文件

- `deploy/vps-relay.md` —— 另一条路（甲骨文 + frp），有卡时可用
- `deploy/update_dns.py` / `server/tonicuisc_server/core/ddns.py` —— 阿里云 DDNS（**搬到 CF 后要停掉**）
- `deploy/Caddyfile.template` —— 家里 Caddy 模板（Tunnel 路径用不到）
- `server/.env.example` —— `TONICUISC_HOST` / `TONICUISC_PUBLIC_URL` / `TONICUISC_TRUST_PROXY` / `TONICUISC_CADDY_AUTOSTART`
- `server/tonicuisc_server/api/deps.py` —— `client_key()` 取来源 IP 的地方（`X-Forwarded-For`，见第五节）
