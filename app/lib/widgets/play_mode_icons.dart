import 'package:flutter/material.dart';

import '../player/player_controller.dart';

/// 播放模式对应的图标（放在 UI 层，播放器本身不依赖 material）。
IconData playModeIcon(PlayMode mode) => switch (mode) {
      PlayMode.order => Icons.playlist_play,
      PlayMode.loopAll => Icons.repeat,
      PlayMode.loopOne => Icons.repeat_one,
      PlayMode.shuffle => Icons.shuffle,
    };
