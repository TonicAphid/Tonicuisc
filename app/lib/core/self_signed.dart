import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';

/// 自签证书支持（内网 HTTPS 用）。
///
/// 公网域名走 Let's Encrypt，证书本来就受信任；但内网自签（
/// `python -m tonicuisc_server cert 192.168.5.37`）客户端默认会拒绝连接。
/// 这里给一个**显式开关**：默认关，用户在「我的」页长按版本号进去手动打开，
/// 打开后所有 HTTPS 请求都不再校验证书链——知道自己连的是哪台机器才该开。
class SelfSignedOverrides extends HttpOverrides {
  /// 是否允许不受信任的证书（进程内全局，开关一变立刻生效）。
  static bool allow = false;

  @override
  HttpClient createHttpClient(SecurityContext? context) {
    final client = super.createHttpClient(context);
    // 注意：回调**总是**装上，只在里面读开关——这样用户中途改开关不用重建连接池。
    client.badCertificateCallback = (cert, host, port) => allow;
    return client;
  }
}

const String _prefsKey = 'allow_self_signed';

Future<bool> loadAllowSelfSigned() async {
  final prefs = await SharedPreferences.getInstance();
  return prefs.getBool(_prefsKey) ?? false;
}

Future<void> saveAllowSelfSigned(bool value) async {
  final prefs = await SharedPreferences.getInstance();
  await prefs.setBool(_prefsKey, value);
}
