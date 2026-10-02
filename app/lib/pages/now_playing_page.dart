import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/lyric.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../state/library_state.dart';

/// 全屏播放页：封面 + 歌词（跟随进度高亮、可点击跳转）+ 进度条 + 播放控制。
class NowPlayingPage extends StatefulWidget {
  const NowPlayingPage({
    super.key,
    required this.controller,
    required this.api,
    required this.library,
  });

  final PlayerController controller;
  final ApiClient api;
  final LibraryState library;

  static Future<void> open(
    BuildContext context,
    PlayerController controller,
    ApiClient api,
    LibraryState library,
  ) {
    return Navigator.of(context).push(
      MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => NowPlayingPage(controller: controller, api: api, library: library),
      ),
    );
  }

  @override
  State<NowPlayingPage> createState() => _NowPlayingPageState();
}

class _NowPlayingPageState extends State<NowPlayingPage> {
  static const double _lineExtent = 46;

  final ScrollController _scroll = ScrollController();
  LyricSheet _sheet = LyricSheet.empty;
  String? _loadedSongId;
  int _active = -1;
  bool _loading = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_handlePlayer);
    _loadLyric();
  }

  @override
  void dispose() {
    widget.controller.removeListener(_handlePlayer);
    _scroll.dispose();
    super.dispose();
  }

  void _handlePlayer() {
    final song = widget.controller.current;
    if (song == null) return;
    if (song.id != _loadedSongId) {
      _loadLyric();
      return;
    }
    final index = _sheet.indexOf(widget.controller.position);
    if (index != _active) {
      setState(() => _active = index);
      _autoScroll(index);
    }
  }

  Future<void> _loadLyric() async {
    final song = widget.controller.current;
    if (song == null) return;
    _loadedSongId = song.id;
    setState(() {
      _loading = true;
      _error = null;
      _sheet = LyricSheet.empty;
      _active = -1;
    });
    try {
      final raw = await widget.api.lyric(song.id);
      if (!mounted || _loadedSongId != song.id) return;
      setState(() => _sheet = LyricSheet.parse(raw));
    } catch (err) {
      if (mounted) setState(() => _error = '歌词获取失败：$err');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  void _autoScroll(int index) {
    if (!_sheet.synced || index < 0 || !_scroll.hasClients) return;
    final viewport = _scroll.position.viewportDimension;
    final target = (index * _lineExtent + _lineExtent / 2 - viewport / 2)
        .clamp(0.0, _scroll.position.maxScrollExtent)
        .toDouble();
    _scroll.animateTo(target, duration: const Duration(milliseconds: 300), curve: Curves.easeOut);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: Container(
        decoration: BoxDecoration(
          gradient: LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [scheme.surfaceContainerHighest, scheme.surface],
          ),
        ),
        child: SafeArea(
          child: GestureDetector(
            onVerticalDragEnd: (details) {
              if ((details.primaryVelocity ?? 0) > 250) Navigator.of(context).maybePop();
            },
            child: AnimatedBuilder(
              animation: widget.controller,
              builder: (context, _) {
                final song = widget.controller.current;
                if (song == null) {
                  return const Center(child: Text('没有正在播放的歌曲'));
                }
                return LayoutBuilder(
                  builder: (context, constraints) => Column(
                    children: [
                      _header(context, song),
                      _cover(song, constraints),
                      const SizedBox(height: 12),
                      _titles(song),
                      const SizedBox(height: 8),
                      Expanded(child: _lyrics(context)),
                      _progress(context),
                      _controls(context),
                      const SizedBox(height: 12),
                    ],
                  ),
                );
              },
            ),
          ),
        ),
      ),
    );
  }

  Widget _header(BuildContext context, Song song) {
    return AnimatedBuilder(
      animation: widget.library,
      builder: (context, _) => Row(
        children: [
          IconButton(
            tooltip: '收起',
            icon: const Icon(Icons.keyboard_arrow_down),
            onPressed: () => Navigator.of(context).maybePop(),
          ),
          Expanded(
            child: Text(
              song.sourceLabel,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.labelMedium,
            ),
          ),
          IconButton(
            tooltip: widget.library.liked(song.id) ? '取消喜欢' : '喜欢',
            icon: Icon(
              widget.library.liked(song.id) ? Icons.favorite : Icons.favorite_border,
              color: widget.library.liked(song.id) ? Colors.redAccent : null,
            ),
            onPressed: () => _toggle('like', song),
          ),
          IconButton(
            tooltip: widget.library.favorited(song.id) ? '取消收藏' : '收藏',
            icon: Icon(widget.library.favorited(song.id) ? Icons.bookmark : Icons.bookmark_border),
            onPressed: () => _toggle('favorite', song),
          ),
        ],
      ),
    );
  }

  Future<void> _toggle(String kind, Song song) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.library.toggle(kind, song.id);
    } catch (err) {
      messenger.showSnackBar(SnackBar(content: Text('操作失败：$err')));
    }
  }

  Widget _cover(Song song, BoxConstraints constraints) {
    final size = (constraints.maxWidth * 0.62).clamp(140.0, constraints.maxHeight * 0.34).toDouble();
    return ClipRRect(
      borderRadius: BorderRadius.circular(18),
      child: SizedBox(
        width: size,
        height: size,
        child: song.coverUrl.isEmpty
            ? _coverFallback()
            : Image.network(
                song.coverUrl,
                fit: BoxFit.cover,
                errorBuilder: (_, __, ___) => _coverFallback(),
                loadingBuilder: (context, child, progress) => progress == null ? child : _coverFallback(),
              ),
      ),
    );
  }

  Widget _coverFallback() {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      color: scheme.surfaceContainerHighest,
      alignment: Alignment.center,
      child: Icon(Icons.music_note, size: 64, color: scheme.onSurfaceVariant),
    );
  }

  Widget _titles(Song song) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 24),
      child: Column(
        children: [
          Text(
            song.name,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            style: theme.textTheme.titleLarge,
          ),
          Text(
            song.singers,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            textAlign: TextAlign.center,
            style: theme.textTheme.bodyMedium,
          ),
        ],
      ),
    );
  }

  Widget _lyrics(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (_loading) return const Center(child: CircularProgressIndicator());
    if (_error != null) return Center(child: Text(_error!));
    if (_sheet.lines.isEmpty) return const Center(child: Text('暂无歌词'));

    return ListView.builder(
      controller: _scroll,
      itemExtent: _lineExtent,
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: _sheet.lines.length,
      itemBuilder: (context, index) {
        final line = _sheet.lines[index];
        final active = index == _active;
        return GestureDetector(
          onTap: _sheet.synced ? () => widget.controller.seek(line.time) : null,
          child: Center(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: Text(
                line.text.isEmpty ? '♪' : line.text,
                textAlign: TextAlign.center,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: active ? 17 : 15,
                  fontWeight: active ? FontWeight.w600 : FontWeight.normal,
                  color: active ? scheme.primary : scheme.onSurfaceVariant,
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _progress(BuildContext context) {
    final theme = Theme.of(context);
    final total = widget.controller.duration ?? Duration.zero;
    final position = total > Duration.zero && widget.controller.position > total
        ? total
        : widget.controller.position;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        children: [
          Slider(
            value: total.inMilliseconds == 0
                ? 0
                : position.inMilliseconds.clamp(0, total.inMilliseconds).toDouble(),
            max: total.inMilliseconds == 0 ? 1 : total.inMilliseconds.toDouble(),
            onChanged: total.inMilliseconds == 0
                ? null
                : (value) => widget.controller.seek(Duration(milliseconds: value.round())),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(formatDuration(position), style: theme.textTheme.labelSmall),
                Text(formatDuration(widget.controller.duration), style: theme.textTheme.labelSmall),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _controls(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        IconButton(
          iconSize: 34,
          tooltip: '后退 10 秒',
          icon: const Icon(Icons.replay_10),
          onPressed: () => widget.controller.seek(
            _forwardOf(-10),
          ),
        ),
        const SizedBox(width: 12),
        if (widget.controller.loading)
          const SizedBox(width: 56, height: 56, child: Center(child: CircularProgressIndicator()))
        else
          IconButton(
            iconSize: 64,
            tooltip: widget.controller.playing ? '暂停' : '播放',
            icon: Icon(widget.controller.playing ? Icons.pause_circle_filled : Icons.play_circle_fill),
            onPressed: widget.controller.toggle,
          ),
        const SizedBox(width: 12),
        IconButton(
          iconSize: 34,
          tooltip: '前进 10 秒',
          icon: const Icon(Icons.forward_10),
          onPressed: () => widget.controller.seek(_forwardOf(10)),
        ),
      ],
    );
  }

  /// 相对当前进度前后跳 [seconds] 秒，并夹在 0 与总时长之间。
  Duration _forwardOf(int seconds) {
    final target = widget.controller.position + Duration(seconds: seconds);
    if (target < Duration.zero) return Duration.zero;
    final total = widget.controller.duration;
    if (total != null && total > Duration.zero && target > total) return total;
    return target;
  }
}
