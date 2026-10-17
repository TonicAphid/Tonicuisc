import 'dart:ui' show ImageFilter;

import 'package:flutter/cupertino.dart';

/// 液态玻璃的模糊半径。
///
/// iOS 26（Liquid Glass）的材质大概在这个量级：底下的东西糊得出来、但还看得出
/// 在动。太小像塑料，太大像磨砂纸。
const double kGlassBlur = 24;

/// 「液态玻璃」材质：模糊 + 半透明底色 + 一圈高光描边 + 圆角 + 淡淡投影。
///
/// Cupertino 里**没有**现成的毛玻璃组件 —— `CupertinoNavigationBar` /
/// `CupertinoTabBar` 的 `barBackgroundColor` 只是**半透明**，并不模糊，压在
/// 内容上还是看得清每一个像素。iOS 26 的观感靠这四层叠出来：
///
/// 1. `BackdropFilter` 把底下的内容**糊掉**（这一层是「玻璃」的本体）
/// 2. 半透明底色把糊掉的结果整体压亮 / 压暗一点（决定这块玻璃偏白还是偏黑）
/// 3. 一圈 0.5px 的浅色描边 —— 「玻璃边缘反光」，少了它就成一块塑料板
/// 4. 一圈很淡的投影 —— 让它看起来是**浮**在内容上的，而不是贴上去的
///
/// 注意 `ClipRRect` 必须在 `BackdropFilter` **外面**：`BackdropFilter` 会把
/// 整个 layer 都糊掉，不裁的话模糊会溢出到圆角外面去。
class GlassSurface extends StatelessWidget {
  const GlassSurface({
    super.key,
    required this.child,
    this.radius = 28,
    this.blur = kGlassBlur,
    this.tint,
    this.border,
    this.shadow = true,
  });

  final Widget child;

  /// 圆角。iOS 26 的悬浮元素圆角都很大，默认给 28。
  final double radius;

  final double blur;

  /// 半透明底色。不传就按当前亮度取默认（浅色偏白、深色偏黑）。
  final Color? tint;

  /// 高光描边颜色。不传就按当前亮度取默认。
  final Color? border;

  /// 要不要那圈投影。
  final bool shadow;

  @override
  Widget build(BuildContext context) {
    final dark = CupertinoTheme.of(context).brightness == Brightness.dark;
    final resolvedTint = tint ?? (dark ? const Color(0xB31C1C1E) : const Color(0xB3FFFFFF));
    final resolvedBorder = border ?? (dark ? const Color(0x1FFFFFFF) : const Color(0x14000000));

    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        boxShadow: shadow
            ? [
                BoxShadow(
                  color: CupertinoColors.black.withValues(alpha: dark ? 0.36 : 0.12),
                  blurRadius: 18,
                  offset: const Offset(0, 6),
                ),
              ]
            : null,
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(radius),
        child: BackdropFilter(
          filter: ImageFilter.blur(sigmaX: blur, sigmaY: blur),
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: resolvedTint,
              borderRadius: BorderRadius.circular(radius),
              border: Border.all(color: resolvedBorder, width: 0.5),
            ),
            child: child,
          ),
        ),
      ),
    );
  }
}
