import 'package:flutter/material.dart';

import 'theme.dart';

/// **过渡用的临时壳，全部迁完之后整个文件删掉。**
///
/// `CupertinoApp` 不提供 `ScaffoldMessenger`，也没有 `Material` 祖先；而
/// `TextField` / `ListTile` / `IconButton` / `FilterChip` / `Slider` / `Card`
/// 这些 Material 控件**找不到祖先会直接抛断言**。迁移是逐页做的，所以先用
/// 这层把还没迁的页面罩住：
///
/// - `ScaffoldMessenger` → 页面里现存的 `showSnackBar` 照常工作；
/// - `Material(transparency)` → 提供 Material 祖先（透明，不挡 Cupertino 的背景）；
/// - `Theme` → 让还没迁的 Material 控件至少跟上深色模式，行为也偏 iOS。
///
/// 亮暗由调用方（`app.dart`）传进来，本文件**只 import material、不 import
/// cupertino**：两边的库都很大，混在一个文件里容易撞同名符号，没必要冒这个险。
class LegacyMaterialHost extends StatelessWidget {
  const LegacyMaterialHost({super.key, required this.brightness, required this.child});

  final Brightness brightness;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Theme(
      data: ThemeData(
        useMaterial3: true,
        brightness: brightness,
        colorScheme: ColorScheme.fromSeed(seedColor: kAccentLight, brightness: brightness),
        platform: TargetPlatform.iOS,
      ),
      child: Material(
        type: MaterialType.transparency,
        child: ScaffoldMessenger(child: child),
      ),
    );
  }
}
