import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';

import 'debug_log.dart';

/// 桌面端窗口「是否在前台」（常亮逻辑要用）。
///
/// 用 [ValueNotifier] 是因为「窗口失活」这件事在桌面端被我们从生命周期里拦掉了，
/// 但别的功能（比如播放时的常亮）仍然需要知道它 —— 两边各拿各的信号，互不影响。
class DesktopLifecycle {
  static final ValueNotifier<bool> foreground = ValueNotifier<bool>(true);

  static bool get _isDesktop => Platform.isWindows || Platform.isLinux || Platform.isMacOS;

  /// 桌面端把「窗口失焦」对框架的影响整个拦掉，保住输入法组字。
  ///
  /// **引擎把「窗口失焦」分两路告诉框架，两路都会把输入焦点收走，必须都拦：**
  ///
  /// 1. `SystemChannels.lifecycle` → `AppLifecycleState.inactive` →
  ///    `FocusManager._appLifecycleChange` 挂起 `primaryFocus`（`_suspendedNode` + `unfocus`）。
  /// 2. `PlatformDispatcher.onViewFocusChange` → `View.didChangeViewFocus` 收到
  ///    `ViewFocusState.unfocused` 后执行 `rootScope.requestScopeFocus()`，把焦点
  ///    「停」在根作用域上（`packages/flutter/lib/src/widgets/view.dart`）。
  ///
  /// 日志里两路都能看见（`core/ime_debug.dart` 写的那份）：
  ///
  /// ```
  /// search text="a"  composing=(0, 1)
  /// search focus=false                    ← 17~22ms 后焦点被收走
  /// search text="a"  composing=(-1, -1)   ← 组字被结束，只剩第一个字母
  /// ```
  ///
  /// 触发者是第三方输入法（实测微信输入法）：打出第一个字母时弹出候选窗，
  /// 主窗口短暂失活 / 失去原生焦点，于是两路信号同时到达。Flutter 自己在
  /// `focus_manager.dart` 的 `_respondToLifecycleChange` 注释里写了同类的坑
  /// （Android 键盘也会这样，所以移动端干脆不监听生命周期），Windows / macOS
  /// 还开着，只能我们自己拦。
  ///
  /// 拦掉之后：`hidden` / `paused`（最小化、切后台）照常透传 —— 那种情况才真的
  /// 该收焦点；`alt-tab` 出去再回来，输入框也保持原来的光标位置（桌面应用的常见行为）。
  static void install() {
    if (!_isDesktop) return;

    // 第一路：生命周期。只丢掉 `inactive`，其余状态原样转给框架。
    SystemChannels.lifecycle.setMessageHandler((message) async {
      final state = _parse(message);
      if (state == null) return null;
      if (state == AppLifecycleState.inactive) {
        foreground.value = false;
        debugLog('ime', 'window inactive（焦点保持，保住输入法组字）');
        return null;
      }
      foreground.value = state == AppLifecycleState.resumed;
      // ignore: invalid_use_of_protected_member - 这就是框架自己的生命周期入口，
      // 我们只是把它原本会收到的消息重新转进去（`inactive` 那一档除外）。
      WidgetsBinding.instance.handleAppLifecycleStateChanged(state);
      return null;
    });

    // 第二路：view focus。`unfocused` 会让 `View` 把焦点停到根作用域；
    // `focused` 又会用 `findFirstFocus(ignoreCurrentFocus: true)` 抢到「第一个」
    // 可聚焦控件上。桌面端单窗口用不着这套（它本来是给多窗口 / Web 的），
    // 一律不转给框架，只记日志。
    WidgetsBinding.instance.platformDispatcher.onViewFocusChange = (event) {
      debugLog(
        'ime',
        'view focus=${event.state} direction=${event.direction}'
        '（桌面端不转给框架，保留输入焦点）',
      );
    };
  }

  static AppLifecycleState? _parse(String? message) {
    for (final state in AppLifecycleState.values) {
      if (state.toString() == message) return state;
    }
    return null;
  }
}
