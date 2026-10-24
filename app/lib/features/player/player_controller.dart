import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';

import '../../core/api_client.dart';
import '../../core/debug_log.dart';
import '../../core/temp_dir.dart';
import '../../models/song.dart';
import '../../shared/cover_cache.dart';

/// 播放模式（图标在 widgets/play_mode_icons.dart 里映射）。
enum PlayMode {
  order('顺序播放'),
  loopAll('列表循环'),
  loopOne('单曲循环'),
  shuffle('随机播放');

  const PlayMode(this.label);

  final String label;
}

/// 播放器状态封装：播放队列、播放模式、上一个/下一个。
///
/// - iOS 走系统后端，配合 just_audio_background 支持锁屏 / 控制中心；
/// - Windows / Linux 走 media_kit 后端（见 main.dart 的初始化）；
/// - 点歌优先用音源直链，失败再回退服务端代理流。
class PlayerController extends ChangeNotifier {
  PlayerController() {
    // 这些订阅必须存下来：dispose 时要取消，否则播放器一关，
    // 流回调还可能在 ChangeNotifier 已经销毁之后调 notifyListeners()（debug 下直接断言崩溃）
    _subscriptions.addAll([
      _player.positionStream.listen((value) {
        // 真的走起来了：把「起播看门狗」撤掉（0 是刚 setAudioSource 后的正常值）
        if (value > Duration.zero) _watchdog?.cancel();
        _position = value;
        _notify();
      }),
      _player.durationStream.listen((value) {
        _duration = value;
        _notify();
      }),
      _player.playerStateStream.listen((value) {
        _playing = value.playing;
        // 卡死时这行日志是唯一能看出「播放器停在哪一步」的东西：
        // 一直 loading 说明 mpv 还没打开文件；ready 但位置不动说明解码/输出卡住。
        _log('state=${value.processingState.name} playing=${value.playing}');
        if (value.processingState == ProcessingState.completed) {
          _playing = false;
          unawaited(_handleCompleted());
        }
        _notify();
      }),
      _player.playbackEventStream.listen(
        (_) {},
        onError: (Object error, StackTrace stack) => unawaited(_handlePlaybackError(error)),
      ),
    ]);
  }

  final AudioPlayer _player = AudioPlayer();
  final Random _random = Random();
  final List<StreamSubscription<dynamic>> _subscriptions = <StreamSubscription<dynamic>>[];

  List<Song> _queue = const [];
  int _index = -1;
  PlayMode _mode = PlayMode.loopAll;
  ApiClient? _api;

  /// 点歌代次：每点一次 +1。上一次点歌还没加载完就被下一次接管时，
  /// 旧的那次必须直接退出，否则它会把过期的音频塞进播放器（播出来的和列表高亮不是同一首）。
  int _generation = 0;

  bool _disposed = false;
  Timer? _watchdog;

  /// 正在从「直连」切到「中转」：期间播放器抛的错别再重复处理一遍。
  bool _recovering = false;

  /// 装载超时（见 [_load]）：直连是 CDN，12 秒还没回应基本就是死了；
  /// 中转流要经过隧道 + 家里上行，给到 30 秒。
  static const Duration _directLoadBudget = Duration(seconds: 12);
  static const Duration _streamLoadBudget = Duration(seconds: 30);

  Duration _position = Duration.zero;
  Duration? _duration;
  bool _playing = false;
  bool _loading = false;
  String? _error;
  bool _usingDirect = false;
  Uri? _fallbackStream;

  /// 已经在后台触发过增强歌词搜索的歌曲（服务端有缓存，这里只防重复请求）。
  final Set<String> _lyricPrefetched = <String>{};

  /// 销毁之后不再通知（异步的点歌流程可能在页面已销毁后才跑完）。
  void _notify() {
    if (_disposed) return;
    notifyListeners();
  }

  // ------------------------------------------------------------------ 状态
  List<Song> get queue => List.unmodifiable(_queue);
  int get index => _index;
  PlayMode get mode => _mode;
  Song? get current => (_index >= 0 && _index < _queue.length) ? _queue[_index] : null;
  Duration get position => _position;
  Duration? get duration => _duration;
  bool get playing => _playing;
  bool get loading => _loading;
  String? get error => _error;
  bool get hasNext => _nextIndex(auto: true) != null || _mode == PlayMode.loopAll;

  // ------------------------------------------------------------------ 播放
  /// 播放整个列表，从 [startIndex] 开始。
  Future<void> playQueue(List<Song> songs, int startIndex, ApiClient api) async {
    _api = api;
    _queue = List<Song>.of(songs);
    if (_queue.isEmpty) return;
    _index = startIndex.clamp(0, _queue.length - 1);
    unawaited(CoverCache.resolve(api, _queue)); // 顺带把这批歌的封面问一下
    await _playCurrent();
  }

  /// 单曲播放（搜索结果里直接点一首）。
  Future<void> play(Song song, ApiClient api) => playQueue([song], 0, api);

  Future<void> _playCurrent() async {
    final song = current;
    final api = _api;
    if (song == null || api == null) return;
    // 一开始播放就触发歌词匹配，不要等用户打开播放页才去搜。
    unawaited(_prefetchLyric(api, song));
    final gen = ++_generation;
    _watchdog?.cancel();
    _recovering = false;
    _error = null;
    _duration = null;
    _position = Duration.zero;
    _loading = true;
    _notify();
    try {
      await _start(song, api);
    } catch (err) {
      if (gen != _generation) return; // 已经被下一次点歌接管，别把上一首的错误报到这一首上
      _error = err is TimeoutException
          ? '播放失败：音源${_usingDirect ? '直连' : '中转'}一直没回应，可能是链接失效或网络不通'
          : '播放失败：$err';
      _playing = false;
      _loading = false;
      _notify();
      return;
    }
    if (gen != _generation) return; // 加载期间用户又点了一首：让新的那次说了算，这里不许 resume
    _loading = false;
    _notify();
    unawaited(_reportPlay(api, song.id)); // 播放历史，失败不影响播放
    _resume();
    _armWatchdog(gen);
  }

  /// 提前让服务端开始匹配增强歌词；拿不到也没关系，播放页打开后会再问。
  Future<void> _prefetchLyric(ApiClient api, Song song) async {
    if (song.id.isEmpty || !_lyricPrefetched.add(song.id)) return;
    try {
      await api.lyricEnhanced(song.id);
    } catch (_) {
      _lyricPrefetched.remove(song.id);
    }
  }

  Future<void> _reportPlay(ApiClient api, String songId) async {
    try {
      await api.libraryAdd('history', songId);
    } catch (_) {
      // 忽略：历史记录失败不该影响播放
    }
  }

  Future<void> _start(Song song, ApiClient api) async {
    // Debug 版 media_kit 一定要能写 %TEMP%，否则**创建播放器就卡死**，
    // 连装载超时都等不到（卡的是 player 创建，不是装载）。提前失败，给句人话。
    if (kDebugMode && !TempDirCheck.writable) {
      throw StateError(
        '系统临时目录（%TEMP%）不可写，Debug 版播放器会卡死；'
        '修好目录权限，或改用 Release / 安装包版本',
      );
    }
    _fallbackStream = api.streamUri(song.id);
    // 封面和直链一起要：列表页早就在后台查封面了，这里最多再等 600ms。
    // just_audio_background 的 MediaItem 只在 setAudioSource 时取一次，先拿到 QQ 封面
    // 才不会让控制中心锁死酷我原图（之后 UI 换图也带不动它）。
    final coverReady = CoverCache.ensure(api, song);
    // 直连 CDN：不用等服务端把整首下完，第一次点歌也几乎立刻出声。
    // 但**必须先验一遍**：过期直链交给播放器时，错误会被后端吞掉（没有声音也没回退）。
    DirectSource? direct;
    try {
      direct = await api.directUrl(song.id);
      await api.verifyDirect(direct);
    } catch (err) {
      _log('直连不可用（$err），改走服务端中转');
      direct = null;
    }
    if (direct != null) {
      try {
        await coverReady;
        await _load(
          AudioSource.uri(Uri.parse(direct.url), headers: direct.headers, tag: _mediaItem(song)),
          _directLoadBudget,
          '直连',
        );
        _usingDirect = true;
        _log('直连 ${Uri.parse(direct.url).host}');
        return;
      } catch (err) {
        _log('直连装载失败（$err），改走服务端中转');
      }
    }
    // 回退到服务端代理流：必须带上 X-API-Key，否则服务端开着鉴权时就是 401
    await coverReady;
    await _load(
      AudioSource.uri(_fallbackStream!, headers: api.streamHeaders, tag: _mediaItem(song)),
      _streamLoadBudget,
      '中转流',
    );
    _usingDirect = false;
    _log('中转流 ${_fallbackStream!.path}');
  }

  /// 装载一个音源，**带超时**。
  ///
  /// ⚠️ 这是「点歌没声音、界面一直转圈」的真正原因：just_audio 只在**平台事件通道
  /// 本身报错**时才把错误抛出来，而 Windows 的 media_kit 后端把 `Failed to open ...`
  /// 当成一条普通事件发出来（见 `just_audio_media_kit` 的 `mediakit_player.dart`）。
  /// 于是 `setAudioSource` 返回的 Future **既不完成也不报错**，`_loading` 永远收不掉，
  /// 看门狗又是在 `_start` 返回之后才武装的 —— 整条回退链路根本走不到。
  ///
  /// 所以这里自己设上限：超时就当这条路不通，让调用方回退。
  Future<void> _load(AudioSource source, Duration budget, String label) async {
    final watch = Stopwatch()..start();
    try {
      await _player.setAudioSource(source).timeout(budget);
      _log('$label 装载完成 ${watch.elapsedMilliseconds} ms');
    } on TimeoutException {
      _log('$label 装载超时（${budget.inSeconds}s 没有任何回应）');
      rethrow;
    }
  }

  /// 起播看门狗：`setAudioSource` 成功 ≠ 真的出声。
  ///
  /// Windows 的 media_kit 后端把「Failed to open xxx」当成一条普通事件发出来，
  /// just_audio 只把它塞进 PlaybackEvent、不会抛异常（`just_audio.dart` 里只在
  /// **事件通道本身报错**时才 addError）。结果就是：过期直链 / CDN 拒绝 → 界面
  /// 一直停在「正在播放」，没有声音、没有报错、也不会回退。
  ///
  /// 所以这里用「播放位置有没有真的动过」当判据：直连 10 秒没动静就换中转流，
  /// 中转流 25 秒还没动静才认输（家里宽带 + frp 首次拉流可能要等）。
  ///
  /// 只认位置、不认「拿到时长」：media_kit 解析完文件头就能给出时长，但解码 / 出声音
  /// 仍可能卡住 —— 用它当「起来了」的判断会漏掉这种卡死。
  void _armWatchdog(int gen) {
    _watchdog?.cancel();
    final budget = _usingDirect ? const Duration(seconds: 10) : const Duration(seconds: 25);
    _watchdog = Timer(budget, () {
      if (_disposed || gen != _generation) return;
      if (_position > Duration.zero) return; // 位置在走 = 真的出声了
      unawaited(_recoverPlayback('${budget.inSeconds} 秒没有出声'));
    });
  }

  /// 直连放不出来时换中转流；中转流也不行才报错。
  Future<void> _recoverPlayback(String reason) async {
    if (_recovering) return;
    final song = current;
    final api = _api;
    final fallback = _fallbackStream;
    _watchdog?.cancel();
    if (song == null || api == null || fallback == null) return;
    final gen = _generation;
    if (!_usingDirect) {
      // 已经在中转流上了：这条路也放不出来，只能报错
      if (_disposed || gen != _generation) return;
      _error = '播放失败：音源没有出声（$reason）';
      _playing = false;
      _loading = false;
      _notify();
      return;
    }
    _recovering = true;
    _usingDirect = false;
    _log('直连没有出声（$reason），回退到服务端中转');
    try {
      await _load(
        AudioSource.uri(fallback, headers: api.streamHeaders, tag: _mediaItem(song)),
        _streamLoadBudget,
        '中转流（回退）',
      );
      if (_disposed || gen != _generation) return;
      _resume();
      _armWatchdog(gen);
      return;
    } catch (err) {
      _log('中转流也装不上（$err）');
      _error = '播放失败：$err';
    } finally {
      _recovering = false;
    }
    if (_disposed || gen != _generation) return;
    _playing = false;
    _loading = false;
    _notify();
  }

  /// 播放（不等待 Future 完成，否则会一直卡在 loading 状态）。
  void _resume() {
    unawaited(
      _player.play().catchError((Object error) {
        _log('play() 报错：$error');
        _error = '播放失败：$error';
        _playing = false;
        _notify();
      }),
    );
  }

  void _log(String message) => debugLog('play', message);

  // ------------------------------------------------------------- 上一个/下一个
  /// 下一个：手动点会循环，自动播完在「顺序播放」模式下会停下。
  int? _nextIndex({required bool auto}) {
    if (_queue.isEmpty) return null;
    if (_queue.length == 1) {
      // 队列只有一首：随机播放没有「下一个」，但播完还是该重头再来
      // （以前这里会 fall through，随机模式下自动切歌直接停住）
      if (auto && _mode == PlayMode.order) return null;
      return 0;
    }
    if (_mode == PlayMode.shuffle) {
      var next = _index;
      while (next == _index) {
        next = _random.nextInt(_queue.length);
      }
      return next;
    }
    final next = _index + 1;
    if (next < _queue.length) return next;
    if (!auto || _mode == PlayMode.loopAll) return 0;
    return null; // 顺序播放到底
  }

  Future<void> next() async {
    final target = _nextIndex(auto: false);
    if (target == null) return;
    _index = target;
    await _playCurrent();
  }

  Future<void> previous() async {
    if (_queue.isEmpty) return;
    // 播了 3 秒以上先回到开头，符合一般播放器习惯
    if (_position.inSeconds > 3) {
      await _player.seek(Duration.zero);
      return;
    }
    _index = _index <= 0 ? _queue.length - 1 : _index - 1;
    await _playCurrent();
  }

  Future<void> playAt(int index) async {
    if (index < 0 || index >= _queue.length) return;
    _index = index;
    await _playCurrent();
  }

  Future<void> _handleCompleted() async {
    if (_mode == PlayMode.loopOne) {
      await _player.seek(Duration.zero);
      _resume();
      return;
    }
    final target = _nextIndex(auto: true);
    if (target == null) {
      await _player.pause();
      await _player.seek(Duration.zero);
      _notify();
      return;
    }
    _index = target;
    await _playCurrent();
  }

  void setMode(PlayMode mode) {
    _mode = mode;
    _notify();
  }

  PlayMode cycleMode() {
    const values = PlayMode.values;
    setMode(values[(values.indexOf(_mode) + 1) % values.length]);
    return _mode;
  }

  void removeAt(int index) {
    if (index < 0 || index >= _queue.length) return;
    final wasCurrent = index == _index;
    final updated = List<Song>.of(_queue)..removeAt(index);
    _queue = updated;
    if (updated.isEmpty) {
      stop();
      return;
    }
    if (wasCurrent) {
      _index = index.clamp(0, updated.length - 1);
      unawaited(_playCurrent());
      return;
    }
    if (index < _index) _index -= 1;
    _notify();
  }

  Future<void> toggle() async {
    if (current == null) return;
    if (_player.playing) {
      await _player.pause();
    } else {
      _resume();
    }
  }

  Future<void> seek(Duration position) {
    // 乐观先把滑块挪过去：松手到 positionStream 真的跳过去之间有几帧，
    // 不然滑块会「先跳过去、又倒着退回来」才落位
    _position = position;
    _notify();
    return _player.seek(position);
  }

  /// 换服务器 / 重新登录 / 退出登录之后调用：新的请求要用新的地址和 key。
  ///
  /// 不调的话，播放器里存的还是旧 `_api`——回退流会打到旧地址、播放历史会用旧 key 发。
  /// 已经在播的这一首不动（音频是播放器自己在读，和 ApiClient 无关）。
  void updateApi(ApiClient api) {
    _api = api;
    _fallbackStream = null; // 旧地址的流不能再拿来当回退
    _usingDirect = false;
    _watchdog?.cancel();
    _notify();
  }

  void stop() {
    ++_generation; // 让还在飞的那次点歌别在 stop 之后又 resume 起来
    _watchdog?.cancel();
    _recovering = false;
    unawaited(_player.stop());
    _queue = const [];
    _index = -1;
    _playing = false;
    _loading = false;
    _error = null;
    _position = Duration.zero;
    _duration = null;
    _usingDirect = false;
    _fallbackStream = null;
    _notify();
  }

  // ------------------------------------------------------------------ 出错
  Future<void> _handlePlaybackError(Object error) async {
    _log('播放器报错：$error');
    if (_recovering) return; // 正在切中转流，这次报错多半是上一条直连的余波
    final song = current;
    if (song == null) return; // 队列空了（stop 过 / 还没点歌），没什么好回退的
    if (_usingDirect) {
      await _recoverPlayback('播放器报错');
      return;
    }
    if (_disposed) return;
    _watchdog?.cancel();
    _error = '播放失败：$error';
    _playing = false;
    _notify();
  }

  /// 控制中心 / 锁屏用的媒体信息。
  ///
  /// 封面必须优先用 [CoverCache]：搜索结果里的 `coverUrl` 只是「搜到那一刻」的音源原图，
  /// QQ 封面是后台补的。如果这里直接用 `song.coverUrl`，控制中心会一直锁着酷我的糊图，
  /// 之后 UI 换成 QQ 封面也带不动它（just_audio_background 的 MediaItem 只在 setAudioSource
  /// 时取一次 tag，之后不会跟着 [CoverCache.revision] 更新）。
  MediaItem _mediaItem(Song song) {
    final cover = CoverCache.of(song.id) ?? song.coverUrl;
    return MediaItem(
      id: song.id,
      title: song.name,
      artist: song.singers,
      album: song.album.isEmpty ? 'Tonicuisc' : song.album,
      artUri: cover.isEmpty ? null : Uri.tryParse(cover),
      extras: {'source': song.sourceLabel},
    );
  }

  @override
  void dispose() {
    _disposed = true;
    _watchdog?.cancel();
    for (final subscription in _subscriptions) {
      subscription.cancel();
    }
    _subscriptions.clear();
    unawaited(_player.dispose());
    super.dispose();
  }
}

String formatDuration(Duration? value) {
  if (value == null) return '--:--';
  final minutes = value.inMinutes.remainder(60).toString().padLeft(2, '0');
  final seconds = value.inSeconds.remainder(60).toString().padLeft(2, '0');
  return value.inHours > 0 ? '${value.inHours}:$minutes:$seconds' : '$minutes:$seconds';
}
