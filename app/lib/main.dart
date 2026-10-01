import 'dart:io';

import 'package:flutter/material.dart';
import 'package:just_audio_background/just_audio_background.dart';
import 'package:just_audio_media_kit/just_audio_media_kit.dart';

import 'pages/home_page.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
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

class TonicuiscApp extends StatelessWidget {
  const TonicuiscApp({super.key});

  @override
  Widget build(BuildContext context) {
    final scheme = ColorScheme.fromSeed(seedColor: const Color(0xFF3F51B5));
    return MaterialApp(
      title: 'Tonicuisc',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(colorScheme: scheme, useMaterial3: true),
      darkTheme: ThemeData(colorScheme: scheme.copyWith(brightness: Brightness.dark), useMaterial3: true),
      home: const HomePage(),
    );
  }
}
