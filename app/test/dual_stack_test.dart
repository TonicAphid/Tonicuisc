import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:tonicuisc/core/dual_stack.dart';
import 'package:tonicuisc/core/server_config.dart';

/// 假 socket：只用到 `destroy()`，其余成员靠 noSuchMethod 兜住。
class _FakeSocket implements Socket {
  bool destroyed = false;

  @override
  void destroy() {
    destroyed = true;
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

InternetAddress _v6() => InternetAddress('240e:1:2:3::1');
InternetAddress _v4() => InternetAddress('1.2.3.4');

/// 立刻连上的假连接。
Future<ConnectionTask<Socket>> _connected(Socket socket) async =>
    ConnectionTask.fromSocket(Future<Socket>.value(socket), () {});

/// 永远连不上的假连接（用来模拟 v6 黑洞）。
Future<ConnectionTask<Socket>> _hang() async =>
    ConnectionTask.fromSocket(Completer<Socket>().future, () {});

void main() {
  group('orderAddresses', () {
    test('IPv6 排到前面，同族内保持原顺序', () {
      final v4a = InternetAddress('1.1.1.1');
      final v4b = InternetAddress('2.2.2.2');
      final v6a = InternetAddress('240e::1');
      final v6b = InternetAddress('240e::2');
      expect(
        orderAddresses([v4a, v6a, v4b, v6b]).map((e) => e.address).toList(),
        [v6a.address, v6b.address, v4a.address, v4b.address],
      );
    });

    test('只有一族时原样返回', () {
      final v4 = [InternetAddress('1.1.1.1'), InternetAddress('2.2.2.2')];
      expect(orderAddresses(v4), v4);
      expect(orderAddresses(const []), isEmpty);
    });

    test('preferV6: false 时 IPv4 排前面（留给第二轮的）', () {
      final v4 = InternetAddress('1.1.1.1');
      final v6 = InternetAddress('240e::1');
      expect(
        orderAddresses([v6, v4], preferV6: false).map((e) => e.address).toList(),
        [v4.address, v6.address],
      );
    });
  });

  group('needsOwnTls（装了 connectionFactory 之后 TLS 得自己发）', () {
    test('直连 https：要自己握手', () {
      expect(needsOwnTls(Uri.parse('https://uisc.tonicaphid.cn/api/health'), null), isTrue);
    });

    test('http：不用', () {
      expect(needsOwnTls(Uri.parse('http://192.168.5.43:8000/api/health'), null), isFalse);
    });

    test('走代理的 https：不用（dart:io 自己发 CONNECT 再握手）', () {
      expect(needsOwnTls(Uri.parse('https://uisc.tonicaphid.cn/api/health'), '127.0.0.1'), isFalse);
    });

    test('App 的默认地址必须落进「自己握手」这一档', () {
      // 这条判错的下场就是这个 bug：请求明文打到 443，
      // 家里 v6 回 400、frp 节点直接关连接（Connection closed before full header was received）
      final url = Uri.parse(kDefaultServerUrl);
      expect(url.isScheme('https'), isTrue);
      expect(needsOwnTls(url, null), isTrue);
    });
  });

  group('raceConnections（Happy Eyeballs）', () {
    test('v6 先连上：不会再去动 v4', () async {
      final v6 = _v6();
      final v4 = _v4();
      final socket = _FakeSocket();
      final tried = <String>[];

      final winner = await raceConnections(
        [v6, v4],
        8443,
        stagger: const Duration(milliseconds: 20),
        connector: (host, port) async {
          tried.add('$host');
          return '$host' == '$v6' ? _connected(socket) : _hang();
        },
      );

      expect(winner, same(socket));
      expect(tried, ['$v6']); // v4 根本没被连过
    });

    test('v6 卡住：头筹时间一到 v4 接上', () async {
      final v6 = _v6();
      final v4 = _v4();
      final socket = _FakeSocket();
      final tried = <String>[];

      final winner = await raceConnections(
        [v6, v4],
        8443,
        stagger: const Duration(milliseconds: 20),
        connector: (host, port) async {
          tried.add('$host');
          return '$host' == '$v6' ? _hang() : _connected(socket);
        },
      );

      expect(winner, same(socket));
      expect(tried, ['$v6', '$v4']);
    });

    test('v6 立刻报错：不等头筹时间，直接试 v4', () async {
      final v6 = _v6();
      final v4 = _v4();
      final socket = _FakeSocket();
      final watch = Stopwatch()..start();

      final winner = await raceConnections(
        [v6, v4],
        8443,
        // 头筹给到 5 秒：真要是等了，这个测试会明显变慢
        stagger: const Duration(seconds: 5),
        connector: (host, port) async {
          if ('$host' == '$v6') throw const SocketException('no route to host');
          return _connected(socket);
        },
      );
      watch.stop();

      expect(winner, same(socket));
      expect(watch.elapsed, lessThan(const Duration(seconds: 1)));
    });

    test('所有地址都连不上：把错误抛出去', () async {
      await expectLater(
        raceConnections(
          [_v6(), _v4()],
          8443,
          stagger: const Duration(milliseconds: 20),
          connector: (host, port) async => throw SocketException('连不上 $host'),
        ),
        throwsA(isA<SocketException>()),
      );
    });

    test('落选的连接会被取消，赢家不会被取消', () async {
      final v6 = _v6();
      final v4 = _v4();
      final socket = _FakeSocket();
      var v6Cancelled = false;
      var winnerCancelled = false;
      final hang = Completer<Socket>();

      final winner = await raceConnections(
        [v6, v4],
        8443,
        stagger: const Duration(milliseconds: 20),
        connector: (host, port) async {
          if ('$host' == '$v6') {
            return ConnectionTask.fromSocket(hang.future, () => v6Cancelled = true);
          }
          return ConnectionTask.fromSocket(
            Future<Socket>.value(socket),
            () => winnerCancelled = true,
          );
        },
      );

      expect(winner, same(socket));
      expect(v6Cancelled, isTrue); // 黑洞那条被掐掉了
      expect(winnerCancelled, isFalse); // 赢家要留给 HttpClient 用
    });
  });

  group('attemptBothFamilies（握手失败就换一族整套重来）', () {
    test('v6 握手失败 → 换 v4 优先再来一遍，用 v4 那条', () async {
      final orders = <List<InternetAddress>>[];
      final v4Socket = _FakeSocket();

      final socket = await attemptBothFamilies(
        [_v6(), _v4()],
        attemptAll: (ordered) async {
          orders.add(ordered);
          if (ordered.first.type == InternetAddressType.IPv6) {
            // 中间盒的典型手法：TCP 放你过，握手阶段打 RST
            // （本项目在大陆节点上实测到的就是这种，见 README / free-frp-relay.md）
            throw const HandshakeException('Connection terminated during handshake');
          }
          return v4Socket;
        },
      );

      expect(socket, same(v4Socket));
      expect(orders, hasLength(2));
      expect(orders[0].first.address, _v6().address); // 第一轮 v6 占头筹
      expect(orders[1].first.address, _v4().address); // 第二轮换 v4 占头筹
    });

    test('v6 一次就成：绝不跑第二轮', () async {
      final v6Socket = _FakeSocket();
      var rounds = 0;

      final socket = await attemptBothFamilies(
        [_v6(), _v4()],
        attemptAll: (ordered) async {
          rounds++;
          return v6Socket;
        },
      );

      expect(socket, same(v6Socket));
      expect(rounds, 1);
    });

    test('只有一族：没得换，原样抛出', () async {
      var rounds = 0;
      await expectLater(
        attemptBothFamilies(
          [_v6(), InternetAddress('240e::9')],
          attemptAll: (ordered) async {
            rounds++;
            throw const HandshakeException('boom');
          },
        ),
        throwsA(isA<HandshakeException>()),
      );
      expect(rounds, 1);
    });

    test('两族都不行：抛首选那条路的错', () async {
      await expectLater(
        attemptBothFamilies(
          [_v6(), _v4()],
          attemptAll: (ordered) async {
            if (ordered.first.type == InternetAddressType.IPv6) {
              throw const HandshakeException('v6 被掐');
            }
            throw const SocketException('v4 也不通');
          },
        ),
        // 报第一条（v6）的错，而不是 v4 的 SocketException —— 首选那条更有诊断价值
        throwsA(isA<HandshakeException>()),
      );
    });
  });
}
