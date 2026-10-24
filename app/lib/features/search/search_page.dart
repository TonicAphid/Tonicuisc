import 'dart:async';

import 'package:flutter/cupertino.dart';

import '../../core/api_client.dart';
import '../../models/song.dart';
import '../player/player_controller.dart';
import '../../shared/cover_cache.dart';
import '../library/library_state.dart';
import '../../shared/song_avatar.dart';
import '../../shared/toast.dart';
import 'artist_page.dart';

/// 搜索页（首页 tab）：搜索、试听、下载、点红心。
///
/// 全 Cupertino：搜索框用 `CupertinoSearchTextField`（自带放大镜 + 清除按钮，
/// 键盘确认键也固定是「搜索」），列表行自己画（`ListTile` 那套 Material 的
/// 选中态 / 水波纹在这里不合适）。音源现在固定酷我，不再显示选择胶囊。
class SearchPage extends StatefulWidget {
  const SearchPage({
    super.key,
    required this.api,
    required this.player,
    required this.library,
    required this.onUnauthorized,
  });

  final ApiClient api;
  final PlayerController player;
  final LibraryState library;
  final Future<void> Function(String message) onUnauthorized;

  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage> {
  /// 一次要多少条；滑到底再要同样多（服务端会去重，不会给重复的）。
  static const int _pageSize = 15;

  final TextEditingController _keyword = TextEditingController();
  final ScrollController _scroll = ScrollController();

  List<Song> _results = const [];

  /// 服务端那边已经取过多少条。**不能用 `_results.length`**：翻页回来的重复条目会被去重，
  /// 一旦用显示条数当 offset，就会反复要同一段（永远要不到新歌，还一直显示「加载更多」）。
  int _offset = 0;

  /// 搜索代次：每次重新搜索 +1，让还在飞的翻页请求知道自己的结果该被丢掉。
  int _epoch = 0;

  bool _searching = false;
  bool _loadingMore = false;
  bool _hasMore = false;
  String _lastKeyword = '';
  String? _error;
  double? _elapsed;
  double? _serverElapsed;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _scroll.dispose();
    _keyword.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final remaining = _scroll.position.maxScrollExtent - _scroll.position.pixels;
    if (remaining < 400) _loadMore();
  }

  Future<void> _search() async {
    final keyword = _keyword.text.trim();
    if (keyword.isEmpty || _searching) return;
    final epoch = ++_epoch; // 让上一轮还没回来的翻页请求作废
    setState(() {
      _searching = true;
      _error = null;
      _elapsed = null;
      _serverElapsed = null;
      _hasMore = false;
      _lastKeyword = keyword;
    });
    final stopwatch = Stopwatch()..start();
    try {
      final result = await widget.api.search(
        keyword,
        offset: 0,
        limit: _pageSize,
        refresh: true,
      );
      stopwatch.stop();
      if (!mounted) return;
      if (epoch != _epoch) return; // 不该走到这（_searching 挡着）；真发生了也别拿旧结果覆盖新一轮
      setState(() {
        _results = result.items;
        _offset = result.items.length;
        _hasMore = result.hasMore;
        _elapsed = stopwatch.elapsedMilliseconds / 1000;
        _serverElapsed = result.elapsed;
      });
      unawaited(CoverCache.resolve(widget.api, result.items));
    } catch (err) {
      stopwatch.stop();
      if (!mounted) return;
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      setState(() {
        _results = const [];
        _offset = 0;
        _error = friendlyError(err, widget.api.baseUrl);
      });
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  /// 滑到底：再要 15 条，按 id 去重，绝不重复显示。
  Future<void> _loadMore() async {
    if (!_hasMore || _loadingMore || _searching || _lastKeyword.isEmpty) return;
    final epoch = _epoch;
    setState(() => _loadingMore = true);
    try {
      final result = await widget.api.search(
        _lastKeyword,
        offset: _offset,
        limit: _pageSize,
      );
      if (!mounted) return;
      if (epoch != _epoch) return; // 期间用户已经搜了别的词，这批结果不要了
      final existing = _results.map((song) => song.id).toSet();
      final fresh = result.items.where((song) => !existing.contains(song.id)).toList();
      setState(() {
        _results = [..._results, ...fresh];
        // 偏移量按「服务端给了多少」推进：即使去重去掉了几条，下一次也要接着往后要
        _offset += result.items.length;
        // 服务端说没有了、或者这一页压根是空的，就不再要了
        _hasMore = result.hasMore && result.items.isNotEmpty;
      });
      unawaited(CoverCache.resolve(widget.api, fresh));
    } on UnauthorizedException catch (err) {
      // 翻页也可能是 401：不能悄悄吞掉，否则用户停在过期的 key 上出不去
      if (!mounted) return;
      setState(() => _hasMore = false);
      await widget.onUnauthorized('$err');
    } catch (_) {
      if (mounted) setState(() => _hasMore = false);
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _download(Song song) async {
    Toast.show(context, '正在下载 ${song.name} …');
    try {
      final dir = await ApiClient.downloadDirectory();
      final path = await widget.api.downloadTo(song, dir);
      if (!mounted) return;
      Toast.show(context, '已保存到 $path');
    } catch (err) {
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      if (!mounted) return;
      Toast.show(context, '下载失败：$err');
    }
  }

  Future<void> _toggleLike(Song song) async {
    try {
      final wasLiked = widget.library.liked(song.id);
      await widget.library.toggle('like', song.id);
      if (!mounted) return;
      Toast.show(
        context,
        wasLiked ? '已取消喜欢 ${song.name}' : '已加入我喜欢 ${song.name}',
        duration: const Duration(seconds: 1),
      );
    } catch (err) {
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      if (!mounted) return;
      Toast.show(context, '操作失败：$err');
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.library,
      builder: (context, _) => Column(
        children: [
          _searchRow(context),
          _statusRow(context),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(
                _error!,
                style: TextStyle(
                  fontSize: 13,
                  color: CupertinoColors.systemRed.resolveFrom(context),
                ),
              ),
            ),
          Expanded(child: _body(context)),
        ],
      ),
    );
  }

  /// 搜索框 + 搜索按钮。
  Widget _searchRow(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
      child: Row(
        children: [
          Expanded(
            child: CupertinoSearchTextField(
              controller: _keyword,
              placeholder: '搜索歌曲 / 歌手',
              onSubmitted: (_) => _search(),
            ),
          ),
          const SizedBox(width: 8),
          CupertinoButton.filled(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            borderRadius: BorderRadius.circular(20),
            onPressed: _searching ? null : _search,
            child: const Text('搜索'),
          ),
        ],
      ),
    );
  }

  /// 「正在进行…」/「搜索完成 · 用时 …」。
  Widget _statusRow(BuildContext context) {
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 2, 16, 6),
      child: Row(
        children: [
          if (_searching) ...[
            const CupertinoActivityIndicator(radius: 7),
            const SizedBox(width: 10),
            Text('正在进行…', style: TextStyle(fontSize: 13, color: secondary)),
          ] else if (_elapsed != null)
            Expanded(
              child: Text(
                '搜索完成 · 用时 ${_elapsed!.toStringAsFixed(1)} 秒'
                '${_serverElapsed == null ? '' : '（服务端 ${_serverElapsed!.toStringAsFixed(1)} 秒）'}'
                ' · 共 ${_results.length} 首',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 12, color: secondary),
              ),
            ),
        ],
      ),
    );
  }

  Widget _body(BuildContext context) {
    if (_results.isEmpty) {
      return Center(
        child: Text(
          _searching ? '正在进行…' : '输入关键词开始搜索',
          style: TextStyle(
            fontSize: 14,
            color: CupertinoColors.secondaryLabel.resolveFrom(context),
          ),
        ),
      );
    }
    return ListView.separated(
      controller: _scroll,
      // 最后多一格：加载中 / 没有更多了
      itemCount: _results.length + 1,
      separatorBuilder: (_, __) => const _Hairline(),
      itemBuilder: (context, index) {
        if (index >= _results.length) return _footer(context);
        final song = _results[index];
        return _SongRow(
          song: song,
          current: widget.player.current?.id == song.id,
          liked: widget.library.liked(song.id),
          onTap: () => widget.player.playQueue(_results, index, widget.api),
          onSinger: song.singers.isEmpty
              ? null
              : () => ArtistPage.open(
                    context,
                    name: song.singers,
                    api: widget.api,
                    player: widget.player,
                    library: widget.library,
                    onUnauthorized: widget.onUnauthorized,
                  ),
          onLike: () => _toggleLike(song),
          onDownload: () => _download(song),
        );
      },
    );
  }

  /// 列表底部：还有就自动加载，没有就说一声。
  Widget _footer(BuildContext context) {
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    if (_loadingMore) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 20),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const CupertinoActivityIndicator(radius: 8),
            const SizedBox(width: 10),
            Text('正在加载更多…', style: TextStyle(fontSize: 13, color: secondary)),
          ],
        ),
      );
    }
    if (_hasMore) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Center(
          child: CupertinoButton(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            onPressed: _loadMore,
            child: const Text('加载更多'),
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Center(
        child: Text('共 ${_results.length} 首', style: TextStyle(fontSize: 11, color: secondary)),
      ),
    );
  }
}

/// 列表里的一行歌：封面 + 曲名 + 歌手（可点）+ 格式 + 喜欢 / 下载。
///
/// 没用 `CupertinoListTile`：它自带一条底边线（和 `separated` 的分隔线会重），
/// 而且没有「当前播放」这种选中态。
class _SongRow extends StatelessWidget {
  const _SongRow({
    required this.song,
    required this.current,
    required this.liked,
    required this.onTap,
    required this.onSinger,
    required this.onLike,
    required this.onDownload,
  });

  final Song song;
  final bool current;
  final bool liked;
  final VoidCallback onTap;
  final VoidCallback? onSinger;
  final VoidCallback onLike;
  final VoidCallback onDownload;

  @override
  Widget build(BuildContext context) {
    final accent = CupertinoTheme.of(context).primaryColor;
    final label = CupertinoColors.label.resolveFrom(context);
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        // 正在播放的那首淡淡铺一层主色（Material 的 ListTile 用 selected 也是这个意思）
        color: current ? accent.withValues(alpha: 0.10) : null,
        padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
        child: Row(
          children: [
            SongAvatar(song: song, highlight: current),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    song.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: current ? FontWeight.w600 : FontWeight.w400,
                      color: label,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Row(
                    children: [
                      // 点歌手名进歌手主页
                      Flexible(
                        child: GestureDetector(
                          onTap: onSinger,
                          child: Text(
                            song.singers,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 12.5, color: accent),
                          ),
                        ),
                      ),
                      if (song.subtitleTail.isNotEmpty)
                        Flexible(
                          child: Text(
                            ' · ${song.subtitleTail}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 12.5, color: secondary),
                          ),
                        ),
                    ],
                  ),
                ],
              ),
            ),
            Text(song.ext.toUpperCase(), style: TextStyle(fontSize: 11, color: secondary)),
            _IconTap(
              icon: liked ? CupertinoIcons.heart_fill : CupertinoIcons.heart,
              color: liked ? CupertinoColors.systemRed.resolveFrom(context) : secondary,
              onTap: onLike,
            ),
            _IconTap(icon: CupertinoIcons.cloud_download, color: secondary, onTap: onDownload),
          ],
        ),
      ),
    );
  }
}

/// 列表行尾部的小图标按钮（自带按下变淡）。
class _IconTap extends StatelessWidget {
  const _IconTap({required this.icon, required this.color, required this.onTap});

  final IconData icon;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return CupertinoButton(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      pressedOpacity: 0.5,
      onPressed: onTap,
      child: Icon(icon, size: 22, color: color),
    );
  }
}

/// 0.5px 的分隔线（iOS 的 hairline）。
class _Hairline extends StatelessWidget {
  const _Hairline();

  @override
  Widget build(BuildContext context) => Container(
        height: 0.5,
        color: CupertinoColors.separator.resolveFrom(context),
      );
}
