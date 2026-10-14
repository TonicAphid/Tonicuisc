import 'package:shared_preferences/shared_preferences.dart';

/// 服务器地址与本地设置（用户改过就一直在本机生效）。
///
/// 默认走域名 + HTTPS，用户不用填 IP；改地址的入口藏在「我的」页长按版本号。

const String _prefsKey = 'server_base_url';

/// 默认服务器地址（首次启动用，之后存在本地设置里）。
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
