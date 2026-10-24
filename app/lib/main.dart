import 'dart:io';

import 'package:flutter/material.dart';
import 'package:just_audio_background/just_audio_background.dart';
import 'package:just_audio_media_kit/just_audio_media_kit.dart';

import 'app/app.dart';
import 'core/self_signed.dart';
import 'core/temp_dir.dart';

/// 入口：只做「平台相关的初始化」，界面本身在 `app/app.dart`。
Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  // 内网 HTTPS 自签证书的开关：默认关，用户在「我的」页长按版本号那里打开。
  // 公网域名走 Let's Encrypt，用不到它。
  SelfSignedOverrides.allow = await loadAllowSelfSigned();
  HttpOverrides.global = SelfSignedOverrides();

  // media_kit 的 Debug 版要往 %TEMP% 写一个指针文件；写不进去会**卡死播放器创建**
  // （界面永远转圈、无声音、无报错，详见 core/temp_dir.dart）。先探一次，
  // 出问题时至少日志和错误提示里说得清楚。
  await TempDirCheck.probe();

  // Windows / Linux 上 just_audio 需要 media_kit 作为后端。
  if (Platform.isWindows || Platform.isLinux) {
    JustAudioMediaKit.ensureInitialized();
  }
  // 手机端锁屏 / 控制中心的播放控制（桌面端不支持，不能调用）。
  if (Platform.isAndroid || Platform.isIOS) {
    await JustAudioBackground.init(
      androidNotificationChannelId: 'com.tonicuisc.audio',
      androidNotificationChannelName: 'Tonicuisc 播放',
      androidNotificationOngoing: true,
    );
  }
  runApp(const TonicuiscApp());
}
