import 'dart:io';

import 'debug_log.dart';

/// `%TEMP%` 能不能写 —— 单独查这一件事，因为它能把播放整个卡死。
///
/// 为什么：media_kit 在 **Debug** 构建下会往
/// `<临时目录>\com.alexmercerind.media_kit.NativeReferenceHolder.<pid>` 写一个
/// 指针缓冲区的地址（见 `media_kit/src/player/native/utils/native_reference_holder.dart`）。
/// 写失败时它内部抛的是**未捕获的异步异常**，而且 `_completer` 永远不会 complete ——
/// 接着 `add()` 里的 `await _completer.future` 就永远等下去，**创建播放器直接卡死**。
///
/// 表现出来就是：点歌后一直转圈、没有声音、没有报错、也不会回退到中转流
/// （`setAudioSource` 的 Future 根本不返回）。Release 构建里整段代码都被
/// `kDebugMode` 跳过了，所以**只有 Debug 会中招** —— 这也是「同一份代码，
/// Release 能放、Debug 放不了」的原因。
///
/// 实测触发条件：`C:\Users\<用户>\AppData\Local\Temp` 的 ACL 被改坏（比如被某个
/// 沙箱工具塞了一堆 `xxxSandboxUsers` 组），连 `flutter run` 自己都会因为
/// 「Creation of temporary directory failed」崩掉。
class TempDirCheck {
  /// `main()` 里查一次，之后各处同步读。
  static bool writable = true;

  /// 真正写一个文件再删掉（只查权限不够，ACL / 磁盘满 / 只读卷都会被这一步抓住）。
  static Future<bool> probe() async {
    final probe = File(
      '${Directory.systemTemp.path}${Platform.pathSeparator}tonicuisc-temp-$pid.txt',
    );
    try {
      probe.writeAsStringSync('ok');
      probe.deleteSync();
      writable = true;
    } catch (err) {
      writable = false;
      debugLog('play', '临时目录 ${Directory.systemTemp.path} 不可写：$err');
    }
    return writable;
  }
}
