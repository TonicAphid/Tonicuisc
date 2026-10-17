import 'dart:io';

import 'package:shared_preferences/shared_preferences.dart';

import 'dual_stack.dart';

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
    // 全 App 只有这一个 HttpClient 工厂（`main.dart` 里装了 HttpOverrides.global），
    // 双栈策略（IPv6 优先）挂在这里就等于全 App 生效，不用每个请求各管各的。
    // ⚠️ 装了 connectionFactory 之后，TLS 由 dual_stack **自己**握手（dart:io 不代劳），
    // 所以直连时上面那个 badCertificateCallback 不会被调用 —— 开关必须再传一次进去。
    // （上面那行留着是为了**走代理**那条路：那种情况 dart:io 会自己握手并调它。）
    installDualStack(client, allowBadCertificate: () => allow);
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
