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

/// 音源直链：客户端直接连 CDN，不用等服务端中转。
class DirectSource {
  const DirectSource(this.url, this.headers);
  final String url;
  final Map<String, String> headers;
}

class ApiClient {
  ApiClient(this.baseUrl);

  final String baseUrl;

  static Future<String> loadBaseUrl() async {
    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_prefsKey);
    return (saved == null || saved.isEmpty) ? defaultBaseUrl() : saved;
  }

  static Future<void> saveBaseUrl(String value) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_prefsKey, value.trim());
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl$path');
    return query == null ? uri : uri.replace(queryParameters: query);
  }

  Future<List<Song>> search(String keyword, {List<String> sources = const [], int limit = 50}) async {
    final resp = await http
        .get(_uri('/api/search', {
          'keyword': keyword,
          if (sources.isNotEmpty) 'sources': sources.join(','),
          'limit': '$limit',
        }))
        .timeout(const Duration(seconds: 90));
    if (resp.statusCode != 200) {
      throw ApiException(_messageOf(resp));
    }
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    final items = (data['items'] as List?) ?? const [];
    return items.map((e) => Song.fromJson(e as Map<String, dynamic>)).toList();
  }

  Uri streamUri(String id) => _uri('/api/stream/$id');

  Uri downloadUri(String id) => _uri('/api/download/$id');

  /// 歌词（可能是带时间戳的 LRC 文本）。
  Future<String> lyric(String id) async {
    final resp = await http.get(_uri('/api/lyric/$id')).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw ApiException(_messageOf(resp));
    }
    return utf8.decode(resp.bodyBytes);
  }

  /// 取音源直链；失败时抛 [ApiException]，调用方回退到服务端代理流。
  Future<DirectSource> directUrl(String id) async {
    final resp = await http.get(_uri('/api/url/$id')).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw ApiException(_messageOf(resp));
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
    final resp = await http.get(downloadUri(song.id)).timeout(const Duration(minutes: 15));
    if (resp.statusCode != 200) {
      throw ApiException(_messageOf(resp));
    }
    final ext = song.ext.isEmpty ? 'mp3' : song.ext;
    final file = File('${dir.path}${Platform.pathSeparator}${song.fileBaseName}.$ext');
    await file.writeAsBytes(resp.bodyBytes, flush: true);
    return file.path;
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

  /// 桌面端返回「下载」目录，iOS 返回应用文档目录。
  static Future<Directory> downloadDirectory() async {
    if (Platform.isIOS || Platform.isAndroid) {
      return getApplicationDocumentsDirectory();
    }
    return await getDownloadsDirectory() ?? getApplicationDocumentsDirectory();
  }
}
