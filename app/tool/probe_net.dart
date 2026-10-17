// 网络探针：连不上服务器时**先跑它**，一眼看出是哪条路断了。
//
//   dart run tool/probe_net.dart          # 四条路全测（推荐）
//   dart run tool/probe_net.dart v6       # 只测某一条
//
// 四条路：
//   none  —— 原生 HttpClient，不装 connectionFactory（对照组）
//   dual  —— **App 的真实路径**（dual_stack：v6 优先、250ms 回落 v4）
//   v6    —— 强制只连 AAAA（= 直连家里）
//   v4    —— 强制只连 A（= frp 节点 → 隧道）
//
// 强制那两档只换「连哪个 IP」，**SNI 仍然是域名** —— 不然 frp 节点没法按 SNI 路由。
// 全部通过时退出码 0，有失败时 1（可以直接塞进脚本）。
import 'dart:convert';
import 'dart:io';

import 'package:tonicuisc/core/dual_stack.dart';

const String host = 'uisc.tonicaphid.cn';
const String path = '/api/health';
const List<String> _allModes = ['none', 'dual', 'v6', 'v4'];

Future<void> main(List<String> args) async {
  final modes = args.isEmpty ? _allModes : args;
  var failed = 0;
  for (final mode in modes) {
    if (!await _probe(mode)) failed++;
  }
  print(failed == 0 ? '\n全部通过 ✓' : '\n$failed 条失败 ✗');
  if (failed > 0) exitCode = 1;
}

Future<bool> _probe(String mode) async {
  final client = HttpClient();

  if (mode == 'dual') {
    // 跟 App 里完全一样
    installDualStack(client);
  } else if (mode == 'v6' || mode == 'v4') {
    client.connectionFactory = (url, proxyHost, proxyPort) async {
      final want = mode == 'v6' ? InternetAddressType.IPv6 : InternetAddressType.IPv4;
      final picked = (await InternetAddress.lookup(url.host)).firstWhere(
        (a) => a.type == want,
        orElse: () => throw SocketException('$host 没有 ${want.name} 记录'),
      );
      final socket = await Socket.connect(picked, url.port);
      // 装了 factory 就得自己握手 —— 用 dual_stack 里那个判定，免得探针和 App 行为不一致
      return ConnectionTask.fromSocket(
        needsOwnTls(url, null) ? SecureSocket.secure(socket, host: url.host) : Future.value(socket),
        () {},
      );
    };
  }

  print('--- $mode ---');
  final watch = Stopwatch()..start();
  try {
    final request = await client.getUrl(Uri.https(host, path));
    final response = await request.close();
    final body = await response.transform(utf8.decoder).join();
    watch.stop();
    final info = response.connectionInfo;
    final ok = response.statusCode == 200;
    print('  ${ok ? '✓' : '✗'} status=${response.statusCode}  (${watch.elapsedMilliseconds} ms)');
    print('    连到 = ${info?.remoteAddress.address} [${info?.remoteAddress.type.name}]');
    if (!ok) print('    body = $body');
    client.close(force: true);
    return ok;
  } catch (err) {
    watch.stop();
    print('  ✗ ${watch.elapsedMilliseconds} ms：$err');
    client.close(force: true);
    return false;
  }
}
