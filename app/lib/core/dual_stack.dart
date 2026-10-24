/// 双栈连接策略：域名同时有 AAAA / A 时**优先走 IPv6**，连不上快速回落 IPv4。
///
/// 为什么自己接管：`dart:io` 默认把整个地址列表交给 `Socket.startConnect`，
/// 由系统按 RFC 6724 排序——顺序随平台 / 网络环境变，而且**一个地址连不通时
/// 是干等超时**，不会立刻换下一个。`uisc.tonicaphid.cn` 上两族走的正好是
/// **两条不同的路**：**AAAA = 家里的 IPv6（直连）**、**A = frp 节点（节点按 SNI
/// 送进隧道）**，客户端要的是「v6 优先走直连，v6 坏了 250ms 内回落到隧道」
/// （见 README 的「HTTPS」一节）。
///
/// 这里实现 RFC 8305（Happy Eyeballs）的最小版本：
///
/// 1. 解析出全部地址，**IPv6 排前面**（同族内保持系统给的顺序）；
/// 2. 先连第一个，[kHappyEyeballsStagger] 还没连上就把下一个也发出去；
/// 3. 谁先连上算谁，其余的取消；
/// 4. 该加密的**自己加密** —— ⚠️ 这个坑踩过：装了 `connectionFactory` 之后
///    dart:io **不再代劳 TLS**，返回裸 socket 就是明文请求（详见 [dualStackConnect]）。
///
/// 250ms 是 RFC 8305 建议的头筹时间：给 v6 一个先手，又不至于在 v6 坏掉的
/// 网络上白等几秒——手机从 WiFi 切到只有 v4 的蜂窝时最能感觉到。
library;

import 'dart:async';
import 'dart:io';

/// 给第一族的头筹时间（RFC 8305 建议 250ms）。
const Duration kHappyEyeballsStagger = Duration(milliseconds: 250);

/// 「连一个地址」这件事，抽成参数是为了测试能换成假网络。
typedef SocketConnector = Future<ConnectionTask<Socket>> Function(Object host, int port);

/// 排成「IPv6 优先」，同族内保持原顺序。
///
/// **稳定排序**：系统给的那份顺序里带着它自己的路由表判断，同族内重排反而
/// 可能挑到更差的地址，所以只做「分族」这一步。
///
/// [preferV6] 给 false 就把 IPv4 排前面 —— [attemptBothFamilies] 第二轮用得上
/// （v6 那条路连握手都被掐了，就别再让它占头筹）。
List<InternetAddress> orderAddresses(List<InternetAddress> addresses, {bool preferV6 = true}) {
  final v6 = <InternetAddress>[];
  final v4 = <InternetAddress>[];
  for (final address in addresses) {
    (address.type == InternetAddressType.IPv6 ? v6 : v4).add(address);
  }
  return preferV6 ? [...v6, ...v4] : [...v4, ...v6];
}

/// 给 [HttpClient] 装上双栈策略。
///
/// App 里所有 `dart:io` 的 HTTP 都走同一个 `HttpClient` 工厂
/// （`SelfSignedOverrides.createHttpClient`），在那里调一次就全覆盖了。
///
/// [allowBadCertificate] 就是「允许自签证书」那个开关（**每次握手现读**，中途改也生效）。
/// 必须传进来：装了 `connectionFactory` 之后 TLS 由我们自己握手，直连时
/// `client.badCertificateCallback` **根本不会被调用**（只有走代理那条路才会）。
void installDualStack(HttpClient client, {bool Function()? allowBadCertificate}) {
  client.connectionFactory = (url, proxyHost, proxyPort) => dualStackConnect(
        url,
        proxyHost,
        proxyPort,
        allowBadCertificate: allowBadCertificate,
      );
}

/// [HttpClient.connectionFactory] 的实现：签名由 dart:io 定死。
///
/// `proxyHost` / `proxyPort` 不为空说明这次要走代理，那就**必须**连代理本身，
/// 不能拿 url 的 host 去解析——否则代理等于没走（dart:io 也是这么约定的）。
///
/// ⚠️ **装了 `connectionFactory` 之后，TLS 得我们自己握手。**
/// `_http/http_impl.dart` 里是这么写的：
///
/// ```dart
/// if (cf != null) { connectionTask = cf(uri, host, port); }   // 拿到的 socket 直接用
/// else { connectionTask = isSecure && proxy.isDirect
///     ? SecureSocket.startConnect(...) : Socket.startConnect(...); }
/// ```
///
/// 它**完全信任你返回的 socket**，直接在上面写明文 HTTP。当初这里漏了握手，
/// 现象就是「请求明文打到 443」：家里 v6 那条回 `400 Client sent an HTTP request to an
/// HTTPS server.`，frp 节点那条直接把连接关掉
/// （`Connection closed before full header was received`）。
Future<ConnectionTask<Socket>> dualStackConnect(
  Uri url,
  String? proxyHost,
  int? proxyPort, {
  bool Function()? allowBadCertificate,
}) async {
  final host = proxyHost ?? url.host;
  final port = proxyPort ?? url.port;

  // ── 1) 地址字面量不用解析：用户填 IP 连内网自签就是这么用的（`https://192.168.5.43`）──
  final literal = InternetAddress.tryParse(host);
  if (literal != null) {
    final raw = await Socket.connect(literal, port);
    final only = await _secureIfNeeded(
      raw,
      url,
      proxyHost,
      allowBadCertificate: allowBadCertificate,
    );
    return ConnectionTask.fromSocket(Future<Socket>.value(only), () {});
  }

  final addresses = await InternetAddress.lookup(host);
  if (addresses.isEmpty) {
    throw SocketException('域名解析不出任何地址：$host');
  }

  // ── 2) 双栈尝试（含握手）；握手被掐就换一族整套重来 ─────────────────────
  final ready = await attemptBothFamilies(addresses, attemptAll: (ordered) async {
    // 只有一族就不用赛跑，省掉一次多余的分支
    final raw = ordered.length == 1
        ? await Socket.connect(ordered.first, port)
        : await raceConnections(ordered, port);
    // 该加密的自己加密（装了 connectionFactory 之后 dart:io 不代劳）
    return _secureIfNeeded(raw, url, proxyHost, allowBadCertificate: allowBadCertificate);
  });

  // 走到这里所有落选的连接都已经取消过了，所以 onCancel 不用做事
  return ConnectionTask.fromSocket(Future<Socket>.value(ready), () {});
}

/// 「v6 优先」并不够：**TCP 连得上 ≠ 这条路能用**。
///
/// 中间盒很常见的做法是「TCP 放你过、TLS 握手阶段打 RST」—— 本项目在大陆节点上
/// 实测到的就是这种（TCP 通、`openssl` 能握手、`dart:io` 被 RST）。
/// 所以**握手失败时，把两个家族换个顺序整套再来一遍**，而不是就此认输。
///
/// 抽成独立函数是为了能测：[attemptAll] 接一组**已排好序**的地址，返回「连上且握手
/// 完成」的那条 socket。测试里换成假实现，就能造出「v6 握手失败、v4 成功」这种
/// 真机上很难复现的现场。
Future<Socket> attemptBothFamilies(
  List<InternetAddress> addresses, {
  required Future<Socket> Function(List<InternetAddress> ordered) attemptAll,
}) async {
  final ordered = orderAddresses(addresses);
  try {
    return await attemptAll(ordered);
  } on TlsException catch (first) {
    // 只有一族就没得换，原样抛
    final hasV6 = addresses.any((a) => a.type == InternetAddressType.IPv6);
    final hasV4 = addresses.any((a) => a.type == InternetAddressType.IPv4);
    if (!hasV6 || !hasV4) rethrow;

    try {
      return await attemptAll(orderAddresses(addresses, preferV6: false));
    } catch (_) {
      // 两条都不行：报**首选那条路**的错（更有诊断价值）
      throw first;
    }
  }
}

/// 装了 `connectionFactory` 之后，这条连接要不要**我们自己**做 TLS 握手。
///
/// 判定跟 dart:io 内部分支一致：
///
/// - **走代理** → 不用（返回裸 socket，dart:io 会自己发 `CONNECT` 再握手，别抢它的活）；
/// - **直连 https** → **要**（它不代劳，不握手就是明文）；
/// - **http** → 不用。
///
/// 抽成纯函数是为了能测：这个判断漏掉过一次（请求明文打到 443），
/// 而它在真机上第一眼很难看出来。
bool needsOwnTls(Uri url, String? proxyHost) => proxyHost == null && url.isScheme('https');

Future<Socket> _secureIfNeeded(
  Socket socket,
  Uri url,
  String? proxyHost, {
  bool Function()? allowBadCertificate,
}) {
  if (!needsOwnTls(url, proxyHost)) return Future<Socket>.value(socket);
  final allow = allowBadCertificate;
  // SNI / 证书校验用的都必须是**域名**（`url.host`），不能是 IP ——
  // frp 节点要靠 SNI 路由，证书也是按域名签的。
  return SecureSocket.secure(
    socket,
    host: url.host,
    onBadCertificate: allow == null ? null : (cert) => allow(),
  );
}

/// Happy Eyeballs 主体：错开启动多个连接，返回**先连上**的那个 socket。
///
/// [connector] / [stagger] 是给测试留的口子（默认就是真网络 + 250ms）。
///
/// 失败判定：全部地址都发出去过、而且都失败了，才把最后一个错误抛出去——
/// 中间任何一个连上就立刻返回，别的连不连得上都不影响。
Future<Socket> raceConnections(
  List<InternetAddress> addresses,
  int port, {
  SocketConnector? connector,
  Duration stagger = kHappyEyeballsStagger,
}) {
  final connect = connector ?? Socket.startConnect;
  final completer = Completer<Socket>();
  final live = <ConnectionTask<Socket>>[];
  final timers = <Timer>[];
  var next = 0; // 下一个要试的下标（也等于「已经发出去过几个」）
  var failed = 0; // 已经失败的个数
  var settled = false;
  Object? lastError;

  /// 有结果了：停掉还没到点的头筹计时器。
  void settle() {
    settled = true;
    for (final timer in timers) {
      timer.cancel();
    }
    timers.clear();
  }

  void failIfExhausted() {
    if (settled) return;
    if (next < addresses.length) return; // 还有没试过的地址
    if (failed < next) return; // 还有在飞的连接
    settle();
    completer.completeError(lastError ?? const SocketException('所有地址都连不上'));
  }

  void startNext() {
    if (settled || next >= addresses.length) return;
    final address = addresses[next++];
    connect(address, port).then((task) {
      if (settled) {
        // 已经有赢家了，这个白起了
        task.cancel();
        failed++;
        failIfExhausted();
        return;
      }
      live.add(task);
      task.socket.then((socket) {
        if (settled) {
          socket.destroy();
          return;
        }
        // 先标记再取消：落选的那些被取消时会带着 SocketException 回来，
        // 上面两个 onError 靠 settled 判断「别理它」。
        settle();
        for (final other in live) {
          if (!identical(other, task)) other.cancel();
        }
        live.clear();
        completer.complete(socket);
      }, onError: (Object error) {
        failed++;
        lastError = error;
        startNext(); // 这个地址不通，立刻试下一个，不等头筹时间
        failIfExhausted();
      });
    }, onError: (Object error) {
      failed++;
      lastError = error;
      startNext();
      failIfExhausted();
    });

    // 头筹时间到还没连上，就把下一族也发出去（v6 挂了时 v4 只慢 250ms）
    if (next < addresses.length) {
      timers.add(Timer(stagger, startNext));
    }
  }

  startNext();
  return completer.future;
}
