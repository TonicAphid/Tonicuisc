import 'package:flutter/cupertino.dart';

import 'theme.dart';

/// 侧边栏的一项。
class NavSidebarItem {
  const NavSidebarItem({required this.icon, required this.selectedIcon, required this.label});

  final IconData icon;
  final IconData selectedIcon;
  final String label;
}

/// 桌面布局的左侧导航栏——Apple Music / Finder 那种「图标 + 文字」的宽侧边栏。
///
/// Cupertino 没有 `NavigationRail`，所以自己画：固定宽度、圆角选中块、
/// 鼠标悬停有淡淡一层底色，选中 / 悬停 / 换页都有 140ms 过渡。
class NavSidebar extends StatelessWidget {
  const NavSidebar({
    super.key,
    required this.currentIndex,
    required this.items,
    required this.onSelected,
    this.title = 'Tonicuisc',
  });

  final int currentIndex;
  final List<NavSidebarItem> items;
  final ValueChanged<int> onSelected;
  final String title;

  @override
  Widget build(BuildContext context) {
    final accent = CupertinoTheme.of(context).primaryColor;
    return Container(
      width: kSidebarWidth,
      color: sidebarColor(context),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 22, 20, 16),
            child: Row(
              children: [
                Icon(CupertinoIcons.music_note, size: 19, color: accent),
                const SizedBox(width: 8),
                Text(
                  title,
                  style: TextStyle(
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                    color: CupertinoColors.label.resolveFrom(context),
                  ),
                ),
              ],
            ),
          ),
          for (var i = 0; i < items.length; i++)
            _SidebarTile(
              item: items[i],
              selected: i == currentIndex,
              onTap: () => onSelected(i),
            ),
          const Spacer(),
        ],
      ),
    );
  }
}

class _SidebarTile extends StatefulWidget {
  const _SidebarTile({required this.item, required this.selected, required this.onTap});

  final NavSidebarItem item;
  final bool selected;
  final VoidCallback onTap;

  @override
  State<_SidebarTile> createState() => _SidebarTileState();
}

class _SidebarTileState extends State<_SidebarTile> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final accent = CupertinoTheme.of(context).primaryColor;
    final label = CupertinoColors.label.resolveFrom(context);
    final Color? fill = widget.selected
        ? accent.withValues(alpha: 0.14)
        : (_hovered ? label.withValues(alpha: 0.06) : null);
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: widget.onTap,
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 140),
            curve: Curves.easeOut,
            height: 34,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(color: fill, borderRadius: BorderRadius.circular(8)),
            child: Row(
              children: [
                Icon(
                  widget.selected ? widget.item.selectedIcon : widget.item.icon,
                  size: 17,
                  color: widget.selected ? accent : label,
                ),
                const SizedBox(width: 10),
                Text(
                  widget.item.label,
                  style: TextStyle(
                    fontSize: 14,
                    fontWeight: widget.selected ? FontWeight.w600 : FontWeight.w400,
                    color: widget.selected ? accent : label,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
