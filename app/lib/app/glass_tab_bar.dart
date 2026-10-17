import 'package:flutter/cupertino.dart';

import '../shared/glass.dart';

/// 悬浮胶囊 tab 栏离窗口左右 / 下边缘的边距。
///
/// 放在这里而不是外壳里：这是「胶囊」这个观感的一部分 —— 贴到边上就不叫悬浮了。
const EdgeInsets kFloatingTabBarMargin = EdgeInsets.fromLTRB(12, 0, 12, 8);

/// 悬浮 tab 栏里的一项。
class GlassTabItem {
  const GlassTabItem({required this.icon, required this.selectedIcon, required this.label});

  final IconData icon;
  final IconData selectedIcon;
  final String label;
}

/// iOS 26 那种**悬浮胶囊**底部 tab 栏。
///
/// 和 `CupertinoTabBar` 的区别在于形状与位置：
///
/// * `CupertinoTabBar` 是**贴底通栏**的一条，顶边一条分割线；这是 iOS 18 之前的做法。
/// * iOS 26 把它做成一颗**圆角胶囊浮在内容上方**，四周留白、有投影，材质是液态玻璃。
///
/// 所以这里不用 `CupertinoTabBar`（它自带底色 + 分割线，而且会自己吃掉
/// `viewPadding.bottom` 把高度撑大，套进胶囊里很难看），直接自己画。
///
/// **底部安全区（home indicator）由调用方用 `Padding` 补** —— 这里不自己吃，
/// 否则胶囊会被撑高、圆角比例就不对了。
class GlassTabBar extends StatelessWidget {
  const GlassTabBar({
    super.key,
    required this.currentIndex,
    required this.items,
    required this.onTap,
  });

  final int currentIndex;
  final List<GlassTabItem> items;
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    return GlassSurface(
      radius: 30,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 6),
        child: Row(
          children: [
            for (var i = 0; i < items.length; i++)
              Expanded(
                child: _GlassTabTile(
                  item: items[i],
                  selected: i == currentIndex,
                  onTap: () => onTap(i),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _GlassTabTile extends StatelessWidget {
  const _GlassTabTile({required this.item, required this.selected, required this.onTap});

  final GlassTabItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final accent = CupertinoTheme.of(context).primaryColor;
    final idle = CupertinoColors.secondaryLabel.resolveFrom(context);
    final color = selected ? accent : idle;
    return Semantics(
      button: true,
      selected: selected,
      child: GestureDetector(
        behavior: HitTestBehavior.opaque,
        onTap: onTap,
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          curve: Curves.easeOut,
          padding: const EdgeInsets.symmetric(vertical: 7),
          decoration: BoxDecoration(
            color: selected ? accent.withValues(alpha: 0.14) : null,
            borderRadius: BorderRadius.circular(18),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(selected ? item.selectedIcon : item.icon, size: 20, color: color),
              const SizedBox(height: 3),
              Text(
                item.label,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 10.5,
                  fontWeight: selected ? FontWeight.w600 : FontWeight.w500,
                  color: color,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
