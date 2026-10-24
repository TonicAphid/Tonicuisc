import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/api_client.dart';
import '../../models/lyric.dart';
import '../../models/song.dart';
import 'player_controller.dart';
import '../../shared/cover_cache.dart';
import '../library/library_state.dart';
import 'play_mode_icons.dart';
import '../search/artist_page.dart';
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
  final ScrollController _scroll = ScrollController();
  LyricSheet _sheet = LyricSheet.empty;
  String? _loadedSongId;
  int _active = -1;
  bool _loading = false;
  String? _error;

  /// 增强歌词已经到了；迟到的逐行 LRC 不许把它覆盖回去。
  bool _enhancedReady = false;

  /// 卡拉OK着色时重绘的最低间隔（播放器的 position 流本身就够密了）。
  int _lastPaintMs = 0;

  /// 有逐字歌词时每行要显示原文 + 翻译 + 拼音，得给高一点。
  double get _lineExtent {
    if (!_sheet.hasWords) return 46;
    return _sheet.lines.any((line) => line.hasExtra) ? 84 : 64;
  }

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
    final lineChanged = index != _active;
    if (lineChanged) {
      setState(() => _active = index);
      _autoScroll(index);
    }
    if (_sheet.hasWords && !lineChanged) {
      // 逐字高亮要跟着 position 走；节流到 50ms，避免每个事件都整页重建。
      final now = DateTime.now().millisecondsSinceEpoch;
      if (now - _lastPaintMs >= 50) {
        _lastPaintMs = now;
        setState(() {});
      }
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
      _enhancedReady = false;
    });
    // 逐行 LRC 先出（音源自带、一般很快），逐字歌词在后台轮询，不挡页面。
    unawaited(_loadEnhanced(song.id));
    try {
      final raw = await widget.api.lyric(song.id);
      // 换歌了就别把上一首的歌词/错误盖到这一首上
      if (!mounted || _loadedSongId != song.id) return;
      if (_enhancedReady) return; // 增强歌词先到了，别用逐行版本覆盖
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

  /// 后台轮询增强歌词：`ready=false` 表示服务端还在匹配，隔两秒再问。
  ///
  /// 最多问 6 轮（约 10 秒），拿不到就保持逐行 LRC；换歌后旧轮询自动退出。
  Future<void> _loadEnhanced(String songId) async {
    for (var attempt = 0; attempt < 6; attempt++) {
      if (!mounted || _loadedSongId != songId) return;
      try {
        final data = await widget.api.lyricEnhanced(songId);
        if (!mounted || _loadedSongId != songId) return;
        if (data != null && data['ready'] == true) {
          final sheet = LyricSheet.parseEnhanced(data);
          if (sheet.lines.isEmpty) return;
          setState(() {
            _enhancedReady = true;
            _sheet = sheet;
            _active = sheet.indexOf(widget.controller.position);
            _error = null;
            _loading = false;
          });
          _autoScroll(_active);
          return;
        }
      } on UnauthorizedException {
        return; // 401 交给页面外层处理，别继续轮询
      } catch (_) {
        // 网络抖动：继续下一轮
      }
      await Future<void>.delayed(const Duration(seconds: 2));
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
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 2),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _lyricMain(context, line, active),
                  if (line.translation.isNotEmpty)
                    _lyricSub(context, line.translation, scheme.onSurfaceVariant.withValues(alpha: 0.85)),
                  if (line.roman.isNotEmpty)
                    _lyricSub(context, line.roman, scheme.primary.withValues(alpha: 0.8)),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  /// 原文：有逐字时间轴就按进度给每个字着色，否则整行普通显示。
  Widget _lyricMain(BuildContext context, LyricLine line, bool active) {
    final scheme = Theme.of(context).colorScheme;
    final style = TextStyle(
      fontSize: active ? 17 : 15,
      fontWeight: active ? FontWeight.w600 : FontWeight.normal,
      color: active ? scheme.primary : scheme.onSurfaceVariant,
    );
    if (!line.hasWords) {
      return Text(
        line.text.isEmpty ? '♪' : line.text,
        textAlign: TextAlign.center,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: style,
      );
    }
    final position = widget.controller.position;
    final spans = <TextSpan>[];
    for (final word in line.words) {
      final sung = active && position >= word.start;
      final current = active && position >= word.start && position < word.end;
      spans.add(
        TextSpan(
          text: word.text,
          style: TextStyle(
            color: sung ? scheme.primary : scheme.onSurfaceVariant,
            fontWeight: current ? FontWeight.w800 : (active ? FontWeight.w600 : FontWeight.normal),
          ),
        ),
      );
    }
    return Text.rich(
      TextSpan(style: style, children: spans),
      textAlign: TextAlign.center,
      maxLines: 2,
      overflow: TextOverflow.ellipsis,
    );
  }

  Widget _lyricSub(BuildContext context, String text, Color color) => Padding(
        padding: const EdgeInsets.only(top: 2),
        child: Text(
          text,
          textAlign: TextAlign.center,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(fontSize: 12, color: color),
        ),
      );

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
