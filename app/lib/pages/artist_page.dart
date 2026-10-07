import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../state/cover_cache.dart';
import '../state/library_state.dart';
import '../widgets/song_avatar.dart';

/// 歌手主页：这个歌手在音源上能搜到的所有歌。
class ArtistPage extends StatefulWidget {
  const ArtistPage({
    super.key,
    required this.name,
    required this.api,
    required this.player,
    required this.library,
    this.sources = const [],
    this.onUnauthorized,
  });

  final String name;
  final ApiClient api;
  final PlayerController player;
  final LibraryState library;
  final List<String> sources;
  final Future<void> Function(String message)? onUnauthorized;

  static Future<void> open(
    BuildContext context, {
    required String name,
    required ApiClient api,
    required PlayerController player,
    required LibraryState library,
    List<String> sources = const [],
    Future<void> Function(String message)? onUnauthorized,
  }) {
    return Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => ArtistPage(
          name: name,
          api: api,
          player: player,
          library: library,
          sources: sources,
          onUnauthorized: onUnauthorized,
        ),
      ),
    );
  }

  @override
  State<ArtistPage> createState() => _ArtistPageState();
}

class _ArtistPageState extends State<ArtistPage> {
  List<Song> _songs = const [];
  bool _loading = true;
  String? _error;

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
      final songs = await widget.api.artist(widget.name, sources: widget.sources, limit: 100);
      if (!mounted) return;
      setState(() => _songs = songs);
      unawaited(CoverCache.resolve(widget.api, songs));
    } catch (err) {
      if (!mounted) return;
      if (err is UnauthorizedException && widget.onUnauthorized != null) {
        await widget.onUnauthorized!('$err');
        return;
      }
      setState(() => _error = friendlyError(err, widget.api.baseUrl));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _toggleLike(Song song) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.library.toggle('like', song.id);
    } catch (err) {
      if (err is UnauthorizedException && widget.onUnauthorized != null) {
        await widget.onUnauthorized!('$err');
        return;
      }
      if (mounted) messenger.showSnackBar(SnackBar(content: Text('操作失败：$err')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.name),
        actions: [
          IconButton(tooltip: '刷新', onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(_error!, textAlign: TextAlign.center),
                        const SizedBox(height: 16),
                        FilledButton.icon(onPressed: _load, icon: const Icon(Icons.refresh), label: const Text('重试')),
                      ],
                    ),
                  ),
                )
              : _songs.isEmpty
                  ? Center(child: Text('没有搜到 ${widget.name} 的歌', style: theme.textTheme.bodyMedium))
                  : Column(
                      children: [
                        Padding(
                          padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                          child: Row(
                            children: [
                              Icon(Icons.person_outline, size: 18, color: theme.colorScheme.primary),
                              const SizedBox(width: 8),
                              Expanded(
                                child: Text('共 ${_songs.length} 首', style: theme.textTheme.bodySmall),
                              ),
                              TextButton.icon(
                                onPressed: () => widget.player.playQueue(_songs, 0, widget.api),
                                icon: const Icon(Icons.play_arrow),
                                label: const Text('全部播放'),
                              ),
                            ],
                          ),
                        ),
                        const Divider(height: 1),
                        Expanded(
                          child: AnimatedBuilder(
                            animation: widget.library,
                            builder: (context, _) => ListView.separated(
                              itemCount: _songs.length,
                              separatorBuilder: (_, __) => const Divider(height: 1),
                              itemBuilder: (context, index) {
                                final song = _songs[index];
                                final isCurrent = widget.player.current?.id == song.id;
                                return ListTile(
                                  leading: SongAvatar(song: song, highlight: isCurrent),
                                  title: Text(song.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                                  subtitle: Text(song.subtitleTail, maxLines: 1, overflow: TextOverflow.ellipsis),
                                  trailing: IconButton(
                                    tooltip: widget.library.liked(song.id) ? '取消喜欢' : '喜欢',
                                    icon: Icon(
                                      widget.library.liked(song.id) ? Icons.favorite : Icons.favorite_border,
                                      color: widget.library.liked(song.id) ? Colors.redAccent : null,
                                    ),
                                    onPressed: () => _toggleLike(song),
                                  ),
                                  selected: isCurrent,
                                  onTap: () => widget.player.playQueue(_songs, index, widget.api),
                                );
                              },
                            ),
                          ),
                        ),
                      ],
                    ),
    );
  }
}
