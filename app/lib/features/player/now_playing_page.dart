import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/scheduler.dart';

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

class _NowPlayingPageState extends State<NowPlayingPage> with SingleTickerProviderStateMixin {
  final ScrollController _scroll = ScrollController();
  LyricSheet _sheet = LyricSheet.empty;
  String? _loadedSongId;
  int _active = -1;
  bool _loading = false;
  String? _error;

  /// 增强歌词已经到了；迟到的逐行 LRC 不许把它覆盖回去。
  bool _enhancedReady = false;

  /// 30fps 重绘歌词进度用的 Ticker（position 流本身只有 ~5Hz，直接用它画会一格一格跳）。
  late final Ticker _ticker;

  /// 卡拉OK重绘的最低间隔（毫秒）。
  int _lastPaintMs = 0;

  /// 播放器最后一次上报的 position 和时间点，用来在两次上报之间插值。
  Duration _lastRawPosition = Duration.zero;
  DateTime _lastRawAt = DateTime.now();
  bool _lastPlaying = false;

  /// 有逐字歌词时每行要显示原文 + 翻译 + 拼音，但别留太多空。
  double get _lineExtent {
    if (!_sheet.hasWords) return 40;
    return _sheet.lines.any((line) => line.hasExtra) ? 72 : 46;
  }

  /// 手指拖进度条时的位置（毫秒）；不为 null 时进度条不跟播放进度。
  double? _dragMilliseconds;

  @override
  void initState() {
    super.initState();
    _ticker = createTicker(_onTick)..start();
    widget.controller.addListener(_handlePlayer);
    _loadLyric();
  }

  @override
  void dispose() {
    _ticker.dispose();
    widget.controller.removeListener(_handlePlayer);
    _scroll.dispose();
    super.dispose();
  }

  /// 两次 position 上报之间按真实时间插值，让进度条 30fps 连续走。
  Duration get _displayPosition {
    final raw = widget.controller.position;
    if (!widget.controller.playing || raw == Duration.zero) return raw;
    final value = raw + DateTime.now().difference(_lastRawAt);
    final total = widget.controller.duration;
    if (total != null && value > total) return total;
    return value;
  }

  void _onTick(Duration _) {
    if (!mounted || !widget.controller.playing || !_sheet.hasWords) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    if (now - _lastPaintMs < 33) return;
    _lastPaintMs = now;
    setState(() {});
  }

  void _handlePlayer() {
    final song = widget.controller.current;
    if (song == null) return;
    if (song.id != _loadedSongId) {
      _loadLyric();
      return;
    }
    final raw = widget.controller.position;
    if (raw != _lastRawPosition) {
      _lastRawPosition = raw;
      _lastRawAt = DateTime.now();
    }
    final playing = widget.controller.playing;
    if (playing != _lastPlaying) {
      // 恢复播放时从当前真实位置重新起算，别把暂停的时长算进进度
      _lastPlaying = playing;
      _lastRawAt = DateTime.now();
    }
    final index = _sheet.indexOf(raw);
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
      _enhancedReady = false;
    });
    // 逐行 LRC 先出（音源自带、一般很快），逐字歌词在后台轮询，不挡页面。
    unawaited(_loadEnhanced(song.id));
    try {
      final raw = await widget.api.lyric(song.id);
      // 换歌了就别把上一首的歌词/错误盖到这一首上
      if (!mounted || _loadedSongId != song.id) return;
      if (_enhancedReady) return; // 增强歌词先到了，别用逐行版本覆盖
      _applySheet(LyricSheet.parse(raw, title: song.name, artist: song.singers));
    } catch (err) {
      if (err is UnauthorizedException && widget.onUnauthorized != null) {
        if (mounted) await widget.onUnauthorized!('$err');
        return;
      }
      if (mounted && _loadedSongId == song.id) setState(() => _error = '歌词获取失败：$err');
    } finally {
      // stale 的那次不许动 _loading：不然会把新一轮「正在加载」提前收掉
      if (mounted && _loadedSongId == song.id && _loading) setState(() => _loading = false);
    }
  }

  /// 歌词落地：同时把 loading 收掉、定位到当前行。
  ///
  /// 之前只 setState 不定位，列表要等下一句切行才会滚过去；而且那时 `_loading`
  /// 还没关、ListView 根本没建出来，`_autoScroll` 找不到 controller 直接 no-op。
  void _applySheet(LyricSheet sheet) {
    setState(() {
      _sheet = sheet;
      _active = sheet.indexOf(widget.controller.position);
      _loading = false;
      _error = null;
    });
    _autoScroll(_active, jump: true);
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
          setState(() => _enhancedReady = true);
          _applySheet(sheet);
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

  void _autoScroll(int index, {bool jump = false, int attempt = 0}) {
    if (!_sheet.synced || index < 0 || index >= _sheet.lines.length) return;
    // 列表可能在歌词刚 setState 的这一帧还没挂上 ScrollController；
    // 等这一帧画完再滚，必要时再补几帧。
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (!_scroll.hasClients) {
        if (attempt < 3) {
          Future<void>.delayed(const Duration(milliseconds: 50), () {
            if (mounted) _autoScroll(index, jump: jump, attempt: attempt + 1);
          });
        }
        return;
      }
      final viewport = _scroll.position.viewportDimension;
      final target = (index * _lineExtent + _lineExtent / 2 - viewport / 2)
          .clamp(0.0, _scroll.position.maxScrollExtent)
          .toDouble();
      if (jump) {
        // 刚打开播放页：直接落到当前行，不要从第一行一路滚下来
        _scroll.jumpTo(target);
      } else {
        _scroll.animateTo(target, duration: const Duration(milliseconds: 300), curve: Curves.easeOut);
      }
    });
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
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: _sheet.lines.length,
      itemBuilder: (context, index) {
        final line = _sheet.lines[index];
        final active = index == _active;
        return GestureDetector(
          onTap: _sheet.synced ? () => widget.controller.seek(line.time) : null,
          child: Center(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 1),
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

  /// 原文：有逐字时间轴时，用一条从左往右扫的硬边渐变模拟卡拉OK进度条。
  ///
  /// 不用「每个字单独变红」是因为那样是一格一格的；这里给整行做 ShaderMask，
  /// 填充边界跟着 position 连续移动，看起来才是进度条。
  Widget _lyricMain(BuildContext context, LyricLine line, bool active) {
    final scheme = Theme.of(context).colorScheme;
    final text = line.text.isEmpty ? '♪' : line.text;
    final style = TextStyle(
      fontSize: active ? 17 : 15,
      fontWeight: active ? FontWeight.w600 : FontWeight.normal,
      color: active ? scheme.primary : scheme.onSurfaceVariant,
    );
    if (!active || !line.hasWords) {
      return Text(
        text,
        textAlign: TextAlign.center,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: style,
      );
    }

    final progress = line.progressAt(_displayPosition).clamp(0.0, 1.0);
    if (progress <= 0) {
      return Text(
        text,
        textAlign: TextAlign.center,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: style.copyWith(color: scheme.onSurfaceVariant),
      );
    }
    if (progress >= 1) {
      return Text(
        text,
        textAlign: TextAlign.center,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: style.copyWith(color: scheme.primary),
      );
    }

    return ShaderMask(
      blendMode: BlendMode.srcIn,
      shaderCallback: (bounds) => LinearGradient(
        begin: Alignment.centerLeft,
        end: Alignment.centerRight,
        colors: [
          scheme.primary,
          scheme.primary,
          scheme.onSurfaceVariant,
          scheme.onSurfaceVariant,
        ],
        // 两个相同 stop 之间就是硬边界：左边已唱、右边未唱
        stops: [0.0, progress, progress, 1.0],
      ).createShader(bounds),
      child: Text(
        text,
        textAlign: TextAlign.center,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
        style: style.copyWith(color: Colors.white),
      ),
    );
  }

  Widget _lyricSub(BuildContext context, String text, Color color) => Padding(
        padding: const EdgeInsets.only(top: 1),
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
