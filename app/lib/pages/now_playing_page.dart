import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/lyric.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../state/cover_cache.dart';
import '../state/library_state.dart';
import '../widgets/play_mode_icons.dart';
import 'artist_page.dart';
import 'queue_page.dart';

/// 全屏播放页：封面 + 歌词（跟随进度高亮、可点击跳转）+ 进度条 + 播放控制。
class NowPlayingPage extends StatefulWidget {
  const NowPlayingPage({
    super.key,
    required this.controller,
    required this.api,
    required this.library,
    this.onUnauthorized,
  });

  final PlayerController controller;
  final ApiClient api;
  final LibraryState library;

  /// 401 时交给外壳处理（清凭据回登录页）；不给就只弹个提示。
  final Future<void> Function(String message)? onUnauthorized;

  static Future<void> open(
    BuildContext context,
    PlayerController controller,
    ApiClient api,
    LibraryState library, {
    Future<void> Function(String message)? onUnauthorized,
  }) {
    return Navigator.of(context).push(
      MaterialPageRoute(
        fullscreenDialog: true,
        builder: (_) => NowPlayingPage(
          controller: controller,
          api: api,
          library: library,
          onUnauthorized: onUnauthorized,
        ),
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

  /// 手指拖进度条时的位置（毫秒）；不为 null 时进度条不跟播放进度。
  double? _dragMilliseconds;

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
      // 换歌了就别把上一首的歌词/错误盖到这一首上
      if (!mounted || _loadedSongId != song.id) return;
      setState(() => _sheet = LyricSheet.parse(raw, title: song.name, artist: song.singers));
    } catch (err) {
      if (err is UnauthorizedException && widget.onUnauthorized != null) {
        if (mounted) await widget.onUnauthorized!('$err');
        return;
      }
      if (mounted && _loadedSongId == song.id) setState(() => _error = '歌词获取失败：$err');
    } finally {
      // stale 的那次不许动 _loading：不然会把新一轮「正在加载」提前收掉
      if (mounted && _loadedSongId == song.id) setState(() => _loading = false);
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
      if (err is UnauthorizedException && widget.onUnauthorized != null) {
        await widget.onUnauthorized!('$err');
        return;
      }
      if (mounted) messenger.showSnackBar(SnackBar(content: Text('操作失败：$err')));
    }
  }

  Widget _cover(Song song, BoxConstraints constraints) {
    final size = (constraints.maxWidth * 0.62).clamp(140.0, constraints.maxHeight * 0.34).toDouble();
    // 和列表共用同一个封面缓存：QQ 封面到了大图也会跟着换，里外一致
    return ValueListenableBuilder<int>(
      valueListenable: CoverCache.revision,
      builder: (context, _, __) {
        final cached = CoverCache.of(song.id);
        final url = (cached != null && cached.isNotEmpty) ? cached : song.coverUrl;
        return ClipRRect(
          borderRadius: BorderRadius.circular(18),
          child: SizedBox(
            width: size,
            height: size,
            child: url.isEmpty
                ? _coverFallback()
                : Image.network(
                    url,
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => _coverFallback(),
                    loadingBuilder: (context, child, progress) => progress == null ? child : _coverFallback(),
                  ),
          ),
        );
      },
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
          // 点歌手名进歌手主页
          InkWell(
            onTap: song.singers.isEmpty
                ? null
                : () => ArtistPage.open(
                      context,
                      name: song.singers,
                      api: widget.api,
                      player: widget.controller,
                      library: widget.library,
                    ),
            child: Text(
              song.singers,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: theme.textTheme.bodyMedium?.copyWith(
                color: theme.colorScheme.primary,
                decoration: TextDecoration.underline,
              ),
            ),
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
    // 拖动时进度条跟手指，不跟播放进度；音频继续放，松手才 seek
    final dragging = _dragMilliseconds;
    final shown = dragging != null
        ? Duration(milliseconds: dragging.round())
        : (total > Duration.zero && widget.controller.position > total ? total : widget.controller.position);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Column(
        children: [
          Slider(
            value: total.inMilliseconds == 0
                ? 0
                : (dragging ?? shown.inMilliseconds.toDouble()).clamp(0, total.inMilliseconds).toDouble(),
            max: total.inMilliseconds == 0 ? 1 : total.inMilliseconds.toDouble(),
            onChanged: total.inMilliseconds == 0
                ? null
                : (value) => setState(() => _dragMilliseconds = value),
            onChangeEnd: total.inMilliseconds == 0
                ? null
                : (value) async {
                    await widget.controller.seek(Duration(milliseconds: value.round()));
                    if (mounted) setState(() => _dragMilliseconds = null);
                  },
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8),
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Text(
                  formatDuration(shown),
                  style: theme.textTheme.labelSmall?.copyWith(
                    color: dragging != null ? theme.colorScheme.primary : null,
                  ),
                ),
                Text(formatDuration(widget.controller.duration), style: theme.textTheme.labelSmall),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _controls(BuildContext context) {
    final controller = widget.controller;
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        IconButton(
          iconSize: 34,
          tooltip: '播放模式：${controller.mode.label}',
          icon: Icon(playModeIcon(controller.mode)),
          onPressed: controller.cycleMode,
        ),
        const SizedBox(width: 8),
        IconButton(
          iconSize: 40,
          tooltip: '上一首',
          icon: const Icon(Icons.skip_previous),
          onPressed: controller.previous,
        ),
        const SizedBox(width: 4),
        if (controller.loading)
          const SizedBox(width: 64, height: 64, child: Center(child: CircularProgressIndicator()))
        else
          IconButton(
            iconSize: 64,
            tooltip: controller.playing ? '暂停' : '播放',
            icon: Icon(controller.playing ? Icons.pause_circle_filled : Icons.play_circle_fill),
            onPressed: controller.toggle,
          ),
        const SizedBox(width: 4),
        IconButton(
          iconSize: 40,
          tooltip: '下一首',
          icon: const Icon(Icons.skip_next),
          onPressed: controller.next,
        ),
        const SizedBox(width: 8),
        IconButton(
          iconSize: 34,
          tooltip: '播放列表',
          icon: const Icon(Icons.queue_music),
          onPressed: () => QueueSheet.show(context, controller),
        ),
      ],
    );
  }
}
