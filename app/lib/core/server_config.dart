import 'package:shared_preferences/shared_preferences.dart';

/// 服务器地址与本地设置（用户改过就一直在本机生效）。
///
/// 默认走域名 + HTTPS，用户不用填 IP；改地址的入口藏在「我的」页长按版本号。

const String _prefsKey = 'server_base_url';

/// 默认服务器地址（首次启动用，之后存在本地设置里）。
///
/// **不带端口**：uisc 现在走的是 **frp 隧道**（`CNAME → xg-2.frp.one`），
/// 隧道那条路映射的是 **443**，所以域名后面**不能带 `:8443`**。
///
/// ⚠️ 「家里直连 + 8443」那套方案**还没启用**（DNS 仍是 CNAME、8443 没人监听）。
/// 哪天真的切过去了，要同时满足这些才能改这一行：
///   1. 阿里云删掉 CNAME，改成 A（家里公网 v4）+ AAAA（家里稳定 v6）
///   2. 把 DDNS 定时器装回来（家宽地址会变，不装就失联）
///   3. 路由器把 8443 映射到部署机；IPv6 那边在防火墙放行 8443
///   4. 部署机 `server/.env` 加 `TONICUISC_HTTPS_PORT=8443` 并重启
/// 只改这一行、那几样没做 → App 会连不上（隧道节点的 8443 是关的，实测过）。
const String kDefaultServerUrl = 'https://uisc.tonicaphid.cn';

/// 所有平台的默认地址；用户在本机改过（我的 → 长按版本号）就优先用改过的。
String defaultBaseUrl() => kDefaultServerUrl;

/// 去掉首尾空白和结尾斜杠，避免拼出 `//api/health` 这种地址。
String normalizeBaseUrl(String value) => value.trim().replaceAll(RegExp(r'/+$'), '');

Future<String> loadBaseUrl() async {
  final prefs = await SharedPreferences.getInstance();
  final saved = prefs.getString(_prefsKey);
  return (saved == null || saved.isEmpty) ? defaultBaseUrl() : saved;
}

Future<void> saveBaseUrl(String value) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.setString(_prefsKey, normalizeBaseUrl(value));
}
