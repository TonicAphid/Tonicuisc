import 'package:flutter/cupertino.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import 'legacy_material_host.dart';
import 'main_shell.dart';
import 'theme.dart';

/// 应用根 widget：Cupertino（苹果风）主题 + 中文本地化 + 外壳。
///
/// 单独一个文件（不再和 `main()` 挤在一起）：`main.dart` 只管平台初始化，
/// 这里只管「界面长什么样」，两边改起来互不影响。
class TonicuiscApp extends StatelessWidget {
  const TonicuiscApp({super.key});

  @override
  Widget build(BuildContext context) {
    return CupertinoApp(
      title: 'Tonicuisc',
      debugShowCheckedModeBanner: false,
      // 浅色主题作为基底
      theme: AppTheme.light,
      // 全中文界面：连 Cupertino 自带的「拷贝 / 粘贴 / 取消」也跟着变中文
      locale: const Locale('zh', 'CN'),
      supportedLocales: const [Locale('zh', 'CN'), Locale('en', 'US')],
      localizationsDelegates: const [
        GlobalCupertinoLocalizations.delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ],
      // `CupertinoApp` 没有 `darkTheme` 参数（不像 `MaterialApp`），
      // 所以深色模式在这里按系统亮度自己挑一套。这层 `CupertinoTheme` 套在
      // `WidgetsApp` 外面，整个 Navigator 都在它下面，页面里取到的是对的。
      builder: (context, child) {
        final dark = MediaQuery.platformBrightnessOf(context) == Brightness.dark;
        return CupertinoTheme(
          data: dark ? AppTheme.dark : AppTheme.light,
          // TODO(迁移): 页面全部迁到 Cupertino 后，这层过渡壳和 legacy_material_host.dart 一起删
          child: LegacyMaterialHost(
            brightness: dark ? Brightness.dark : Brightness.light,
            child: child ?? const SizedBox.shrink(),
          ),
        );
      },
      home: const MainShell(),
    );
  }
}
