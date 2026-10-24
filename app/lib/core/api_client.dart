import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

import '../models/song.dart';
import 'api_errors.dart';

// 异常类型 / 错误文案（api_errors）与服务器地址（server_config）都在各自文件里，
// 这里 re-export：页面一直是从 api_client 拿这些东西的，不必每处多 import 一行。
export 'api_errors.dart';
export 'server_config.dart';

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

  /// 播放器直接发起的请求（`/api/stream` 回退流、下载）**必须**带上的头。
  ///
  /// just_audio 自己发 HTTP，不走 [ApiClient] 的方法；漏了 `X-API-Key`，
  /// 服务端开着鉴权时回退流就是 401，整首歌放不出来。
  Map<String, String> get streamHeaders => _headers;

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
      throw errorOf(resp);
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
    if (resp.statusCode != 200) throw errorOf(resp);
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
      throw errorOf(resp);
    }
    return utf8.decode(resp.bodyBytes);
  }

  /// 增强歌词：`ready=false` 表示服务端正在后台匹配，过两秒再问。
  Future<Map<String, dynamic>?> lyricEnhanced(String id) async {
    final resp = await http
        .get(_uri('/api/lyric/$id', {'format': 'enhanced'}), headers: _headers)
        .timeout(const Duration(seconds: 20));
    if (resp.statusCode == 404) return null;
    if (resp.statusCode != 200) throw errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes));
    return data is Map<String, dynamic> ? data : null;
  }

  /// 取音源直链；失败时抛 [ApiException]，调用方回退到服务端代理流。
  Future<DirectSource> directUrl(String id) async {
    final resp = await http.get(_uri('/api/url/$id'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) {
      throw errorOf(resp);
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
      throw errorOf(resp);
    }
    final ext = song.ext.isEmpty ? 'mp3' : song.ext;
    final file = File('${dir.path}${Platform.pathSeparator}${song.fileBaseName}.$ext');
    await file.writeAsBytes(resp.bodyBytes, flush: true);
    return file.path;
  }

  /// 「我的」页面：当前账户 + 本设备信息。
  Future<Map<String, dynamic>> me() async {
    final resp = await http.get(_uri('/api/me'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw errorOf(resp);
    return jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
  }

  /// 已登录设备列表。
  Future<List<Map<String, dynamic>>> devices() async {
    final resp = await http.get(_uri('/api/devices'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw errorOf(resp);
    final data = jsonDecode(utf8.decode(resp.bodyBytes)) as Map<String, dynamic>;
    return ((data['items'] as List?) ?? const []).map((e) => (e as Map).cast<String, dynamic>()).toList();
  }

  /// 吊销某台设备（不需要知道它的 key）。
  Future<void> revokeDevice(String deviceId) async {
    final resp = await http.delete(_uri('/api/devices/$deviceId'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw errorOf(resp);
  }

  /// 「列表」页用：三个列表的数量 + 喜欢/收藏的 id。
  Future<LibrarySummary> librarySummary() async {
    final resp = await http.get(_uri('/api/library/summary'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw errorOf(resp);
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
    if (resp.statusCode != 200) throw errorOf(resp);
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
    if (resp.statusCode != 200) throw errorOf(resp);
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
    if (resp.statusCode != 200) throw errorOf(resp);
  }

  Future<void> libraryRemove(String kind, String songId) async {
    final resp = await http
        .delete(_uri('/api/library/$kind/$songId'), headers: _headers)
        .timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200 && resp.statusCode != 404) throw errorOf(resp);
  }

  Future<void> libraryClear(String kind) async {
    final resp = await http.delete(_uri('/api/library/$kind'), headers: _headers).timeout(const Duration(seconds: 20));
    if (resp.statusCode != 200) throw errorOf(resp);
  }

  /// 桌面端返回「下载」目录，iOS 返回应用文档目录。
  static Future<Directory> downloadDirectory() async {
    if (Platform.isIOS || Platform.isAndroid) {
      return getApplicationDocumentsDirectory();
    }
    return await getDownloadsDirectory() ?? getApplicationDocumentsDirectory();
  }
}
