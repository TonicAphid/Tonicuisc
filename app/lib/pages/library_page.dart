import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../state/library_state.dart';

/// 「列表」tab：喜欢 / 收藏 / 播放历史 三个入口。
class LibraryPage extends StatelessWidget {
  const LibraryPage({
    super.key,
    required this.api,
    required this.library,
    required this.player,
    required this.onUnauthorized,
  });

  final ApiClient api;
  final LibraryState library;
  final PlayerController player;
  final Future<void> Function(String message) onUnauthorized;

  static const Map<String, ({String title, String subtitle, IconData icon})> kinds = {
    'like': (title: '我喜欢', subtitle: '点过红心的歌', icon: Icons.favorite),
    'favorite': (title: '收藏', subtitle: '单独存的歌单', icon: Icons.bookmark),
    'history': (title: '播放历史', subtitle: '最近听过的歌', icon: Icons.history),
  };

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return AnimatedBuilder(
      animation: library,
      builder: (context, _) => ListView(
        padding: const EdgeInsets.all(16),
        children: [
          for (final entry in kinds.entries)
            Card(
              clipBehavior: Clip.antiAlias,
              child: ListTile(
                contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                leading: CircleAvatar(
                  backgroundColor: theme.colorScheme.primaryContainer,
                  child: Icon(entry.value.icon, color: theme.colorScheme.onPrimaryContainer),
                ),
                title: Text(entry.value.title, style: theme.textTheme.titleMedium),
                subtitle: Text('${entry.value.subtitle} · ${library.countOf(entry.key)} 首'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () async {
                  await Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => LibraryListPage(
                        kind: entry.key,
                        api: api,
                        library: library,
                        player: player,
                        onUnauthorized: onUnauthorized,
                      ),
                    ),
                  );
                  await library.refresh();
                },
              ),
            ),
          const SizedBox(height: 8),
          TextButton.icon(
            onPressed: library.refresh,
            icon: const Icon(Icons.refresh),
            label: const Text('刷新'),
          ),
        ],
      ),
    );
  }
}

/// 单个列表的歌曲页。
class LibraryListPage extends StatefulWidget {
  const LibraryListPage({
    super.key,
    required this.kind,
    required this.api,
    required this.library,
    required this.player,
    required this.onUnauthorized,
  });

  final String kind;
  final ApiClient api;
  final LibraryState library;
  final PlayerController player;
  final Future<void> Function(String message) onUnauthorized;

  @override
  State<LibraryListPage> createState() => _LibraryListPageState();
}

class _LibraryListPageState extends State<LibraryListPage> {
  List<Song> _songs = const [];
  bool _loading = true;
  String? _error;

  String get _title => LibraryPage.kinds[widget.kind]?.title ?? widget.kind;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final songs = await widget.api.libraryList(widget.kind);
      if (!mounted) return;
      setState(() => _songs = songs);
    } catch (err) {
      if (!mounted) return;
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      setState(() => _error = '$err');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _remove(Song song) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.library.removeFrom(widget.kind, song.id);
      if (!mounted) return;
      setState(() => _songs = _songs.where((item) => item.id != song.id).toList());
      messenger.showSnackBar(SnackBar(content: Text('已从「$_title」移除 ${song.name}')));
    } catch (err) {
      messenger.showSnackBar(SnackBar(content: Text('移除失败：$err')));
    }
  }

  Future<void> _clear() async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('清空「$_title」？'),
        content: const Text('这个操作不可撤销。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('清空')),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await widget.library.clear(widget.kind);
      if (!mounted) return;
      setState(() => _songs = const []);
    } catch (err) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('清空失败：$err')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('$_title（${_songs.length}）'),
        actions: [
          IconButton(tooltip: '刷新', onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
          IconButton(tooltip: '清空', onPressed: _songs.isEmpty ? null : _clear, icon: const Icon(Icons.delete_sweep_outlined)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(child: Text(_error!))
              : _songs.isEmpty
                  ? Center(child: Text('这里还是空的', style: Theme.of(context).textTheme.bodyMedium))
                  : ListView.separated(
                      itemCount: _songs.length,
                      separatorBuilder: (_, __) => const Divider(height: 1),
                      itemBuilder: (context, index) {
                        final song = _songs[index];
                        final isCurrent = widget.player.current?.id == song.id;
                        return ListTile(
                          leading: CircleAvatar(
                            child: Text(song.sourceLabel.isEmpty ? '?' : song.sourceLabel.substring(0, 1)),
                          ),
                          title: Text(song.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                          subtitle: Text(song.subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
                          trailing: IconButton(
                            tooltip: '移除',
                            icon: const Icon(Icons.remove_circle_outline),
                            onPressed: () => _remove(song),
                          ),
                          selected: isCurrent,
                          onTap: () => widget.player.play(song, widget.api),
                        );
                      },
                    ),
    );
  }
}
