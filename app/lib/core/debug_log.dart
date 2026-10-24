import 'dart:io';

import 'package:flutter/foundation.dart';

/// Debug 构建里把一行日志同时写到控制台和 `%TEMP%\tonicuisc-<tag>.log`。
/// **Release 下是空操作**，不会在用户机器上留文件。
///
/// 为什么需要：直接用 `tonicuisc.exe` 启动时是没有控制台的（GUI 子系统），
/// `debugPrint` 写了也看不见；而「播放没声音」「输入法打不出字」这类问题，
/// 恰恰只能靠这些日志判断走的是直连还是中转、组字有没有被打断。
void debugLog(String tag, String message) {
  if (!kDebugMode) return;
  final line = '[${DateTime.now().toIso8601String()}] $message';
  debugPrint(line);
  // 单元测试里 `kDebugMode` 也是 true：别让 widget test 往临时目录里写文件。
  if (Platform.environment.containsKey('FLUTTER_TEST')) return;
  try {
    File('${Directory.systemTemp.path}${Platform.pathSeparator}tonicuisc-$tag.log')
        .writeAsStringSync('$line\n', mode: FileMode.append, flush: true);
  } catch (_) {
    // 日志写不进去不能影响功能
  }
}
