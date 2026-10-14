import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../core/api_errors.dart';
import '../../core/server_config.dart';

/// 设备码登录协议：App 生成码 → 用户在浏览器批准 → App 轮询取回密钥。
///
/// 从 `ApiClient` 里搬出来的：这是**登录协议**，不是通用 HTTP 客户端的一部分。
/// 轮询节奏也放在这儿（[PollSchedule]），页面只管「什么时候停」。

/// 服务端的状态字面量，客户端只认这几个（服务端见 `auth/constants.py` 的 DeviceStatus）。
class DeviceLoginState {
  /// 已登记，等用户在浏览器里批准
  static const String pending = 'pending';

  /// 已批准，密钥还没被取走
  static const String approved = 'approved';

  /// 用户在网页上拒绝
  static const String denied = 'denied';

  /// 码不存在 / 已过期
  static const String expired = 'expired';

  /// 已批准，但密钥已经被取走过一次——这一轮结束了，别再轮询
  static const String claimed = 'claimed';
}

/// `POST /api/device/start` 的返回。
class DeviceLoginStart {
  const DeviceLoginStart({required this.userCode, required this.expiresIn, required this.loginPath});

  final String userCode;
  final int expiresIn;
  final String loginPath;
}

/// 轮询到的一次状态。
class DeviceLoginStatus {
  const DeviceLoginStatus(this.status, {this.apiKey = '', this.deviceId = '', this.username = ''});

  final String status;
  final String apiKey;
  final String deviceId;
  final String username;

  bool get isApproved => status == DeviceLoginState.approved;

  bool get isPending => status == DeviceLoginState.pending;

  /// 这一轮结束了，不用再问（拒绝 / 过期 / 已经被取走）。
  bool get isFinished =>
      status == DeviceLoginState.denied ||
      status == DeviceLoginState.expired ||
      status == DeviceLoginState.claimed;
}

/// 批准成功后的结果，交给上层存凭据（省得四个字符串按顺序传）。
class DeviceLoginResult {
  const DeviceLoginResult({
    required this.apiKey,
    required this.deviceId,
    required this.deviceName,
    this.username = '',
  });

  final String apiKey;
  final String deviceId;
  final String deviceName;
  final String username;
}

/// 轮询节奏：第一次 1 秒（用户点完「批准」马上就能进），之后慢慢退到 3 秒。
///
/// 以前是固定 2 秒一次：批准后最多白等 2 秒，而一个挂着没人理的登录页
/// 每分钟还要打 30 次接口。现在两头都顾上了。
class PollSchedule {
  const PollSchedule({
    this.first = const Duration(seconds: 1),
    this.max = const Duration(seconds: 3),
    this.step = const Duration(seconds: 1),
  });

  final Duration first;
  final Duration max;
  final Duration step;

  Duration delay(int attempt) {
    // 不用 clamp：它返回 num，Duration(seconds:) 只吃 int，得再转一次，不值得
    final raw = first.inSeconds + attempt * step.inSeconds;
    final seconds = raw < first.inSeconds ? first.inSeconds : (raw > max.inSeconds ? max.inSeconds : raw);
    return Duration(seconds: seconds);
  }
}

/// 第一步：把本机生成的设备码登记到服务端。
///
/// [deviceId] 是本机已存的旧设备 ID（重新配对时带上）：服务端据此就地复用旧记录、
/// 换发新 key，同一台设备登录多少次都只留一条记录。首次登录传空即可。
Future<DeviceLoginStart> startDeviceLogin({
  required String baseUrl,
  required String userCode,
  required String pollSecret,
  required String deviceName,
  String deviceId = '',
}) async {
  final resp = await http
      .post(
        Uri.parse('${normalizeBaseUrl(baseUrl)}/api/device/start'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({
          'user_code': userCode,
          'poll_secret': pollSecret,
          'name': deviceName,
          if (deviceId.isNotEmpty) 'device_id': deviceId,
        }),
      )
      .timeout(const Duration(seconds: 20));
  if (resp.statusCode != 200) {
    throw ApiException(detailOf(resp, 'HTTP ${resp.statusCode}'));
  }
  final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
  return DeviceLoginStart(
    userCode: (data['user_code'] ?? userCode).toString(),
    expiresIn: (data['expires_in'] as num?)?.toInt() ?? 0,
    loginPath: (data['login_path'] ?? '/login').toString(),
  );
}

/// 第二步：问一次状态；approved 时会带回一次性密钥。
///
/// `poll_secret` 走**请求体**：走 query 的话会原样写进服务端 / 反代的访问日志，
/// 而这个秘密的全部意义就是「只有这台 App 知道」。
Future<DeviceLoginStatus> pollDeviceLogin({
  required String baseUrl,
  required String userCode,
  required String pollSecret,
}) async {
  final resp = await http
      .post(
        Uri.parse('${normalizeBaseUrl(baseUrl)}/api/device/status'),
        headers: {'Content-Type': 'application/json'},
        body: jsonEncode({'user_code': userCode, 'poll_secret': pollSecret}),
      )
      .timeout(const Duration(seconds: 20));
  if (resp.statusCode != 200) {
    throw ApiException(detailOf(resp, 'HTTP ${resp.statusCode}'));
  }
  final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
  return DeviceLoginStatus(
    (data['status'] ?? '').toString(),
    apiKey: (data['api_key'] ?? '').toString(),
    deviceId: (data['device_id'] ?? '').toString(),
    username: (data['username'] ?? '').toString(),
  );
}
