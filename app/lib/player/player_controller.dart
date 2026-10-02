import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';

import '../api/api_client.dart';
import '../models/song.dart';

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
    _player.positionStream.listen((value) {
      _position = value;
      notifyListeners();
    });
    _player.durationStream.listen((value) {
      _duration = value;
      notifyListeners();
    });
    _player.playerStateStream.listen((value) {
      _playing = value.playing;
      if (value.processingState == ProcessingState.completed) {
        _playing = false;
        unawaited(_handleCompleted());
      }
      notifyListeners();
    });
    _player.playbackEventStream.listen(
      (_) {},
      onError: (Object error, StackTrace stack) => _handlePlaybackError(error),
    );
  }

  final AudioPlayer _player = AudioPlayer();
  final Random _random = Random();

  List<Song> _queue = const [];
  int _index = -1;
  PlayMode _mode = PlayMode.loopAll;
  ApiClient? _api;

  Duration _position = Duration.zero;
  Duration? _duration;
  bool _playing = false;
  bool _loading = false;
  String? _error;
  bool _usingDirect = false;
  Uri? _fallbackStream;

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
    await _playCurrent();
  }

  /// 单曲播放（搜索结果里直接点一首）。
  Future<void> play(Song song, ApiClient api) => playQueue([song], 0, api);

  Future<void> _playCurrent() async {
    final song = current;
    final api = _api;
    if (song == null || api == null) return;
    _error = null;
    _duration = null;
    _position = Duration.zero;
    _loading = true;
    notifyListeners();
    try {
      await _start(song, api);
    } catch (err) {
      _error = '播放失败：$err';
      _playing = false;
      _loading = false;
      notifyListeners();
      return;
    }
    _loading = false;
    notifyListeners();
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
    await _player.setAudioSource(AudioSource.uri(_fallbackStream!, tag: _mediaItem(song)));
    _usingDirect = false;
  }

  /// 播放（不等待 Future 完成，否则会一直卡在 loading 状态）。
  void _resume() {
    unawaited(
      _player.play().catchError((Object error) {
        _error = '播放失败：$error';
        _playing = false;
        notifyListeners();
      }),
    );
  }

  // ------------------------------------------------------------- 上一个/下一个
  /// 下一个：手动点会循环，自动播完在「顺序播放」模式下会停下。
  int? _nextIndex({required bool auto}) {
    if (_queue.isEmpty) return null;
    if (_mode == PlayMode.shuffle && _queue.length > 1) {
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
      notifyListeners();
      return;
    }
    _index = target;
    await _playCurrent();
  }

  void setMode(PlayMode mode) {
    _mode = mode;
    notifyListeners();
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
    notifyListeners();
  }

  Future<void> toggle() async {
    if (current == null) return;
    if (_player.playing) {
      await _player.pause();
    } else {
      _resume();
    }
  }

  Future<void> seek(Duration position) => _player.seek(position);

  void stop() {
    _player.stop();
    _queue = const [];
    _index = -1;
    _playing = false;
    notifyListeners();
  }

  // ------------------------------------------------------------------ 出错
  Future<void> _handlePlaybackError(Object error) async {
    final fallback = _fallbackStream;
    final song = current;
    if (_usingDirect && fallback != null && song != null) {
      _usingDirect = false;
      try {
        await _player.setAudioSource(AudioSource.uri(fallback, tag: _mediaItem(song)));
        _resume();
        return;
      } catch (err) {
        _error = '播放失败：$err';
      }
    } else {
      _error = '播放失败：$error';
    }
    _playing = false;
    notifyListeners();
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
    _player.dispose();
    super.dispose();
  }
}

String formatDuration(Duration? value) {
  if (value == null) return '--:--';
  final minutes = value.inMinutes.remainder(60).toString().padLeft(2, '0');
  final seconds = value.inSeconds.remainder(60).toString().padLeft(2, '0');
  return value.inHours > 0 ? '${value.inHours}:$minutes:$seconds' : '$minutes:$seconds';
}
