import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';

import 'debug_log.dart';

/// 输入法（IME）诊断：**只在 Debug 构建里生效**，Release 调用是空操作。
///
/// 为什么需要它：Windows 上「第三方输入法只能输入一个字就断」有两种完全不同的
/// 成因，必须先分清才能修：
///
/// 1. **引擎收不到组字消息** —— 文本只以「普通字符」的形式进来，
///    `composing` 永远是 `TextRange.empty`。这是 Flutter Windows 嵌入层的老问题
///    （flutter/flutter#190042 / #190043 / #191196），Dart 侧改控件没用。
/// 2. **组字被 App 打断** —— 日志里能看到 `composing=(0, 1)` 之类的范围，
///    然后下一行突然变成 `composing=(-1, -1)`、文本里落下一个字母，说明焦点 /
///    输入客户端在组字中途被重置了，这类才是我们能在 App 里修的。
///
/// 日志同时写到控制台和 `%TEMP%\tonicuisc-ime.log`（`debug_log.dart`）：直接用 exe
/// 启动时没有控制台，只能靠这个文件。
void watchIme(TextEditingController controller, String tag, {FocusNode? focus}) {
  if (!kDebugMode) return;
  controller.addListener(() {
    final value = controller.value;
    debugLog(
      'ime',
      '$tag text="${value.text}" composing=${value.composing} selection=${value.selection}',
    );
  });
  focus?.addListener(() {
    debugLog('ime', '$tag focus=${focus.hasFocus}');
  });
}

/// 光看 controller 还不够：控件被重建 / 换 key 时 controller 不会变，
/// 但输入法客户端会重挂。这里记一行，方便对照日志时间点。
void logImeEvent(String tag, String event) {
  if (!kDebugMode) return;
  debugLog('ime', '$tag $event');
}
