import 'dart:async';
import 'dart:io';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api/api_client.dart';

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
class LoginPage extends StatefulWidget {
  const LoginPage({
    super.key,
    required this.baseUrl,
    required this.onLoggedIn,
    this.initialDeviceName,
  });

  final String baseUrl;
  final String? initialDeviceName;
  final Future<void> Function(String apiKey, String deviceId, String username) onLoggedIn;

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  static const String _alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

  final Random _random = Random.secure();
  late final TextEditingController _name =
      TextEditingController(text: widget.initialDeviceName ?? defaultDeviceName());

  Timer? _poller;
  late String _userCode = _newUserCode();
  late String _pollSecret = _newSecret();

  bool _registered = false;
  bool _starting = false;
  String? _error;
  int _expiresIn = 0;
  DateTime? _startedAt;

  @override
  void initState() {
    super.initState();
    _begin(); // 进页面就登记，用户只需要去浏览器点一下
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

  String get _loginUrl => '${ApiClient.normalizeBaseUrl(widget.baseUrl)}/login';

  Future<void> _begin() async {
    if (_starting) return;
    setState(() {
      _starting = true;
      _error = null;
    });
    try {
      final started = await ApiClient.startDeviceLogin(
        baseUrl: widget.baseUrl,
        userCode: _userCode,
        pollSecret: _pollSecret,
        deviceName: _name.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _registered = true;
        _expiresIn = started.expiresIn;
        _startedAt = DateTime.now();
      });
      _startPolling();
    } catch (err) {
      if (!mounted) return;
      // 设备码撞车 / 网络不通：给出人话，并换一个码
      setState(() {
        _error = friendlyError(err, widget.baseUrl);
        _registered = false;
        _userCode = _newUserCode();
        _pollSecret = _newSecret();
      });
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
    });
    _begin();
  }

  void _startPolling() {
    _poller?.cancel();
    _poller = Timer.periodic(const Duration(seconds: 2), (_) => _poll());
  }

  Future<void> _poll() async {
    try {
      final status = await ApiClient.deviceStatus(
        baseUrl: widget.baseUrl,
        userCode: _userCode,
        pollSecret: _pollSecret,
      );
      if (!mounted) return;
      switch ('${status['status']}') {
        case 'approved':
          _poller?.cancel();
          final apiKey = (status['api_key'] ?? '').toString();
          final deviceId = (status['device_id'] ?? '').toString();
          final username = (status['username'] ?? '').toString();
          if (apiKey.isEmpty) {
            setState(() => _error = '服务端没有返回密钥，请重新登录');
            return;
          }
          await widget.onLoggedIn(apiKey, deviceId, username);
          return;
        case 'denied':
          _poller?.cancel();
          setState(() => _error = '这次登录被拒绝了');
          return;
        case 'expired':
          _poller?.cancel();
          setState(() {
            _registered = false;
            _error = '设备码已过期，请重新生成';
          });
          return;
        default:
          return; // pending / claimed：继续等
      }
    } catch (_) {
      // 网络抖动忽略，下一轮再问
    }
  }

  /// 主操作：复制设备码 + 弹出系统浏览器。
  Future<void> _openLoginAndCopy() async {
    final messenger = ScaffoldMessenger.of(context);
    if (!_registered) {
      await _begin();
      if (!mounted) return;
    }
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
    if (!_registered) return '点下面的按钮开始';
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
            onTap: () async {
              await Clipboard.setData(ClipboardData(text: _userCode));
              if (!mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('设备码已复制')));
            },
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 20),
              decoration: BoxDecoration(
                color: theme.colorScheme.primaryContainer,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Text(
                _userCode,
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
            onSubmitted: (_) => _reset(),
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
            '只存在系统安全存储里，界面上看不到，服务端也只保存它的哈希。',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
