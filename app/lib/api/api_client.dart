import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/song.dart';

const String _prefsKey = 'server_base_url';

/// 桌面端默认连本机，移动端需要填局域网地址（在设置里改）。
String defaultBaseUrl() {
  if (Platform.isWindows || Platform.isLinux || Platform.isMacOS) {
    return 'http://127.0.0.1:8000';
  }
  return 'http://192.168.1.100:8000';
}

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// 401：key 失效 / 设备被吊销 / 还没配对。
class UnauthorizedException extends ApiException {
  UnauthorizedException([String message = '未授权：请重新配对设备']) : super(message);
}

/// 配对成功后拿到的设备凭据（`apiKey` 只会出现这一次）。
class PairResult {
  const PairResult({required this.apiKey, required this.deviceId, required this.deviceName});
  final String apiKey;
  final String deviceId;
  final String deviceName;
}

/// 设备码登录：`POST /api/device/start` 的返回。
class DeviceLoginStart {
  const DeviceLoginStart({required this.userCode, required this.expiresIn, required this.loginPath});
  final String userCode;
  final int expiresIn;
  final String loginPath;
}

/// 音源直链：客户端直接连 CDN，不用等服务端中转。
class DirectSource {
  const DirectSource(this.url, this.headers);
  final String url;
  final Map<String, String> headers;
}

/// 一次搜索的结果 + 服务端耗时（秒）。
class SearchResult {
  const SearchResult({required this.items, required this.elapsed});

  final List<Song> items;
  final double elapsed;
}

class ApiClient {
  ApiClient(this.baseUrl, {this.apiKey});

  final String baseUrl;

  /// 设备专属 key，随每个请求发 `X-API-Key`。
  final String? apiKey;

  Map<String, String> get _headers => {
        if (apiKey != null && apiKey!.isNotEmpty) 'X-API-Key': apiKey!,
      };

  static String normalizeBaseUrl(String value) => value.trim().replaceAll(RegExp(r'/+$'), '');

  /// 用一次性配对码换取本设备专属的 API Key。
  static Future<PairResult> pair({
    required String baseUrl,
    required String code,
    required String name,
  }) async {
    final resp = await http
        .post(
          Uri.parse('${normalizeBaseUrl(baseUrl)}/api/pair'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'code': code.trim(), 'name': name.trim()}),
        )
        .timeout(const Duration(seconds: 20));
    if (resp.statusCode == 401) {
      throw UnauthorizedException(_detailOf(resp, '配对码错误或已过期'));
    }
    if (resp.statusCode != 200) {
      throw ApiException(_detailOf(resp, 'HTTP ${resp.statusCode}'));
    }
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return PairResult(
      apiKey: (data['api_key'] ?? '').toString(),
      deviceId: (data['device_id'] ?? '').toString(),
      deviceName: (data['name'] ?? '').toString(),
    );
  }

  /// 设备码登录第一步：把 App 生成的设备码登记到服务端。
  static Future<DeviceLoginStart> startDeviceLogin({
    required String baseUrl,
    required String userCode,
    required String pollSecret,
    required String deviceName,
  }) async {
    final resp = await http
        .post(
          Uri.parse('${normalizeBaseUrl(baseUrl)}/api/device/start'),
          headers: {'Content-Type': 'application/json'},
          body: jsonEncode({'user_code': userCode, 'poll_secret': pollSecret, 'name': deviceName}),
        )
        .timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw ApiException(_detailOf(resp, 'HTTP ${resp.statusCode}'));
    }
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return DeviceLoginStart(
      userCode: (data['user_code'] ?? userCode).toString(),
      expiresIn: (data['expires_in'] as num?)?.toInt() ?? 0,
      loginPath: (data['login_path'] ?? '/login').toString(),
    );
  }

  /// 设备码登录第二步：轮询状态；approved 时会带一次 api_key。
  static Future<Map<String, dynamic>> deviceStatus({
    required String baseUrl,
    required String userCode,
    required String pollSecret,
  }) async {
    final uri = Uri.parse('${normalizeBaseUrl(baseUrl)}/api/device/status').replace(queryParameters: {
      'user_code': userCode,
      'poll_secret': pollSecret,
    });
    final resp = await http.get(uri).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw ApiException(_detailOf(resp, 'HTTP ${resp.statusCode}'));
    }
    return jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
  }

  static Future<String> loadBaseUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_prefsKey);
    return (saved == null || saved.isEmpty) ? defaultBaseUrl() : saved;
  }

  static Future<void> saveBaseUrl(String value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, normalizeBaseUrl(value));
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl$path');
    return query == null ? uri : uri.replace(queryParameters: query);
  }

  Future<SearchResult> search(String keyword, {List<String> sources = const [], int limit = 50}) async {
    final resp = await http
        .get(
          _uri('/api/search', {
            'keyword': keyword,
            if (sources.isNotEmpty) 'sources': sources.join(','),
            'limit': '$limit',
          }),
          headers: _headers,
        )
        .timeout(const Duration(seconds: 90));
    if (resp.statusCode != 200) {
      throw _errorOf(resp);
    }
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final items = (data['items'] as List?) ?? const [];
    return SearchResult(
      items: items.map((e) => Song.fromJson(e as Map<String, dynamic>)).toList(),
      elapsed: (data['elapsed'] as num?)?.toDouble() ?? 0,
    );
  }

  Uri streamUri(String id) => _uri('/api/stream/$id');

  Uri downloadUri(String id) => _uri('/api/download/$id');

  /// 歌词（可能是带时间戳的 LRC 文本）。
  Future<String> lyric(String id) async {
    final resp = await http.get(_uri('/api/lyric/$id'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw _errorOf(resp);
    }
    return utf8.decode(resp.bodyBytes);
  }

  /// 取音源直链；失败时抛 [ApiException]，调用方回退到服务端代理流。
  Future<DirectSource> directUrl(String id) async {
    final resp = await http.get(_uri('/api/url/$id'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw _errorOf(resp);
    }
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final url = (data['url'] ?? '').toString();
    if (url.isEmpty) {
      throw ApiException('音源没有返回直链');
    }
    final headers = <String, String>{};
    final rawHeaders = data['headers'];
    if (rawHeaders is Map) {
      rawHeaders.forEach((key, value) => headers['$key'] = '$value');
    }
    return DirectSource(url, headers);
  }

  Future<String> downloadTo(Song song, Directory dir) async {
    final resp = await http.get(downloadUri(song.id), headers: _headers).timeout(const Duration(minutes: 15));
    if (resp.statusCode != 200) {
      throw _errorOf(resp);
    }
    final ext = song.ext.isEmpty ? 'mp3' : song.ext;
    final file = File('${dir.path}${Platform.pathSeparator}${song.fileBaseName}.$ext');
    await file.writeAsBytes(resp.bodyBytes, flush: true);
    return file.path;
  }

  /// 401 单独抛 [UnauthorizedException]，UI 收到后清凭据并引导重新配对。
  ApiException _errorOf(http.Response resp) {
    final detail = _messageOf(resp);
    if (resp.statusCode == 401) return UnauthorizedException(detail);
    return ApiException(detail);
  }

  /// 「我的」页面：当前账户 + 本设备信息。
  Future<Map<String, dynamic>> me() async {
    final resp = await http.get(_uri('/api/me'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw _errorOf(resp);
    return jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
  }

  /// 已登录设备列表。
  Future<List<Map<String, dynamic>>> devices() async {
    final resp = await http.get(_uri('/api/devices'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw _errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ((data['items'] as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList();
  }

  /// 吊销某台设备（不需要知道它的 key）。
  Future<void> revokeDevice(String deviceId) async {
    final resp = await http.delete(_uri('/api/devices/$deviceId'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw _errorOf(resp);
  }

  String _messageOf(http.Response resp) {
    try {
      final body = jsonDecode(utf8.decode(resp.bodyBytes));
      if (body is Map && body['detail'] != null) return body['detail'].toString();
    } catch (_) {
      // 保留原始状态码
    }
    return 'HTTP ${resp.statusCode}';
  }

  static String _detailOf(http.Response resp, String fallback) {
    try {
      final body = jsonDecode(utf8.decode(resp.bodyBytes));
      if (body is Map && body['detail'] != null) return body['detail'].toString();
    } catch (_) {
      // 用兜底文案
    }
    return fallback;
  }

  /// 桌面端返回「下载」目录，iOS 返回应用文档目录。
  static Future<Directory> downloadDirectory() async {
    if (Platform.isIOS || Platform.isAndroid) {
      return getApplicationDocumentsDirectory();
    }
    return await getDownloadsDirectory() ?? getApplicationDocumentsDirectory();
  }
}
