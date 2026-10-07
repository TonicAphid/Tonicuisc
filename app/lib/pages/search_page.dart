import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../state/cover_cache.dart';
import '../state/library_state.dart';
import '../widgets/song_avatar.dart';
import 'artist_page.dart';

/// 搜索页（首页 tab）：搜索、试听、下载、点红心。
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
  final Set<String> _sources = <String>{'migu', 'kuwo'};
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
        sources: _sources.toList(),
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
        sources: _sources.toList(),
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
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text('正在下载 ${song.name} …')));
    try {
      final dir = await ApiClient.downloadDirectory();
      final path = await widget.api.downloadTo(song, dir);
      messenger.showSnackBar(SnackBar(content: Text('已保存到 $path')));
    } catch (err) {
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      messenger.showSnackBar(SnackBar(content: Text('下载失败：$err')));
    }
  }

  Future<void> _toggleLike(Song song) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final wasLiked = widget.library.liked(song.id);
      await widget.library.toggle('like', song.id);
      messenger.showSnackBar(SnackBar(
        content: Text(wasLiked ? '已取消喜欢 ${song.name}' : '已加入我喜欢 ${song.name}'),
        duration: const Duration(seconds: 1),
      ));
    } catch (err) {
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      if (mounted) messenger.showSnackBar(SnackBar(content: Text('操作失败：$err')));
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.library,
      builder: (context, _) => Column(
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _keyword,
                  textInputAction: TextInputAction.search,
                  onSubmitted: (_) => _search(),
                  decoration: const InputDecoration(
                    prefixIcon: Icon(Icons.search),
                    hintText: '搜索歌曲 / 歌手',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton(onPressed: _searching ? null : _search, child: const Text('搜索')),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12),
          child: Row(
            children: [
              const Text('音源：'),
              for (final entry in const {'migu': '咪咕', 'kuwo': '酷我'}.entries)
                FilterChip(
                  label: Text(entry.value),
                  selected: _sources.contains(entry.key),
                  onSelected: (selected) => setState(() {
                    selected ? _sources.add(entry.key) : _sources.remove(entry.key);
                  }),
                ),
            ],
          ),
        ),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 2, 16, 6),
          child: Row(
            children: [
              if (_searching) ...[
                const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                const SizedBox(width: 10),
                const Text('正在进行…'),
              ] else if (_elapsed != null)
                Expanded(
                  child: Text(
                    '搜索完成 · 用时 ${_elapsed!.toStringAsFixed(1)} 秒'
                    '${_serverElapsed == null ? '' : '（服务端 ${_serverElapsed!.toStringAsFixed(1)} 秒）'}'
                    ' · 共 ${_results.length} 首',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
            ],
          ),
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.all(16),
            child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ),
        Expanded(
          child: _results.isEmpty
              ? Center(
                  child: Text(
                    _searching ? '正在进行…' : '输入关键词开始搜索',
                    style: Theme.of(context).textTheme.bodyMedium,
                  ),
                )
              : ListView.separated(
                  controller: _scroll,
                  // 最后多一格：加载中 / 没有更多了
                  itemCount: _results.length + 1,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    if (index >= _results.length) return _footer(context);
                    final song = _results[index];
                    final isCurrent = widget.player.current?.id == song.id;
                    return ListTile(
                      leading: SongAvatar(song: song, highlight: isCurrent),
                      title: Text(song.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: Row(
                        children: [
                          // 点歌手名进歌手主页
                          Flexible(
                            child: InkWell(
                              onTap: song.singers.isEmpty
                                  ? null
                                  : () => ArtistPage.open(
                                        context,
                                        name: song.singers,
                                        api: widget.api,
                                        player: widget.player,
                                        library: widget.library,
                                        sources: _sources.toList(),
                                        onUnauthorized: widget.onUnauthorized,
                                      ),
                              child: Text(
                                song.singers,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: TextStyle(
                                  color: Theme.of(context).colorScheme.primary,
                                  decoration: TextDecoration.underline,
                                ),
                              ),
                            ),
                          ),
                          if (song.subtitleTail.isNotEmpty)
                            Flexible(
                              child: Text(
                                ' · ${song.subtitleTail}',
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: Theme.of(context).textTheme.bodySmall,
                              ),
                            ),
                        ],
                      ),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(song.ext.toUpperCase(), style: Theme.of(context).textTheme.labelSmall),
                          IconButton(
                            tooltip: widget.library.liked(song.id) ? '取消喜欢' : '喜欢',
                            icon: Icon(
                              widget.library.liked(song.id) ? Icons.favorite : Icons.favorite_border,
                              color: widget.library.liked(song.id) ? Colors.redAccent : null,
                            ),
                            onPressed: () => _toggleLike(song),
                          ),
                          IconButton(
                            tooltip: '下载',
                            icon: const Icon(Icons.download_outlined),
                            onPressed: () => _download(song),
                          ),
                        ],
                      ),
                      selected: isCurrent,
                      onTap: () => widget.player.playQueue(_results, index, widget.api),
                    );
                  },
                ),
          ),
        ],
      ),
    );
  }

  /// 列表底部：还有就自动加载，没有就说一声。
  Widget _footer(BuildContext context) {
    final theme = Theme.of(context);
    if (_loadingMore) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 20),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            SizedBox(width: 10),
            Text('正在加载更多…'),
          ],
        ),
      );
    }
    if (_hasMore) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Center(
          child: TextButton(
            onPressed: _loadMore,
            child: const Text('加载更多'),
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Center(
        child: Text('共 ${_results.length} 首', style: theme.textTheme.labelSmall),
      ),
    );
  }
}
