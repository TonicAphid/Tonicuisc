import 'package:flutter/cupertino.dart';

/// iOS 系统蓝。Cupertino 的按钮 / 开关 / 分段控件默认就是这一支，
/// 所以主题里不用到处覆盖颜色。深色模式用官方那支更亮的蓝。
const Color kAccentLight = Color(0xFF007AFF);
const Color kAccentDark = Color(0xFF0A84FF);

/// 内容区 / 侧边栏的底色。
///
/// 浅色下：内容纯白、侧边栏略灰；深色下：内容纯黑、侧边栏略亮。
/// 两边都有淡淡一层层次——macOS / iPadOS 上 Apple Music 就是这种观感。
const Color kContentLight = Color(0xFFFFFFFF);
const Color kContentDark = Color(0xFF000000);
const Color kSidebarLight = Color(0xFFF2F2F7);
const Color kSidebarDark = Color(0xFF1C1C1E);

/// 桌面布局左侧导航的宽度：带文字，所以比「只放图标」的窄栏宽。
const double kSidebarWidth = 208;

bool _isDark(BuildContext context) =>
    CupertinoTheme.of(context).brightness == Brightness.dark;

/// 内容区底色（按当前亮度取）。
Color contentColor(BuildContext context) =>
    _isDark(context) ? kContentDark : kContentLight;

/// 侧边栏底色（按当前亮度取）。
Color sidebarColor(BuildContext context) =>
    _isDark(context) ? kSidebarDark : kSidebarLight;

/// 浅色 / 深色两套主题。
///
/// 只有颜色，没有字体：`CupertinoThemeData` 默认就带苹果那套字号 / 字重，
/// 而且在 Windows / Linux 上会自然回落到系统字体（Segoe UI 等），不用额外配置。
class AppTheme {
  const AppTheme._();

  static const CupertinoThemeData light = CupertinoThemeData(
    brightness: Brightness.light,
    primaryColor: kAccentLight,
    scaffoldBackgroundColor: kContentLight,
    // **必须是不透明色**：`CupertinoPageScaffold` 靠 `barBackgroundColor.alpha == 0xFF`
    // 判断顶栏是不是「完全遮挡」。半透明时它会把内容画到顶栏**后面**（只把顶栏高度
    // 当提示塞进 MediaQuery.padding.top），而各页面还是按老 Material `AppBar` 的规矩
    // 排版 → 内容顶部被顶栏盖住、往下滑还会弹回去。
    // 要做 iOS 26 那种「内容从毛玻璃顶栏下穿过」，得先让每个页面自己吃下这个顶部
    // 内边距，见 README「客户端界面」。
    barBackgroundColor: kContentLight,
  );

  static const CupertinoThemeData dark = CupertinoThemeData(
    brightness: Brightness.dark,
    primaryColor: kAccentDark,
    scaffoldBackgroundColor: kContentDark,
    barBackgroundColor: kContentDark,
  );
}
