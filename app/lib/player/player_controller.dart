import 'package:flutter/foundation.dart';
import 'package:just_audio/just_audio.dart';

import '../models/song.dart';

/// 播放器状态封装：just_audio 在 iOS / macOS 用系统后端，
/// Windows / Linux 走 media_kit 后端（见 main.dart 的初始化）。
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
  }

  final AudioPlayer _player = AudioPlayer();

  Song? _current;
  Duration _position = Duration.zero;
  Duration? _duration;
  bool _playing = false;
  bool _loading = false;
  String? _error;

  Song? get current => _current;
  Duration get position => _position;
  Duration? get duration => _duration;
  bool get playing => _playing;
  bool get loading => _loading;
  String? get error => _error;

  Future<void> play(Song song, Uri uri) async {
    _current = song;
    _error = null;
    _duration = null;
    _position = Duration.zero;
    _loading = true;
    notifyListeners();
    try {
      await _player.setUrl(uri.toString());
      await _player.play();
    } catch (err) {
      _error = '播放失败：$err';
      _playing = false;
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  Future<void> toggle() async {
    if (_current == null) return;
    if (_player.playing) {
      await _player.pause();
    } else {
      await _player.play();
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
