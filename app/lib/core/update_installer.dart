import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

/// 下载桌面安装包并静默安装，然后由脱离的脚本重新启动 App。
///
/// 当前进程会在脚本启动后立即退出；脚本等文件锁释放后再覆盖安装。
class UpdateInstaller {
  UpdateInstaller._();

  static Future<void> downloadAndInstall({
    required String url,
    required String fileName,
    required void Function(double progress) onProgress,
  }) async {
    final temp = await Directory.systemTemp.createTemp('tonicuisc_update');
    final target = File('${temp.path}${Platform.pathSeparator}${_safeName(fileName)}');
    await _download(url, target, onProgress);

    if (Platform.isWindows) {
      await _installWindows(target);
    } else if (Platform.isMacOS) {
      await _installMacos(target);
    } else if (Platform.isLinux) {
      await _installLinux(target);
    } else {
      throw UnsupportedError('当前平台不支持自动安装');
    }
    // 脚本已经脱离；这里结束旧进程，安装脚本会在 2 秒后接手。
    exit(0);
  }

  static String _safeName(String name) {
    final cleaned = name.replaceAll(RegExp(r'[\\/:*?"<>|]'), '_').trim();
    return cleaned.isEmpty ? 'tonicuisc-update' : cleaned;
  }

  static Future<void> _download(
    String url,
    File target,
    void Function(double progress) onProgress,
  ) async {
    final client = http.Client();
    try {
      final request = http.Request('GET', Uri.parse(url));
      final response = await client.send(request);
      if (response.statusCode != 200) {
        throw HttpException('下载失败：HTTP ${response.statusCode}', uri: Uri.parse(url));
      }
      final total = response.contentLength ?? 0;
      final sink = target.openWrite();
      var received = 0;
      try {
        await for (final chunk in response.stream) {
          sink.add(chunk);
          received += chunk.length;
          onProgress(total > 0 ? (received / total).clamp(0.0, 1.0) : 0);
        }
      } finally {
        await sink.close();
      }
      if (total > 0 && received != total) {
        throw HttpException('下载不完整：$received/$total', uri: Uri.parse(url));
      }
    } finally {
      client.close();
    }
  }

  static Future<void> _installWindows(File setup) async {
    final exe = Platform.resolvedExecutable;
    final dir = File(exe).parent.path;
    final script = File('${setup.parent.path}${Platform.pathSeparator}install.ps1');
    final content = '''
Start-Sleep -Seconds 2
Unblock-File -Path '${_psEscape(setup.path)}' -ErrorAction SilentlyContinue
\$installArgs = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/CLOSEAPPLICATIONS','/DIR="${_psEscape(dir)}"')
Start-Process -FilePath '${_psEscape(setup.path)}' -ArgumentList \$installArgs -Wait
Start-Process -FilePath '${_psEscape(exe)}'
''';
    await _writeScript(script, content);
    await Process.start(
      'powershell.exe',
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', script.path],
      mode: ProcessStartMode.detached,
    );
  }

  static Future<void> _installMacos(File dmg) async {
    final exe = Platform.resolvedExecutable;
    final marker = exe.indexOf('.app/');
    if (marker < 0) throw StateError('找不到 .app 路径，无法自动更新');
    final appPath = exe.substring(0, marker + 4);
    final appDir = File(appPath).parent.path;
    final writable = await Process.run('test', ['-w', appDir]);
    if (writable.exitCode != 0) {
      throw FileSystemException('没有写入 $appDir 的权限，请手动安装', appPath);
    }

    final script = File('${dmg.parent.path}${Platform.pathSeparator}install.sh');
    final content = '''
#!/bin/sh
sleep 2
set -e
MOUNT="\$(mktemp -d)"
hdiutil attach "${_shEscape(dmg.path)}" -nobrowse -quiet -mountpoint "\$MOUNT"
rm -rf "${_shEscape(appPath)}"
ditto "\$MOUNT/tonicuisc.app" "${_shEscape(appPath)}"
xattr -dr com.apple.quarantine "${_shEscape(appPath)}" || true
hdiutil detach "\$MOUNT" -quiet || true
open "${_shEscape(appPath)}"
''';
    await _writeScript(script, content);
    await Process.start('/bin/sh', [script.path], mode: ProcessStartMode.detached);
  }

  static Future<void> _installLinux(File archive) async {
    final exe = Platform.resolvedExecutable;
    final appDir = File(exe).parent.path;
    final writable = await Process.run('test', ['-w', appDir]);
    if (writable.exitCode != 0) {
      throw FileSystemException('没有写入 $appDir 的权限，请手动安装', appDir);
    }

    final script = File('${archive.parent.path}${Platform.pathSeparator}install.sh');
    final content = '''
#!/bin/sh
sleep 2
set -e
TMP="\$(mktemp -d)"
tar -xzf "${_shEscape(archive.path)}" -C "\$TMP"
cp -a "\$TMP/bundle/." "${_shEscape(appDir)}/"
nohup "${_shEscape(exe)}" >/dev/null 2>&1 &
''';
    await _writeScript(script, content);
    await Process.start('/bin/sh', [script.path], mode: ProcessStartMode.detached);
  }

  static Future<void> _writeScript(File file, String content) async {
    // PowerShell 5.1 需要 BOM 才按 UTF-8 读；/bin/sh 不介意 BOM，但别给它。
    if (Platform.isWindows) {
      final bytes = <int>[0xEF, 0xBB, 0xBF, ...utf8.encode(content)];
      await file.writeAsBytes(bytes, flush: true);
    } else {
      await file.writeAsString(content, flush: true);
    }
  }

  static String _psEscape(String value) => value.replaceAll("'", "''");

  static String _shEscape(String value) =>
      value.replaceAll(r'\', r'\\').replaceAll('"', r'\"').replaceAll(r'$', r'\$').replaceAll('`', r'\`');
}
