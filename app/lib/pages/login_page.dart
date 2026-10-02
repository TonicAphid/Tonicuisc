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
/// App 自己生成设备码（`A1B1-C1D1`）和一个只有它知道的轮询密钥，
/// 用户拿手机浏览器打开 `服务器地址/login`，输入设备码，选账户或注册，
/// 批准之后 App 轮询到 key 就完成登录。
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

  bool _registering = false;
  bool _waiting = false;
  String? _error;
  String _hint = '';

  String _randomString(int length) =>
      List.generate(length, (_) => _alphabet[_random.nextInt(_alphabet.length)]).join();

  String _newUserCode() {
    final raw = _randomString(8);
    return '${raw.substring(0, 4)}-${raw.substring(4)}';
  }

  String _newSecret() =>
      List.generate(32, (_) => 'abcdefghijklmnopqrstuvwxyz0123456789'[_random.nextInt(36)]).join();

  String get _loginUrl => '${ApiClient.normalizeBaseUrl(widget.baseUrl)}/login';

  @override
  void dispose() {
    _poller?.cancel();
    _name.dispose();
    super.dispose();
  }

  Future<void> _begin() async {
    setState(() {
      _registering = true;
      _error = null;
      _hint = '';
    });
    try {
      await ApiClient.startDeviceLogin(
        baseUrl: widget.baseUrl,
        userCode: _userCode,
        pollSecret: _pollSecret,
        deviceName: _name.text.trim(),
      );
      if (!mounted) return;
      setState(() {
        _waiting = true;
        _hint = '等待批准…先在浏览器里完成登录';
      });
      _startPolling();
    } catch (err) {
      if (!mounted) return;
      // 码被占用之类的：换一个再试
      setState(() {
        _error = '$err';
        _userCode = _newUserCode();
        _pollSecret = _newSecret();
      });
    } finally {
      if (mounted) setState(() => _registering = false);
    }
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
          setState(() {
            _waiting = false;
            _error = '这次登录被拒绝了';
          });
          return;
        case 'expired':
          _poller?.cancel();
          setState(() {
            _waiting = false;
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

  Future<void> _openBrowser() async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final ok = await launchUrl(Uri.parse(_loginUrl), mode: LaunchMode.externalApplication);
      if (!ok) {
        messenger.showSnackBar(const SnackBar(content: Text('打不开浏览器，请手动复制上面的地址')));
      }
    } catch (err) {
      messenger.showSnackBar(SnackBar(content: Text('打不开浏览器：$err')));
    }
  }

  Future<void> _copy(String value, String label) async {
    await Clipboard.setData(ClipboardData(text: value));
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('$label 已复制')));
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('登录 Tonicuisc')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text('1. 用手机浏览器打开登录页', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          InkWell(
            onTap: () => _copy(_loginUrl, '登录地址'),
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: theme.colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Text(_loginUrl, style: theme.textTheme.bodyMedium),
            ),
          ),
          const SizedBox(height: 8),
          OutlinedButton.icon(
            onPressed: _openBrowser,
            icon: const Icon(Icons.open_in_browser),
            label: const Text('打开登录页'),
          ),
          const SizedBox(height: 20),
          Text('2. 把这个设备码填进去', style: theme.textTheme.titleSmall),
          const SizedBox(height: 8),
          InkWell(
            onTap: () => _copy(_userCode, '设备码'),
            child: Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(vertical: 18),
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
          const SizedBox(height: 4),
          Text('点一下可以复制', style: theme.textTheme.labelSmall),
          const SizedBox(height: 20),
          TextField(
            controller: _name,
            decoration: const InputDecoration(
              labelText: '设备名',
              hintText: '方便以后在「我的」里辨认',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 16),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
            ),
          if (_hint.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Row(
                children: [
                  const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
                  const SizedBox(width: 10),
                  Expanded(child: Text(_hint, style: theme.textTheme.bodySmall)),
                ],
              ),
            ),
          FilledButton.icon(
            onPressed: _registering ? null : _begin,
            icon: const Icon(Icons.login),
            label: Text(_waiting ? '重新开始' : '我已在浏览器里登录'),
          ),
          const SizedBox(height: 8),
          TextButton(
            onPressed: _registering
                ? null
                : () => setState(() {
                      _userCode = _newUserCode();
                      _pollSecret = _newSecret();
                      _error = null;
                      _hint = '';
                      _waiting = false;
                      _poller?.cancel();
                    }),
            child: const Text('换一个设备码'),
          ),
          const SizedBox(height: 12),
          Text(
            '设备码 10 分钟内有效。批准后这台设备会拿到一把专属密钥，'
            '只存在系统安全存储里，界面上看不到，服务端也只保存它的哈希。',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
