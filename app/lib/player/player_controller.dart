import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';
import 'package:just_audio_background/just_audio_background.dart';

import '../api/api_client.dart';
import '../models/song.dart';

/// 播放器状态封装。
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
        _player.pause();
        _player.seek(Duration.zero);
      }
      notifyListeners();
    });
    _player.playbackEventStream.listen(
      (_) {},
      onError: (Object error, StackTrace stack) => _handlePlaybackError(error),
    );
  }

  final AudioPlayer _player = AudioPlayer();

  Song? _current;
  Duration _position = Duration.zero;
  Duration? _duration;
  bool _playing = false;
  bool _loading = false;
  String? _error;
  bool _usingDirect = false;
  Uri? _fallbackStream;

  Song? get current => _current;
  Duration get position => _position;
  Duration? get duration => _duration;
  bool get playing => _playing;
  bool get loading => _loading;
  String? get error => _error;

  Future<void> play(Song song, ApiClient api) async {
    _current = song;
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
    // 注意：just_audio 的 play() 要到暂停/播放结束才完成，await 会把 loading 卡死。
    _resume();
  }

  Future<void> _reportPlay(ApiClient api, String songId) async {
    try {
      await api.libraryAdd('history', songId);
    } catch (_) {
      // 忽略：历史记录失败不该影响播放
    }
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

  Future<void> _handlePlaybackError(Object error) async {
    final fallback = _fallbackStream;
    final song = _current;
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

  Future<void> toggle() async {
    if (_current == null) return;
    if (_player.playing) {
      await _player.pause();
    } else {
      _resume();
    }
  }

  Future<void> seek(Duration position) => _player.seek(position);

  void stop() {
    _player.stop();
    _current = null;
    _playing = false;
    notifyListeners();
  }

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
