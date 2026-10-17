import 'package:shared_preferences/shared_preferences.dart';

/// 服务器地址与本地设置（用户改过就一直在本机生效）。
///
/// 默认走域名 + HTTPS，用户不用填 IP；改地址的入口藏在「我的」页长按版本号。

const String _prefsKey = 'server_base_url';

/// 默认服务器地址（首次启动用，之后存在本地设置里）。
///
/// **不带端口**（走 443）。`uisc.tonicaphid.cn` 上是两条记录：
/// **A = frp 节点（`23.147.68.16`，香港-2）**、**AAAA = 家里的 IPv6**。所以：
///
/// - **有 IPv6** 的客户端连**家里**的 443（**不过隧道**，最短路径）
/// - **没有 IPv6** 的客户端连**节点**的 443，节点按 SNI 送进隧道 → 家里
///
/// 客户端**优先 IPv6**、v6 不通 250ms 内回落 IPv4（`dual_stack.dart`）——
/// 正好就是「v6 走直连、v6 不通走隧道」，两条路都通。
///
/// ⚠️ **别把 `:8443` 加回来**：那是「家里 v4 直连 + 非标端口」的**备选**方案，没启用
/// （隧道节点的 8443 是关的，加上去就是 App 全连不上，实测过）。
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
