import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../models/song.dart';

/// QQ 封面缓存。
///
/// 搜索接口**不等封面**（不然每次搜索都要多等几百毫秒），列表先按原图画出来，
/// 页面再调 [resolve] 去问一次 `/api/covers`，查到的图到了就换上去。
/// 服务端也有一层缓存，所以同一首歌只会真的查一次。
class CoverCache {
  CoverCache._();

  /// 有任何新封面就 +1，[SongAvatar] 靠它刷新。
  static final ValueNotifier<int> revision = ValueNotifier<int>(0);

  static final Map<String, String> _covers = <String, String>{};
  static final Set<String> _requested = <String>{};

  static String? of(String songId) => _covers[songId];

  /// 把这批歌里还没有封面、也没问过的挑出来问一次。
  static Future<void> resolve(ApiClient api, List<Song> songs) async {
    final pending = <String>[];
    for (final song in songs) {
      if (song.id.isEmpty || _covers.containsKey(song.id) || _requested.contains(song.id)) continue;
      pending.add(song.id);
    }
    if (pending.isEmpty) return;
    _requested.addAll(pending);
    try {
      final found = await api.covers(pending);
      if (found.isEmpty) return;
      _covers.addAll(found);
      revision.value++;
    } catch (_) {
      // 拿不到就用原图，下次进页面再试
      _requested.removeAll(pending);
    }
  }

  static void clear() {
    _covers.clear();
    _requested.clear();
    revision.value++;
  }
}
