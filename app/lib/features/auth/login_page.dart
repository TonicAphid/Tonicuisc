import 'dart:async';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../core/api_errors.dart';
import '../../core/credentials.dart';
import '../../core/ime_debug.dart';
import '../../core/server_config.dart';
import 'device_login.dart';

/// 默认设备名，用来在服务端辨认。
String defaultDeviceName() {
  if (Platform.isIOS) return 'iPhone';
  if (Platform.isMacOS) return 'Mac';
  if (Platform.isWindows) return 'Windows';
  if (Platform.isLinux) return 'Linux';
  return 'Device';
}

/// 设备码登录页。
///
/// 进页面就自动登记设备码并开始轮询；点「打开登录页并复制设备码」会
/// 直接弹出系统浏览器并把设备码放进剪贴板，用户粘一下就能登录。
///
/// 轮询是**自适应**的（见 [PollSchedule]）：刚登记时 1 秒一次，越等越慢；
/// 连续几次失败会把原因显示出来（而不是永远停在「等待批准…」）。
class LoginPage extends StatefulWidget {
  const LoginPage({
    super.key,
    required this.baseUrl,
    required this.onLoggedIn,
    this.initialDeviceName,
  });

  final String baseUrl;
  final String? initialDeviceName;
  final Future<void> Function(DeviceLoginResult result) onLoggedIn;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  /// 去掉看混的 I / O / 0 / 1，和服务端 `CODE_ALPHABET` 保持一致
  static const String _alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  static const PollSchedule _schedule = PollSchedule();

  /// 连续失败几次才把错误摆到界面上（网络抖一下不值得吓用户）
  static const int _failuresBeforeNotice = 3;

  final Random _random = Random.secure();
  late final TextEditingController _name =
      TextEditingController(text: widget.initialDeviceName ?? defaultDeviceName());

  Timer? _poller;
  late String _userCode = _newUserCode();
  late String _pollSecret = _newSecret();

  bool _registered = false;
  bool _starting = false;
  String? _error;
  int _attempt = 0;
  int _failures = 0;
  int _expiresIn = 0;
  DateTime? _startedAt;

  @override
  void initState() {
    super.initState();
    _begin(); // 进页面就登记，用户只需要去浏览器点一下
    // 对照组：这里是 Material 的 TextField，搜索页是 Cupertino 的。两边都打日志，
    // 就能分清「只有 Cupertino 控件坏」还是「整个引擎在这台机器上都收不到组字」。
    watchIme(_name, 'login');
  }

  @override
  void dispose() {
    _poller?.cancel();
    _name.dispose();
    super.dispose();
  }

  String _randomString(int length, String alphabet) =>
      List.generate(length, (_) => alphabet[_random.nextInt(alphabet.length)]).join();

  String _newUserCode() {
    final raw = _randomString(8, _alphabet);
    return '${raw.substring(0, 4)}-${raw.substring(4)}';
  }

  String _newSecret() => _randomString(32, 'abcdefghijklmnopqrstuvwxyz0123456789');

  String get _loginUrl => '${normalizeBaseUrl(widget.baseUrl)}/login';

  /// 登记设备码；成功返回 true。
  ///
  /// 失败时**换一个新码**但保持「未登记」状态：用户点复制 / 打开登录页时会再试一次，
  /// 不会拿到一个服务端根本不认识的码（以前失败后界面上摆的就是这种码）。
  Future<bool> _begin() async {
    if (_starting) return false;
    setState(() {
      _starting = true;
      _error = null;
    });
    try {
      // 本机存过设备 ID 就带上：服务端会就地复用旧记录（换新 key），不再重复建档
      final oldDeviceId = await Credentials.deviceId();
      final started = await startDeviceLogin(
        baseUrl: widget.baseUrl,
        userCode: _userCode,
        pollSecret: _pollSecret,
        deviceName: _name.text.trim(),
        deviceId: oldDeviceId ?? '',
      );
      if (!mounted) return false;
      setState(() {
        _registered = true;
        _expiresIn = started.expiresIn;
        _startedAt = DateTime.now();
        _attempt = 0;
        _failures = 0;
      });
      _startPolling();
      return true;
    } catch (err) {
      if (!mounted) return false;
      setState(() {
        _error = friendlyError(err, widget.baseUrl);
        _registered = false;
        _userCode = _newUserCode();
        _pollSecret = _newSecret();
      });
      return false;
    } finally {
      if (mounted) setState(() => _starting = false);
    }
  }

  void _reset() {
    _poller?.cancel();
    setState(() {
      _userCode = _newUserCode();
      _pollSecret = _newSecret();
      _registered = false;
      _error = null;
      _startedAt = null;
      _attempt = 0;
      _failures = 0;
    });
    _begin();
  }

  void _startPolling() {
    _poller?.cancel();
    _scheduleNext();
  }

  /// 按 [PollSchedule] 排下一次；不是固定间隔的定时器。
  void _scheduleNext() {
    _poller?.cancel();
    _poller = Timer(_schedule.delay(_attempt), _poll);
  }

  /// 这一轮结束了：停轮询并把原因显示在状态行上。
  void _finish(String message) {
    _poller?.cancel();
    if (!mounted) return;
    setState(() {
      _registered = false;
      _error = message;
    });
  }

  Future<void> _poll() async {
    try {
      final status = await pollDeviceLogin(
        baseUrl: widget.baseUrl,
        userCode: _userCode,
        pollSecret: _pollSecret,
      );
      if (!mounted) return;
      _failures = 0;
      _attempt++;
      switch (status.status) {
        case DeviceLoginState.approved:
          _poller?.cancel();
          if (status.apiKey.isEmpty) {
            _finish('服务端没有返回密钥，请重新登录');
            return;
          }
          await widget.onLoggedIn(DeviceLoginResult(
            apiKey: status.apiKey,
            deviceId: status.deviceId,
            deviceName: _name.text.trim(),
            username: status.username,
          ));
          return;
        case DeviceLoginState.denied:
          _finish('这次登录被拒绝了');
          return;
        case DeviceLoginState.expired:
          _finish('设备码已过期，请重新生成');
          return;
        case DeviceLoginState.claimed:
          // 密钥已经被取走过一次（上次拿到了 key 但没存住）：再问永远是 claimed，
          // 老逻辑会一直显示「等待浏览器里批准…」直到十分钟后码自然过期
          _finish('这次登录已经完成过了，点「换一个设备码」重来');
          return;
        default:
          // pending：继续按节奏问
          setState(() {});
          _scheduleNext();
      }
    } catch (err) {
      if (!mounted) return;
      _failures++;
      _attempt++;
      if (_failures >= _failuresBeforeNotice) {
        // 网络 / 服务端出了问题：说出来，但别停——恢复了要能自己接上
        setState(() => _error = friendlyError(err, widget.baseUrl));
      }
      _scheduleNext();
    }
  }

  /// 复制设备码前先确保它真的登记过了。
  Future<void> _copyCode() async {
    final messenger = ScaffoldMessenger.of(context);
    if (!_registered && !(await _begin())) {
      messenger.showSnackBar(const SnackBar(content: Text('设备码没登记成功，看一下上面的提示再试')));
      return;
    }
    if (!mounted || !_registered) return;
    await Clipboard.setData(ClipboardData(text: _userCode));
    messenger.showSnackBar(const SnackBar(content: Text('设备码已复制')));
  }

  /// 主操作：确保登记 + 复制设备码 + 弹出系统浏览器。
  Future<void> _openLoginAndCopy() async {
    final messenger = ScaffoldMessenger.of(context);
    if (!_registered && !(await _begin())) {
      messenger.showSnackBar(const SnackBar(content: Text('设备码没登记成功，看一下上面的提示再试')));
      return;
    }
    if (!mounted) return;
    await Clipboard.setData(ClipboardData(text: _userCode));
    var opened = false;
    try {
      opened = await launchUrl(Uri.parse(_loginUrl), mode: LaunchMode.externalApplication);
    } catch (_) {
      opened = false;
    }
    messenger.showSnackBar(
      SnackBar(
        content: Text(opened ? '已复制设备码 $_userCode，在浏览器里粘一下就能登录' : '已复制设备码 $_userCode，请手动打开 $_loginUrl'),
        duration: const Duration(seconds: 4),
      ),
    );
  }

  String get _statusText {
    if (_starting) return '正在登记设备码…';
    if (_error != null) return _error!;
    if (!_registered) return '还没登记成功，点下面的按钮重试';
    return '等待浏览器里批准…';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final remaining = _startedAt == null || _expiresIn == 0
        ? null
        : _expiresIn - DateTime.now().difference(_startedAt!).inSeconds;
    return Scaffold(
      appBar: AppBar(title: const Text('登录 Tonicuisc')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text('把这台设备连到你的账户', style: theme.textTheme.titleMedium),
          const SizedBox(height: 6),
          Text('点下面的按钮会打开登录页并复制设备码，在浏览器里粘贴即可。', style: theme.textTheme.bodySmall),
          const SizedBox(height: 20),
          InkWell(
            onTap: _copyCode,
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 20),
              decoration: BoxDecoration(
                color: theme.colorScheme.primaryContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                _registered ? _userCode : '点这里登记设备码',
                textAlign: TextAlign.center,
                style: theme.textTheme.headlineMedium?.copyWith(
                  fontFamily: 'monospace',
                  letterSpacing: 4,
                  fontWeight: FontWeight.bold,
                  color: theme.colorScheme.onPrimaryContainer,
                ),
              ),
            ),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: _starting ? null : _openLoginAndCopy,
            icon: const Icon(Icons.open_in_new),
            label: const Text('打开登录页并复制设备码'),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              if (_starting)
                const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
              else
                Icon(
                  _error != null ? Icons.error_outline : (_registered ? Icons.hourglass_empty : Icons.info_outline),
                  size: 18,
                  color: _error != null ? theme.colorScheme.error : theme.colorScheme.onSurfaceVariant,
                ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  _statusText,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: _error != null ? theme.colorScheme.error : null,
                  ),
                ),
              ),
            ],
          ),
          if (remaining != null && remaining > 0)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('设备码还有约 ${(remaining / 60).ceil()} 分钟有效',
                  style: theme.textTheme.labelSmall),
            ),
          const SizedBox(height: 20),
          TextField(
            controller: _name,
            decoration: const InputDecoration(
              labelText: '设备名',
              hintText: '方便以后在「我的」里辨认',
              border: OutlineInputBorder(),
            ),
            // 改名字不该把设备码作废（以前按回车会换一个码）——没登记过才去登记
            onSubmitted: (_) {
              if (!_registered) _begin();
            },
          ),
          TextButton.icon(
            onPressed: _starting ? null : _reset,
            icon: const Icon(Icons.refresh),
            label: const Text('换一个设备码'),
          ),
          const SizedBox(height: 8),
          Text(
            '登录地址：$_loginUrl\n'
            '设备码 10 分钟内有效。批准后这台设备会拿到一把专属密钥，'
            '存进系统安全存储（Windows/Linux 上会额外留一份本机明文副本，'
            '免得每次启动都要重新配对；移动端不留），服务端只保存它的哈希。',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
