import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';

import '../api/api_client.dart';
import '../models/song.dart';
import '../state/cover_cache.dart';

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
        _position = value;
        _notify();
      }),
      _player.durationStream.listen((value) {
        _duration = value;
        _notify();
      }),
      _player.playerStateStream.listen((value) {
        _playing = value.playing;
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

  Duration _position = Duration.zero;
  Duration? _duration;
  bool _playing = false;
  bool _loading = false;
  String? _error;
  bool _usingDirect = false;
  Uri? _fallbackStream;

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
    final gen = ++_generation;
    _error = null;
    _duration = null;
    _position = Duration.zero;
    _loading = true;
    _notify();
    try {
      await _start(song, api);
    } catch (err) {
      if (gen != _generation) return; // 已经被下一次点歌接管，别把上一首的错误报到这一首上
      _error = '播放失败：$err';
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
  }

  Future<void> _reportPlay(ApiClient api, String songId) async {
    try {
      await api.libraryAdd('history', songId);
    } catch (_) {
      // 忽略：历史记录失败不该影响播放
    }
  }

  Future<void> _start(Song song, ApiClient api) async {
    _fallbackStream = api.streamUri(song.id);
    // 直连 CDN：不用等服务端把整首下完，第一次点歌也几乎立刻出声
    try {
      final direct = await api.directUrl(song.id);
      await _player.setAudioSource(
        AudioSource.uri(Uri.parse(direct.url), headers: direct.headers, tag: _mediaItem(song)),
      );
      _usingDirect = true;
      return;
    } catch (_) {
      // 直链不可用（过期 / 需要额外鉴权）时走服务端代理
    }
    // 回退到服务端代理流：必须带上 X-API-Key，否则服务端开着鉴权时就是 401
    await _player.setAudioSource(
      AudioSource.uri(_fallbackStream!, headers: api.streamHeaders, tag: _mediaItem(song)),
    );
    _usingDirect = false;
  }

  /// 播放（不等待 Future 完成，否则会一直卡在 loading 状态）。
  void _resume() {
    unawaited(
      _player.play().catchError((Object error) {
        _error = '播放失败：$error';
        _playing = false;
        _notify();
      }),
    );
  }

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
    final values = PlayMode.values;
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
    _notify();
  }

  void stop() {
    ++_generation; // 让还在飞的那次点歌别在 stop 之后又 resume 起来
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
    final song = current;
    if (song == null) return; // 队列空了（stop 过 / 还没点歌），没什么好回退的
    final gen = _generation;
    final fallback = _fallbackStream;
    final api = _api;
    if (_usingDirect && fallback != null && api != null) {
      _usingDirect = false;
      try {
        await _player.setAudioSource(
          AudioSource.uri(fallback, headers: api.streamHeaders, tag: _mediaItem(song)),
        );
        if (gen != _generation) return; // 期间已经切歌了，别把上一首接管过来
        _resume();
        return;
      } catch (err) {
        _error = '播放失败：$err';
      }
    } else {
      _error = '播放失败：$error';
    }
    if (gen != _generation) return;
    _playing = false;
    _notify();
  }

  MediaItem _mediaItem(Song song) => MediaItem(
        id: song.id,
        title: song.name,
        artist: song.singers,
        album: song.album.isEmpty ? 'Tonicuisc' : song.album,
        artUri: song.coverUrl.isEmpty ? null : Uri.tryParse(song.coverUrl),
        extras: {'source': song.sourceLabel},
      );

  @override
  void dispose() {
    _disposed = true;
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
