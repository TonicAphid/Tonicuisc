import 'dart:convert';
import 'dart:io';

import 'package:http/http.dart' as http;

/// 下载桌面安装包 → 交给一个「脱离当前进程」的安装脚本 → 静默安装并重启。
///
/// ## 为什么 Windows 上不能用 `Process.start(detached)` + `exit(0)`
///
/// 实测（2026-10-24，v0.2.7 → v0.2.9）：安装包下载完整、`install.ps1` 也写出来了，
/// 但点完「下载并安装」App 直接消失，安装程序**根本没启动**。原因不是权限，而是：
/// 父进程 `exit(0)` 时，detached 出来的 PowerShell 被一起收掉了（进程在同一个
/// 作业对象里），它连脚本第一行都没跑到。用 WMI 的 `Win32_Process.Create` 创建
/// 就不一样：进程归 WMI 服务管，不在本进程的作业对象里，能一直活到安装结束。
///
/// ## 流程（Windows）
///
/// 1. 下载到 `%LOCALAPPDATA%\Tonicuisc\update\`（不放 `%TEMP%`，它的 ACL 会被
///    各种沙箱工具改坏）；
/// 2. 写 `install.ps1`：等 2 秒 → 用 `-Verb RunAs` 弹 UAC → 静默安装 → 写结果 →
///    重新打开 App；
/// 3. 用 WMI 启动这个脚本，然后 App 自己退出。
///
/// 安装脚本是普通用户权限，只有安装程序本身要管理员（UAC 那一下）。
/// 失败信息写在 `last-result.txt`，App 下次启动时由 `consumeResult()` 读出来提示。
class UpdateInstaller {
  UpdateInstaller._();

  static const _appDirName = 'Tonicuisc';
  static const _updateDirName = 'update';
  static const _resultFileName = 'last-result.txt';

  /// 下载并安装。成功启动安装脚本后本进程会 `exit(0)`，函数不会返回。
  ///
  /// [fallbackUrl] 是镜像失败时的直连地址；[expectedSize] 用来校验下载完整性
  /// （服务端 `/api/update` 会给 `asset.size`）。
  static Future<void> downloadAndInstall({
    required String url,
    String? fallbackUrl,
    required String fileName,
    int expectedSize = 0,
    required void Function(double progress) onProgress,
  }) async {
    final dir = await _prepareDirectory();
    final target = File('${dir.path}${Platform.pathSeparator}${_safeName(fileName)}');
    try {
      await _download(url, target, onProgress, expectedSize);
    } catch (first) {
      final alt = (fallbackUrl ?? '').trim();
      if (alt.isEmpty || alt == url) rethrow;
      // 镜像挂了就换直连再试一次（进度条归零重新走）
      onProgress(0);
      await _download(alt, target, onProgress, expectedSize);
    }

    if (Platform.isWindows) {
      await _installWindows(target, dir);
    } else if (Platform.isMacOS) {
      await _installMacos(target);
    } else if (Platform.isLinux) {
      await _installLinux(target);
    } else {
      throw UnsupportedError('当前平台不支持自动安装');
    }
    // 安装脚本已经脱离（Windows 走 WMI，其它平台 detached），这里结束旧进程。
    exit(0);
  }

  /// 上一次自动更新的结果（只在上次失败时有内容）。读完即删。
  ///
  /// 场景：UAC 被取消 / 安装程序报错 → 安装脚本把原因写进文件并把旧 App 重新拉起来，
  /// 这里把它取出来给用户一个明确的提示，而不是「更新了一下，怎么还是老版本」。
  static Future<String?> consumeResult() async {
    if (!Platform.isWindows) return null;
    final file = File('${_baseDirectory().path}${Platform.pathSeparator}$_resultFileName');
    try {
      if (!file.existsSync()) return null;
      final text = await file.readAsString();
      await file.delete();
      final trimmed = text.trim();
      if (trimmed.isEmpty || trimmed == 'ok' || trimmed.startsWith('ok ')) return null;
      return trimmed;
    } catch (_) {
      return null;
    }
  }

  static Directory _baseDirectory() {
    final local = Platform.environment['LOCALAPPDATA'];
    if (Platform.isWindows && local != null && local.isNotEmpty) {
      return Directory(
        '$local${Platform.pathSeparator}$_appDirName${Platform.pathSeparator}$_updateDirName',
      );
    }
    return Directory('${Directory.systemTemp.path}${Platform.pathSeparator}tonicuisc_update');
  }

  /// 每次更新前清空目录：旧安装包 / 旧日志留着没用，还会越堆越多。
  static Future<Directory> _prepareDirectory() async {
    final base = _baseDirectory();
    if (await base.exists()) {
      await for (final entity in base.list()) {
        try {
          await entity.delete(recursive: true);
        } catch (_) {
          // 删不掉就算了，不影响下载
        }
      }
    } else {
      await base.create(recursive: true);
    }
    return base;
  }

  static String _safeName(String name) {
    final cleaned = name.replaceAll(RegExp(r'[\\/:*?"<>|]'), '_').trim();
    return cleaned.isEmpty ? 'tonicuisc-update' : cleaned;
  }

  static Future<void> _download(
    String url,
    File target,
    void Function(double progress) onProgress,
    int expectedSize,
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
      // 有些镜像不给 Content-Length（chunked），用服务端给的文件大小兜底：
      // 不然一个被截断的 exe 会被当成下载成功，安装时静默失败。
      if (expectedSize > 0) {
        final actual = await target.length();
        if (actual != expectedSize) {
          throw HttpException(
            '安装包大小不对：$actual/$expectedSize（镜像可能返回了错误页）',
            uri: Uri.parse(url),
          );
        }
      }
    } finally {
      client.close();
    }
  }

  static Future<void> _installWindows(File setup, Directory dir) async {
    final exe = Platform.resolvedExecutable;
    final installDir = File(exe).parent.path;
    final script = File('${dir.path}${Platform.pathSeparator}install.ps1');
    final log = File('${dir.path}${Platform.pathSeparator}setup.log');
    final result = File('${dir.path}${Platform.pathSeparator}$_resultFileName');

    // 参数拼成一整串交给 Start-Process，避免 PowerShell 对数组里的引号再加工。
    final args = [
      '/VERYSILENT',
      '/SUPPRESSMSGBOXES',
      '/NORESTART',
      '/CLOSEAPPLICATIONS',
      '/DIR="$installDir"',
      '/LOG="${log.path}"',
    ].join(' ');
    final setupBaseName = setup.uri.pathSegments.last.replaceAll(RegExp(r'\.exe$'), '');
    final systemRoot = Platform.environment['SystemRoot'] ?? r'C:\Windows';
    final powershell =
        '$systemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe';

    final content = '''
\$ErrorActionPreference = 'Stop'
# 等旧 App 完全退出，文件锁释放
Start-Sleep -Seconds 2
try {
  # -Verb RunAs：UAC 就在这里弹（普通用户权限的脚本 → 管理员权限的安装程序）
  \$p = Start-Process -FilePath '${_psEscape(setup.path)}' -ArgumentList '${_psEscape(args)}' -Verb RunAs -PassThru
  if (\$p) {
    \$p.WaitForExit()
    \$code = \$p.ExitCode
  } else {
    # 极少数 PowerShell 版本拿不到 RunAs 的进程句柄，按进程名等它结束
    \$deadline = (Get-Date).AddMinutes(5)
    while ((Get-Process -Name '${_psEscape(setupBaseName)}' -ErrorAction SilentlyContinue) -and ((Get-Date) -lt \$deadline)) {
      Start-Sleep -Milliseconds 500
    }
    \$code = 0
  }
  if (\$code -eq 0) {
    'ok' | Out-File -Encoding utf8 '${_psEscape(result.path)}'
  } else {
    "failed 安装程序退出码 \$code（日志：${_psEscape(log.path)}）" | Out-File -Encoding utf8 '${_psEscape(result.path)}'
  }
} catch {
  "failed \$(\$_.Exception.Message)（日志：${_psEscape(log.path)}）" | Out-File -Encoding utf8 '${_psEscape(result.path)}'
}
Start-Sleep -Seconds 1
# 脚本是普通用户权限，这里拉起来的也是普通用户权限的 App
Start-Process -FilePath '${_psEscape(exe)}'
''';
    await _writeScript(script, content);

    // 关键一步：用 WMI 创建，脱离本进程的作业对象。见类注释。
    final commandLine =
        '"$powershell" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "${script.path}"';
    final psCommand =
        'Invoke-CimMethod -ClassName Win32_Process -MethodName Create '
        "-Arguments @{ CommandLine = '${_psEscape(commandLine)}' } | "
        'Select-Object -ExpandProperty ReturnValue';
    final outcome = await Process.run(powershell, [
      '-NoProfile',
      '-NonInteractive',
      '-ExecutionPolicy',
      'Bypass',
      '-Command',
      psCommand,
    ]);
    final code = '${outcome.stdout}'.trim();
    if (outcome.exitCode != 0 || code != '0') {
      throw StateError(
        '启动安装脚本失败（WMI 返回 ${code.isEmpty ? outcome.exitCode : code}）：'
        '${outcome.stderr}'.trim(),
      );
    }
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
