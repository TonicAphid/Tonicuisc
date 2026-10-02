import 'package:flutter/foundation.dart';

import '../api/api_client.dart';

/// 喜欢 / 收藏 / 历史 的本地状态：搜索页的红心、「列表」页的数字都读它。
///
/// 服务端是唯一数据源，这里只是缓存；每次增删都会用返回结果刷新计数。
class LibraryState extends ChangeNotifier {
  LibraryState(this.api);

  ApiClient api;

  int likeCount = 0;
  int favoriteCount = 0;
  int historyCount = 0;

  Set<String> _likedIds = <String>{};
  Set<String> _favoriteIds = <String>{};

  bool liked(String songId) => _likedIds.contains(songId);
  bool favorited(String songId) => _favoriteIds.contains(songId);

  int countOf(String kind) {
    switch (kind) {
      case 'like':
        return likeCount;
      case 'favorite':
        return favoriteCount;
      default:
        return historyCount;
    }
  }

  Future<void> refresh() async {
    try {
      final summary = await api.librarySummary();
      applySummary(summary);
    } catch (_) {
      // 拉不到就保持原样，下次再试
    }
  }

  void applySummary(LibrarySummary summary) {
    likeCount = summary.like;
    favoriteCount = summary.favorite;
    historyCount = summary.history;
    _likedIds = {...summary.likeIds};
    _favoriteIds = {...summary.favoriteIds};
    notifyListeners();
  }

  /// 切换「喜欢」/「收藏」；本地先翻转，失败再翻回来。
  Future<void> toggle(String kind, String songId) async {
    final target = kind == 'like' ? _likedIds : _favoriteIds;
    final wasOn = target.contains(songId);
    if (wasOn) {
      target.remove(songId);
    } else {
      target.add(songId);
    }
    _bump(kind, wasOn ? -1 : 1);
    notifyListeners();
    try {
      if (wasOn) {
        await api.libraryRemove(kind, songId);
      } else {
        await api.libraryAdd(kind, songId);
      }
    } catch (_) {
      // 回滚
      if (wasOn) {
        target.add(songId);
      } else {
        target.remove(songId);
      }
      _bump(kind, wasOn ? 1 : -1);
      notifyListeners();
      rethrow;
    }
  }

  /// 播放时上报历史（失败无所谓，不影响播放）。
  Future<void> recordPlay(String songId) async {
    try {
      await api.libraryAdd('history', songId);
      historyCount += 1;
      notifyListeners();
    } catch (_) {
      // 忽略
    }
  }

  Future<void> removeFrom(String kind, String songId) async {
    await api.libraryRemove(kind, songId);
    if (kind == 'like') {
      _likedIds.remove(songId);
    } else if (kind == 'favorite') {
      _favoriteIds.remove(songId);
    }
    await refresh();
  }

  Future<void> clear(String kind) async {
    await api.libraryClear(kind);
    await refresh();
  }

  void _bump(String kind, int delta) {
    switch (kind) {
      case 'like':
        likeCount = (likeCount + delta).clamp(0, 1 << 30);
        break;
      case 'favorite':
        favoriteCount = (favoriteCount + delta).clamp(0, 1 << 30);
        break;
      default:
        historyCount = (historyCount + delta).clamp(0, 1 << 30);
    }
  }
}
