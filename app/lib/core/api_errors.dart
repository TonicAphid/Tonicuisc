import 'dart:convert';

import 'package:http/http.dart' as http;

/// 客户端异常类型 + 把服务端响应翻译成「人话」。
///
/// 单独一个文件：这些类型被播放器、页面、网络层到处引用，不该和 `ApiClient` 绑在一起。

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 401：key 失效 / 设备被吊销 / 还没配对。
class UnauthorizedException extends ApiException {
  UnauthorizedException([super.message = '未授权：请重新登录设备']);
}

/// 从响应体里抠 FastAPI 的 `detail`；抠不到就用兜底文案。
String detailOf(http.Response response, [String? fallback]) {
  try {
    final body = jsonDecode(utf8.decode(response.bodyBytes));
    if (body is Map && body['detail'] != null) return body['detail'].toString();
  } catch (_) {
    // 保持原样，用兜底文案
  }
  return fallback ?? 'HTTP ${response.statusCode}';
}

/// 把非 200 响应变成异常；401 单独抛 [UnauthorizedException]。
ApiException errorOf(http.Response response) {
  final detail = detailOf(response);
  if (response.statusCode == 401) return UnauthorizedException(detail);
  return ApiException(detail);
}

/// 把原始异常变成人话：网络连不上 / 服务端版本太旧 / 其他。
String friendlyError(Object error, String baseUrl) {
  final text = '$error';
  if (error is UnauthorizedException) return text;
  final looksOffline = text.contains('SocketException') ||
      text.contains('Connection refused') ||
      text.contains('Connection failed') ||
      text.contains('No route to host') ||
      text.contains('Failed host lookup') ||
      text.contains('TimeoutException') ||
      text.contains('timed out');
  if (looksOffline) {
    return '连不上服务器 $baseUrl\n'
        '· 确认服务端已经启动（浏览器打开 $baseUrl/api/health 应该能看到 JSON）\n'
        '· 检查手机网络；改过地址的话，「我的」页长按版本号可重新设置';
  }
  if (text.contains('404') || text.toLowerCase().contains('not found')) {
    return '服务端返回 404：接口不存在。\n'
        '多半是服务端代码太旧，更新后重启一次（要看到 /api/library 这些新接口）。';
  }
  return text;
}
