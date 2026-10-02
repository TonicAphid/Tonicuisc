import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models/song.dart';

const String _prefsKey = 'server_base_url';

/// 默认服务器地址。要换服务器就改这一行（首次启动用，之后存在本地设置里）。
const String kDefaultServerUrl = 'http://192.168.5.37:8000';

/// 所有平台的默认地址；用户在「我的 → 服务器地址」里改过就优先用改过的。
String defaultBaseUrl() => kDefaultServerUrl;

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
        '· 确认地址填对、手机和服务器在同一个网络\n'
        '· 换地址：我的 → 服务器地址';
  }
  if (text.contains('404') || text.toLowerCase().contains('not found')) {
    return '服务端返回 404：接口不存在。\n'
        '多半是服务端代码太旧，更新后重启一次（要看到 /api/library 这些新接口）。';
  }
  return text;
}

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
  const SearchResult({
    required this.items,
    required this.elapsed,
    this.hasMore = false,
    this.total = 0,
  });

  final List<Song> items;
  final double elapsed;

  /// 还有下一页（滑到底继续加载）。
  final bool hasMore;

  /// 服务端这次一共拿到多少条。
  final int total;
}

/// 喜欢 / 收藏 / 历史 的数量与 id 集合。
class LibrarySummary {
  const LibrarySummary({
    required this.like,
    required this.favorite,
    required this.history,
    required this.likeIds,
    required this.favoriteIds,
  });

  final int like;
  final int favorite;
  final int history;
  final Set<String> likeIds;
  final Set<String> favoriteIds;

  int countOf(String kind) {
    switch (kind) {
      case 'like':
        return like;
      case 'favorite':
        return favorite;
      default:
        return history;
    }
  }
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

  /// 搜索。[offset] 不为 0 就是「滑到底再要 15 条」。
  Future<SearchResult> search(
    String keyword, {
    List<String> sources = const [],
    int offset = 0,
    int limit = 15,
    bool refresh = false,
  }) async {
    final resp = await http
        .get(
          _uri('/api/search', {
            'keyword': keyword,
            if (sources.isNotEmpty) 'sources': sources.join(','),
            'offset': '$offset',
            'limit': '$limit',
            if (refresh) 'refresh': 'true',
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
      hasMore: data['has_more'] == true,
      total: (data['total'] as num?)?.toInt() ?? 0,
    );
  }

  /// 按需取 QQ 封面，只返回查到的（没查到就用音源原图）。
  Future<Map<String, String>> covers(List<String> ids) async {
    if (ids.isEmpty) return const {};
    final resp = await http
        .post(
          _uri('/api/covers'),
          headers: {..._headers, 'Content-Type': 'application/json'},
          body: jsonEncode({'ids': ids}),
        )
        .timeout(const Duration(seconds: 30));
    if (resp.statusCode != 200) throw _errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final raw = data['covers'];
    if (raw is! Map) return const {};
    return raw.map((key, value) => MapEntry('$key', '$value'));
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

  /// 「列表」页用：三个列表的数量 + 喜欢/收藏的 id。
  Future<LibrarySummary> librarySummary() async {
    final resp = await http.get(_uri('/api/library/summary'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw _errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final counts = (data['counts'] as Map?)?.cast<String, dynamic>() ?? const {};
    return LibrarySummary(
      like: (counts['like'] as num?)?.toInt() ?? 0,
      favorite: (counts['favorite'] as num?)?.toInt() ?? 0,
      history: (counts['history'] as num?)?.toInt() ?? 0,
      likeIds: ((data['like_ids'] as List?) ?? const []).map((e) => '$e').toSet(),
      favoriteIds: ((data['favorite_ids'] as List?) ?? const []).map((e) => '$e').toSet(),
    );
  }

  /// 某个列表的歌曲（带元信息）。
  Future<List<Song>> libraryList(String kind, {int limit = 200}) async {
    final resp = await http
        .get(_uri('/api/library/$kind', {'limit': '$limit'}), headers: _headers)
        .timeout(const Duration(seconds: 30));
    if (resp.statusCode != 200) throw _errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ((data['items'] as List?) ?? const [])
        .map((e) => Song.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  /// 歌手主页：这个歌手在音源上能搜到的歌。
  Future<List<Song>> artist(String name, {List<String> sources = const [], int limit = 50}) async {
    final resp = await http
        .get(
          _uri('/api/artist', {
            'name': name,
            if (sources.isNotEmpty) 'sources': sources.join(','),
            'limit': '$limit',
          }),
          headers: _headers,
        )
        .timeout(const Duration(seconds: 90));
    if (resp.statusCode != 200) throw _errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ((data['items'] as List?) ?? const [])
        .map((e) => Song.fromJson((e as Map).cast<String, dynamic>()))
        .toList();
  }

  Future<void> libraryAdd(String kind, String songId) async {
    final resp = await http
        .post(_uri('/api/library/$kind'), headers: {..._headers, 'Content-Type': 'application/json'},
            body: jsonEncode({'song_id': songId}))
        .timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw _errorOf(resp);
  }

  Future<void> libraryRemove(String kind, String songId) async {
    final resp = await http
        .delete(_uri('/api/library/$kind/$songId'), headers: _headers)
        .timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200 && resp.statusCode != 404) throw _errorOf(resp);
  }

  Future<void> libraryClear(String kind) async {
    final resp = await http.delete(_uri('/api/library/$kind'), headers: _headers).timeout(const Duration(seconds: 20));
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
