import 'dart:io';

import 'package:flutter/foundation.dart';

/// Debug 构建里把一行日志同时写到控制台和 `%LOCALAPPDATA%\Tonicuisc\logs\tonicuisc-<tag>.log`。
/// **Release 下是空操作**，不会在用户机器上留文件。
///
/// 为什么需要：直接用 `tonicuisc.exe` 启动时是没有控制台的（GUI 子系统），
/// `debugPrint` 写了也看不见；而「播放没声音」「输入法打不出字」这类问题，
/// 恰恰只能靠这些日志判断走的是直连还是中转、组字有没有被打断。
///
/// 为什么不写 `%TEMP%`：实测 `%TEMP%` 的 ACL 会被各种沙箱工具改坏，
/// 连 media_kit 都因此卡死（见 `core/temp_dir.dart`）—— 日志不能和它绑在一起。
/// `%LOCALAPPDATA%` 是用户自己的目录，稳定得多。
void debugLog(String tag, String message) {
  if (!kDebugMode) return;
  final line = '[${DateTime.now().toIso8601String()}] $message';
  debugPrint(line);
  // 单元测试里 `kDebugMode` 也是 true：别让 widget test 往磁盘里写文件。
  if (Platform.environment.containsKey('FLUTTER_TEST')) return;
  final directory = _logDirectory;
  if (directory == null) return;
  try {
    File('$directory${Platform.pathSeparator}tonicuisc-$tag.log')
        .writeAsStringSync('$line\n', mode: FileMode.append, flush: true);
  } catch (_) {
    // 日志写不进去不能影响功能
  }
}

/// 目录只解析 / 创建一次：每敲一个字都 `mkdir` 太浪费。
String? _logDirectoryCache;

String? get _logDirectory {
  if (_logDirectoryCache != null) return _logDirectoryCache;
  final local = Platform.environment['LOCALAPPDATA'];
  final candidates = <String>[
    if (local != null && local.isNotEmpty) '$local${Platform.pathSeparator}Tonicuisc'
        '${Platform.pathSeparator}logs',
    // 非 Windows 桌面：退回临时目录（那边一般不会像 Windows 这样被沙箱改坏）
    if (!Platform.isWindows) Directory.systemTemp.path,
  ];
  for (final path in candidates) {
    try {
      final directory = Directory(path);
      if (!directory.existsSync()) directory.createSync(recursive: true);
      return _logDirectoryCache = path;
    } catch (_) {
      continue;
    }
  }
  return null;
}
