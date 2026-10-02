import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../state/library_state.dart';
import '../widgets/song_avatar.dart';

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
  final TextEditingController _keyword = TextEditingController();
  final Set<String> _sources = <String>{'migu', 'kuwo'};

  List<Song> _results = const [];
  bool _searching = false;
  String? _error;
  double? _elapsed;
  double? _serverElapsed;

  @override
  void dispose() {
    _keyword.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final keyword = _keyword.text.trim();
    if (keyword.isEmpty || _searching) return;
    setState(() {
      _searching = true;
      _error = null;
      _elapsed = null;
      _serverElapsed = null;
    });
    final stopwatch = Stopwatch()..start();
    try {
      final result = await widget.api.search(keyword, sources: _sources.toList());
      stopwatch.stop();
      if (!mounted) return;
      setState(() {
        _results = result.items;
        _elapsed = stopwatch.elapsedMilliseconds / 1000;
        _serverElapsed = result.elapsed;
      });
    } catch (err) {
      stopwatch.stop();
      if (!mounted) return;
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      setState(() {
        _results = const [];
        _error = '$err';
      });
    } finally {
      if (mounted) setState(() => _searching = false);
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
      messenger.showSnackBar(SnackBar(content: Text('操作失败：$err')));
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
                  itemCount: _results.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    final song = _results[index];
                    final isCurrent = widget.player.current?.id == song.id;
                    return ListTile(
                      leading: SongAvatar(song: song, highlight: isCurrent),
                      title: Text(song.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                      subtitle: Text(song.subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
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
}
