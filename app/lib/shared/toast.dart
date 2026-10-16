import 'dart:async';

import 'package:flutter/cupertino.dart';

/// 苹果风的轻提示，用来替代 Material 的 `SnackBar`。
///
/// 为什么要自己写：`CupertinoApp` 不提供 `ScaffoldMessenger`，
/// `ScaffoldMessenger.of(context)` 会直接抛断言。
///
/// 样式和行为：底部居中的深色胶囊、白字，淡入 → 停留 → 淡出，
/// **同时只留一条**（新的进来把旧的顶掉），并且不吃点击。
class Toast {
  Toast._();

  static OverlayEntry? _entry;

  static void show(BuildContext context, String message, {Duration duration = const Duration(milliseconds: 1800)}) {
    final overlay = Overlay.maybeOf(context, rootOverlay: true);
    if (overlay == null) return;
    dismiss();
    final entry = OverlayEntry(
      builder: (_) => _ToastView(
        message: message,
        hold: duration,
        onDismissed: () {
          if (identical(_entry, entry)) _entry = null;
          if (entry.mounted) entry.remove();
        },
      ),
    );
    _entry = entry;
    overlay.insert(entry);
  }

  /// 立刻收掉当前这条（换页 / 退出登录时用得上）。
  static void dismiss() {
    final entry = _entry;
    _entry = null;
    if (entry != null && entry.mounted) entry.remove();
  }
}

class _ToastView extends StatefulWidget {
  const _ToastView({required this.message, required this.hold, required this.onDismissed});

  final String message;
  final Duration hold;
  final VoidCallback onDismissed;

  @override
  State<_ToastView> createState() => _ToastViewState();
}

class _ToastViewState extends State<_ToastView> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 220),
    reverseDuration: const Duration(milliseconds: 180),
  );

  Timer? _timer;

  @override
  void initState() {
    super.initState();
    _controller.forward();
    _timer = Timer(widget.hold, _leave);
  }

  Future<void> _leave() async {
    if (!mounted) return;
    await _controller.reverse();
    if (!mounted) return;
    widget.onDismissed();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final fade = CurvedAnimation(parent: _controller, curve: Curves.easeOutCubic);
    return Positioned(
      left: 0,
      right: 0,
      // 抬到底部播放条 / tab 栏上面，别互相压着
      bottom: 132,
      child: IgnorePointer(
        child: FadeTransition(
          opacity: fade,
          child: SlideTransition(
            position: Tween<Offset>(begin: const Offset(0, 0.4), end: Offset.zero).animate(fade),
            child: Center(
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: CupertinoColors.black.withValues(alpha: 0.82),
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 11),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 420),
                    child: Text(
                      widget.message,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        color: CupertinoColors.white,
                        fontSize: 14,
                        decoration: TextDecoration.none,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
