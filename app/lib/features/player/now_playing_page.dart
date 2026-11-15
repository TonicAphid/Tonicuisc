import 'dart:async';

import 'package:flutter/gestures.dart' show PointerDeviceKind;
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

  /// 插值最多补这么多：再长就当位置流卡住了，宁可不补也不让歌词跑前面。
  static const Duration _maxExtrapolate = Duration(milliseconds: 350);

  /// 歌词排版参数。**测量（[_extentAt]）和渲染必须用同一套尺寸**，否则算出来的
  /// 行高和真实高度差一截，滚动位置会一点点跑偏。
  static const double _mainSize = 17; // 统一按「当前行」的字号算高，切行时不会跳
  static const double _mainHeight = 1.2;
  static const double _subSize = 12;
  static const double _subHeight = _subSize * _mainHeight;
  static const double _subGap = 2;
  static const double _lineGap = 10; // 行与行之间的留白
  static const double _itemVPad = 1;
  static const double _listPad = 4; // 列表上下留白（定位时要算进去）

  /// 用户自己滚过歌词后，这段时间内先别自动抢滚动。
  static const Duration _resumeDelay = Duration(seconds: 4);

  /// 行高缓存（index → 外层高度）；宽度一变整张作废。
  final Map<int, double> _extents = {};
  double _extentWidth = 0;

  /// 用户正按着歌词拖动；松手后靠 [_userTouchedAt] 再压 [_resumeDelay]。
  bool _userDragging = false;
  DateTime? _userTouchedAt;
  double? _touchStartOffset;

  /// 卡拉OK 逐字上色只让「当前行」自己重绘（以前是整页 30fps setState，
  /// 拖动歌词时整棵树跟着重建，手感又卡又硬）。
  final ValueNotifier<int> _karaoke = ValueNotifier<int>(0);

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
    _karaoke.dispose();
    _scroll.dispose();
    super.dispose();
  }

  /// 两次 position 上报之间按真实时间插值，让进度条 30fps 连续走。
  ///
  /// **插值有上限**（[_maxExtrapolate]）：position 流一旦停更（缓冲、卡顿、
  /// 后端不再报位置），无上限地加时间会让逐字歌词一路跑到人声前面去——
  /// 这就是「歌词比歌快」的来源。网页版那边早就按 0.3s 封顶了，这里是漏的。
  Duration get _displayPosition {
    final raw = widget.controller.position;
    if (!widget.controller.playing || raw == Duration.zero) return raw;
    final elapsed = DateTime.now().difference(_lastRawAt);
    final value = raw + (elapsed > _maxExtrapolate ? _maxExtrapolate : elapsed);
    final total = widget.controller.duration;
    if (total != null && value > total) return total;
    return value;
  }

  /// 30fps 只让当前行重绘逐字进度，**不再整页 setState**。
  void _onTick(Duration _) {
    if (!mounted || !widget.controller.playing || !_sheet.hasWords) return;
    final now = DateTime.now().millisecondsSinceEpoch;
    if (now - _lastPaintMs < 33) return;
    _lastPaintMs = now;
    _karaoke.value++;
  }

  /// 第 [index] 行实际要占多高：按内容量出来，而不是全页一刀切。
  ///
  /// 以前只有 40 / 46 / 72 三档，只要整首歌里有一行带拼音，**所有**行都按 72 排，
  /// 于是「一行原文 + 一行拼音」中间能空出 30 多像素。现在逐行量：
  /// 没译文没拼音的行就一个行高，有的才往上加。
  double _extentAt(int index, double textWidth) {
    if (textWidth != _extentWidth) {
      // 窗口宽度变了（或第一次进来）：缓存整张作废，否则行高是按旧宽度折行的
      _extents.clear();
      _extentWidth = textWidth;
    }
    final cached = _extents[index];
    if (cached != null) return cached;
    final line = _sheet.lines[index];
    // 宽度可能是 0（还没布局 / 窗口被压扁）：给个下限，免得 TextPainter 算出怪值
    final width = textWidth < 40 ? 40.0 : textWidth;
    final painter = TextPainter(
      text: TextSpan(
        text: line.text.isEmpty ? '♪' : line.text,
        // 用当前行的字号/字重量：只会算得比非当前行**更大**，不会算小导致裁字
        style: const TextStyle(
          fontSize: _mainSize,
          fontWeight: FontWeight.w600,
          height: _mainHeight,
        ),
      ),
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.center,
      maxLines: 2,
      ellipsis: '…',
    )..layout(maxWidth: width);
    var height = painter.height + _itemVPad * 2 + _lineGap;
    painter.dispose();
    if (line.translation.isNotEmpty) height += _subGap + _subHeight;
    if (line.roman.isNotEmpty) height += _subGap + _subHeight;
    return _extents[index] = height;
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
      _extents.clear();
      _extentWidth = 0;
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
      // 还在装载这一首（音频没起来）时不要拿 position 定位：那会儿它可能是
      // 上一首的残值，歌曲一换、歌词先到，就会出现「继承了上一首的进度」。
      _active = widget.controller.loading ? -1 : sheet.indexOf(widget.controller.position);
      _loading = false;
      _error = null;
      // 换了一份歌词：行高缓存作废（行数、内容都变了）
      _extents.clear();
      _extentWidth = 0;
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

  /// 用户刚摸过歌词（正在拖 / 松手不满 [_resumeDelay]）：这段时间别抢滚动。
  bool get _userInterfering {
    if (_userDragging) return true;
    final at = _userTouchedAt;
    return at != null && DateTime.now().difference(at) < _resumeDelay;
  }

  void _autoScroll(int index, {bool jump = false, int attempt = 0}) {
    if (!_sheet.synced || index < 0 || index >= _sheet.lines.length) return;
    // 用户自己在看某一段：不许把画面拽回去。等他自己停手 [_resumeDelay] 之后，
    // 下一句切行时才悄悄跟上（以前是无条件 animateTo，一拖就被抢，很硬）。
    if (!jump && _userInterfering) return;
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
      final width = _extentWidth > 0 ? _extentWidth : MediaQuery.sizeOf(context).width - 48;
      // 行高是逐行量的，目标位置就得把前面每一行加起来（不能 index * 定值）
      var above = 0.0;
      for (var i = 0; i < index; i++) {
        above += _extentAt(i, width);
      }
      final target = (_listPad + above + _extentAt(index, width) / 2 - viewport / 2)
          .clamp(0.0, _scroll.position.maxScrollExtent)
          .toDouble();
      if (jump) {
        // 刚打开播放页：直接落到当前行，不要从第一行一路滚下来
        _scroll.jumpTo(target);
      } else {
        // 曲线比默认的 easeOut 收得更慢，落位时不至于「一顿就停」
        _scroll.animateTo(
          target,
          duration: const Duration(milliseconds: 380),
          curve: Curves.easeOutCubic,
        );
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

    // 桌面端的 ScrollBehavior 会自动在最右边挂一条滚动条——这里关掉，但**不影响**
    // 滚轮和拖动。顺手把鼠标也加进 dragDevices：Windows 上默认鼠标是拖不动列表的，
    // 只能滚轮，想按住往回翻做不到，手感就很「死」。
    final behavior = ScrollConfiguration.of(context).copyWith(
      scrollbars: false,
      dragDevices: {
        PointerDeviceKind.touch,
        PointerDeviceKind.mouse,
        PointerDeviceKind.trackpad,
        PointerDeviceKind.stylus,
        PointerDeviceKind.invertedStylus,
      },
    );

    return ScrollConfiguration(
      behavior: behavior,
      child: Listener(
        // 用户一动手就记下来：拖动期间不自动滚，松手后再压 [_resumeDelay]。
        // 用指针事件而不是 ScrollNotification：后者分不清「用户滚」和「我们自己
        // animateTo」，会被自己的动画触发自我封锁。
        onPointerDown: (_) {
          _touchStartOffset = _scroll.hasClients ? _scroll.offset : null;
          _userDragging = true;
          _userTouchedAt = DateTime.now();
        },
        onPointerUp: (_) => _releaseDrag(),
        onPointerCancel: (_) => _releaseDrag(),
        onPointerSignal: (_) {
          // 滚轮 / 触控板滚动：没有按下-抬起，只更新时间戳
          _userTouchedAt = DateTime.now();
        },
        child: ListView.builder(
          controller: _scroll,
          // 逐行量高度（见 `_extentAt`）：带译文/拼音的行才高一点，光一行原文就矮一点。
          // 以前是一刀切 40/46/72，只要歌里有一行带拼音，整页都按 72 排，行距大得离谱。
          itemExtentBuilder: (index, dims) => _extentAt(index, dims.crossAxisExtent - 48),
          // 桌面默认 ClampingScrollPhysics：到边硬停、没惯性，拖起来很「脆」。
          // 换成带阻尼的回弹，跟 iOS 手感一致。
          physics: const BouncingScrollPhysics(parent: AlwaysScrollableScrollPhysics()),
          padding: const EdgeInsets.symmetric(vertical: _listPad),
          itemCount: _sheet.lines.length,
          itemBuilder: (context, index) {
            final line = _sheet.lines[index];
            final active = index == _active;
            return GestureDetector(
              // 整行（含上下留白）都能点，不然只有字上点得到，跳转很难点
              behavior: HitTestBehavior.opaque,
              onTap: !_sheet.synced
                  ? null
                  : () {
                      // 点行 = 「我要跳到这一句」：先解除压制，让画面立刻对齐过去
                      _userDragging = false;
                      _userTouchedAt = null;
                      widget.controller.seek(line.time);
                    },
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: _itemVPad),
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
        ),
      ),
    );
  }

  /// 松手 / 取消：结束拖动。**只有真的滚过才压 [_resumeDelay]**——
  /// 单纯点一下某一行（跳转）不该让自动跟随停 4 秒。
  void _releaseDrag() {
    _userDragging = false;
    final start = _touchStartOffset;
    final moved = start != null && _scroll.hasClients && (_scroll.offset - start).abs() > 0.5;
    _userTouchedAt = moved ? DateTime.now() : null;
    _touchStartOffset = null;
  }

  /// 原文：有逐字时间轴时，按**每个字/词**自己的时间上色。
  ///
  /// 以前是整行一个 ShaderMask 横扫；长句一旦折成两行，那条渐变是横跨整个文本框的，
  /// 第二行会从左边重新填充，看起来就像「一句歌词变成两段」。现在每个词各画各的，
  /// 折行也不影响。
  Widget _lyricMain(BuildContext context, LyricLine line, bool active) {
    final scheme = Theme.of(context).colorScheme;
    final text = line.text.isEmpty ? '♪' : line.text;
    final style = TextStyle(
      fontSize: active ? _mainSize : _mainSize - 2,
      // 行高写死成和 [_extentAt] 里测量时一致，否则量出来的高度和实际排版对不上
      height: _mainHeight,
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

    // 只有「当前行」需要跟着 30fps 重绘逐字进度：用 ValueListenable 把这层
    // 单独拎出来，父级 ListView 和别的行都不重建。
    return ValueListenableBuilder<int>(
      valueListenable: _karaoke,
      builder: (context, _, __) => Text.rich(
        TextSpan(children: _karaokeSpans(line, scheme, style)),
        textAlign: TextAlign.center,
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
    );
  }

  /// 把一行拆成「已唱 / 正在唱 / 还没唱」三种 span。
  ///
  /// 正在唱的那个词用 WidgetSpan + ShaderMask 单独扫，既保留了连续填充的观感，
  /// 又不会因为折行把整行的进度画歪。
  List<InlineSpan> _karaokeSpans(LyricLine line, ColorScheme scheme, TextStyle style) {
    final position = _displayPosition;
    final spans = <InlineSpan>[];
    for (final word in line.words) {
      if (word.text.isEmpty) continue;
      final progress = word.progressAt(position);
      if (progress >= 1) {
        spans.add(TextSpan(text: word.text, style: style.copyWith(color: scheme.primary)));
      } else if (progress <= 0) {
        spans.add(TextSpan(text: word.text, style: style.copyWith(color: scheme.onSurfaceVariant)));
      } else {
        spans.add(
          WidgetSpan(
            alignment: PlaceholderAlignment.baseline,
            baseline: TextBaseline.alphabetic,
            child: ShaderMask(
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
              child: Text(word.text, style: style.copyWith(color: Colors.white)),
            ),
          ),
        );
      }
    }
    return spans;
  }

  Widget _lyricSub(BuildContext context, String text, Color color) => Padding(
        padding: const EdgeInsets.only(top: _subGap),
        child: Text(
          text,
          textAlign: TextAlign.center,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          // 同样写死行高：`_extentAt` 按 `_subSize * _mainHeight` 加高
          style: TextStyle(fontSize: _subSize, height: _mainHeight, color: color),
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
