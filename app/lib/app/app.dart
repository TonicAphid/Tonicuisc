import 'package:flutter/material.dart';

import 'main_shell.dart';

/// 应用根 widget：主题 + 外壳。
///
/// 单独一个文件（不再和 `main()` 挤在一起）：`main.dart` 只管平台初始化，
/// 这里只管「界面长什么样」，两边改起来互不影响。
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
      home: const MainShell(),
    );
  }
}
