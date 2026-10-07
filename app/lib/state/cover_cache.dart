import 'package:flutter/foundation.dart';

import '../api/api_client.dart';
import '../models/song.dart';

/// QQ 封面缓存。
///
/// 搜索接口**不等封面**（不然每次搜索都要多等几百毫秒），列表先按原图画出来，
/// 页面再调 [resolve] 去问一次 `/api/covers`，查到的图到了就换上去。
/// 服务端也有一层缓存，所以同一首歌只会真的查一次。
///
/// 服务端只回**有结论**的 id，缺席 = 它还在查，[resolve] 会过两秒再问——
/// 这样音源原图只是「暂用」，不会被当成结论锁死，QQ 封面查到就换上。
class CoverCache {
  CoverCache._();

  /// 有任何新封面就 +1，[SongAvatar] 靠它刷新。
  static final ValueNotifier<int> revision = ValueNotifier<int>(0);

  static final Map<String, String> _covers = <String, String>{};
  static final Set<String> _requested = <String>{};

  /// 单次 [resolve] 内对「还没结论」的最多连着再问几轮（每轮隔 [_retryDelay]）。
  ///
  /// 问完这几轮还没结论的会退出等待、**不记成空串**，等下次进页面再问。
  static const int _maxUndecidedRounds = 2;
  static const Duration _retryDelay = Duration(seconds: 2);

  static String? of(String songId) => _covers[songId];

  /// 把这批歌里还没有封面、也没问过的挑出来问一次。
  ///
  /// 服务端**只回有结论的 id**（含空串 = 查过、确实没有）；没出现的表示它还在查。
  /// 这种「待定」既不能记成空串（等于宣布没有，QQ 封面永远换不上），
  /// 也不能装作问过了，所以过两秒再问一次。
  static Future<void> resolve(ApiClient api, List<Song> songs) => _resolve(api, songs, 0);

  static Future<void> _resolve(ApiClient api, List<Song> songs, int round) async {
    final pending = <String>[];
    for (final song in songs) {
      if (song.id.isEmpty || _covers.containsKey(song.id) || _requested.contains(song.id)) continue;
      pending.add(song.id);
    }
    if (pending.isEmpty) return;
    _requested.addAll(pending);
    try {
      final found = await api.covers(pending);
      final undecided = <String>[];
      for (final id in pending) {
        final url = found[id];
        if (url != null) {
          _covers[id] = url;
        } else {
          _requested.remove(id); // 放回待问，下一轮还能再问
          undecided.add(id);
        }
      }
      if (found.isNotEmpty) revision.value++;
      if (undecided.isEmpty || round >= _maxUndecidedRounds) return;
      await Future<void>.delayed(_retryDelay);
      final retry = songs.where((song) => undecided.contains(song.id)).toList();
      if (retry.isNotEmpty) await _resolve(api, retry, round + 1);
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
