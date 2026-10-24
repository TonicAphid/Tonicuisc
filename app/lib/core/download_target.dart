/// 下载目标目录。
///
/// * Windows / macOS / Linux：第一次下载让用户选文件夹，之后记住；也可以在「我的」里改。
/// * iOS / Android：写到 App 文档目录下的 `Downloads/`，iOS 通过 Info.plist 的
///   `UIFileSharingEnabled` 暴露到「文件」App。
library;

import 'dart:io';

import 'package:file_selector/file_selector.dart';
import 'package:path_provider/path_provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

class DownloadTarget {
  DownloadTarget._();

  static const String _key = 'tonicuisc.download_dir';

  /// 返回要写入的目录；用户在桌面端取消选择时返回 null。
  static Future<Directory?> resolve() async {
    if (Platform.isIOS || Platform.isAndroid) {
      return _mobileDirectory();
    }

    final prefs = await SharedPreferences.getInstance();
    final saved = prefs.getString(_key);
    if (saved != null && saved.trim().isNotEmpty) {
      final dir = Directory(saved);
      if (await dir.exists()) return dir;
    }

    // 没设置过：走系统文件夹选择器；取消就放弃这次下载
    return choose();
  }

  /// 让用户选一个文件夹并记住；取消返回 null。
  static Future<Directory?> choose() async {
    final path = await getDirectoryPath(confirmButtonText: '选择文件夹');
    if (path == null || path.trim().isEmpty) return null;
    final dir = Directory(path);
    await dir.create(recursive: true);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(_key, dir.path);
    return dir;
  }

  /// 清掉记住的目录（macOS 沙盒重启后权限失效时用）。
  static Future<void> clear() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }

  static Future<Directory> _mobileDirectory() async {
    final docs = await getApplicationDocumentsDirectory();
    final dir = Directory('${docs.path}${Platform.pathSeparator}Downloads');
    await dir.create(recursive: true);
    return dir;
  }
}
