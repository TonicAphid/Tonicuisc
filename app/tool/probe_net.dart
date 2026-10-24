// 网络探针：连不上服务器时**先跑它**，一眼看出是哪条路断了。
//
//   dart run tool/probe_net.dart          # 全测（推荐）
//   dart run tool/probe_net.dart v6       # 只测某一条
//
// 先跑的是一条「备案体检」（明文 HTTP 打 A 记录那条路的 80 端口）：
// 阿里云对**未备案域名**会在 80 上直接回一张 `Non-compliance ICP Filing` 的 403 页。
// 看到它 = 「证书没问题、客户端没问题，是域名在大陆节点上被拦了」。
//
// 然后四条路：
//   none  —— 原生 HttpClient，不装 connectionFactory（对照组）
//   dual  —— **App 的真实路径**（dual_stack：v6 优先、250ms 回落 v4、握手被掐再换一族）
//   v6    —— 强制只连 AAAA（= 直连家里）
//   v4    —— 强制只连 A（= 中转节点 → 隧道）
//
// 强制那两档只换「连哪个 IP」，**SNI 仍然是域名** —— 不然节点没法按 SNI 路由。
// 全部通过时退出码 0，有失败时 1（可以直接塞进脚本）。
import 'dart:convert';
import 'dart:io';

import 'package:tonicuisc/core/dual_stack.dart';

const String host = 'uisc.tonicaphid.cn';
const String path = '/api/health';
const List<String> _allModes = ['none', 'dual', 'v6', 'v4'];

Future<void> main(List<String> args) async {
  await _printDns();
  await _checkIcpFiling();

  final modes = args.isEmpty ? _allModes : args;
  var failed = 0;
  for (final mode in modes) {
    if (!await _probe(mode)) failed++;
  }
  print(failed == 0 ? '\n全部通过 ✓' : '\n$failed 条失败 ✗');
  if (failed > 0) exitCode = 1;
}

/// 先把 A / AAAA 摊开：后面每条连接「走的哪条路」就是跟这两个对照出来的。
Future<void> _printDns() async {
  print('--- DNS 现状 ---');
  try {
    for (final record in await InternetAddress.lookup(host)) {
      final kind = record.type == InternetAddressType.IPv6 ? 'AAAA' : 'A   ';
      print('  $kind ${record.address}');
    }
  } catch (err) {
    print('  解析失败：$err');
  }
}

/// 备案体检：明文 HTTP :80，只走 A 记录那个 IP（`Host` 用域名）。
///
/// 这是「v4 不通」最常见的两种原因的分水岭：
/// - 回 `403 + Non-compliance ICP Filing` → **备案拦截**（不是证书、不是客户端）
/// - 连不上 / 没回拦截页 → 更可能是节点挂了或没开 80
Future<void> _checkIcpFiling() async {
  print('--- 备案体检（明文 HTTP :80）---');
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 10);
  try {
    final v4 = (await InternetAddress.lookup(host))
        .where((a) => a.type == InternetAddressType.IPv4)
        .toList();
    if (v4.isEmpty) {
      print('  ？ A 记录解析不出来，跳过');
      return;
    }
    // 明文 HTTP 不用握手，直接把 socket 钉到 A 记录那个 IP
    client.connectionFactory = (url, proxyHost, proxyPort) => Socket.startConnect(v4.first, 80);
    final response = await (await client.getUrl(Uri.parse('http://$host/'))).close();
    final body = await response.transform(utf8.decoder).join();
    final blocked = response.statusCode == 403 && body.contains('ICP Filing');
    if (blocked) {
      print('  ✗ status=403 阿里云拦截页 —— **域名未备案，大陆节点上 v4 会被 RST**');
      print('    （证书和客户端都没问题，见 deploy/free-frp-relay.md「大陆节点会被备案拦截」）');
    } else {
      print('  ✓ status=${response.statusCode} 没看到备案拦截页');
    }
  } catch (err) {
    print('  ？ 80 端口没给出有效响应（${err.runtimeType}）—— 没有拦截页，可能只是没开 80');
  } finally {
    client.close(force: true);
  }
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
    final remote = info?.remoteAddress;
    print('    连到 = ${remote?.address} [${remote?.type.name}]');
    if (remote != null) print('    ${await _describePath(remote)}');
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

/// 说人话版：这条连接到底走的哪条路 —— 跟 A / AAAA 对照出来的。
///
/// 「浏览器能开、App 不能」这种争论靠这一行定性：**连到 AAAA = 直连家里（没过节点）**，
/// 连到 A = 走中转节点 → frp 隧道。换句话说：**App 能连上 ≠ 那条 frp 能用**。
Future<String> _describePath(InternetAddress remote) async {
  try {
    final records = await InternetAddress.lookup(host);
    final v6 = records.where((a) => a.type == InternetAddressType.IPv6).map((a) => a.address);
    final v4 = records.where((a) => a.type == InternetAddressType.IPv4).map((a) => a.address);
    if (v6.contains(remote.address)) return '→ 走的是 AAAA：直连家里（没经过中转节点）';
    if (v4.contains(remote.address)) return '→ 走的是 A：中转节点 → frp 隧道';
    return '→ 这个 IP 既不是 A 也不是 AAAA（被中间盒改了？）';
  } catch (_) {
    return '';
  }
}
