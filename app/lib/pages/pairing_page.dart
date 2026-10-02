import 'dart:io';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../api/credentials.dart';

/// 配对页：填服务器地址 + 控制台打印的一次性配对码，换回本设备专属 API Key。
class PairingPage extends StatefulWidget {
  const PairingPage({super.key, required this.initialBaseUrl});

  final String initialBaseUrl;

  @override
  State<PairingPage> createState() => _PairingPageState();
}

class _PairingPageState extends State<PairingPage> {
  late final TextEditingController _baseUrl = TextEditingController(text: widget.initialBaseUrl);
  final TextEditingController _code = TextEditingController();
  late final TextEditingController _name = TextEditingController(text: _defaultDeviceName());

  bool _busy = false;
  String? _error;

  static String _defaultDeviceName() {
    if (Platform.isIOS) return 'iPhone';
    if (Platform.isMacOS) return 'Mac';
    if (Platform.isWindows) return 'Windows';
    if (Platform.isLinux) return 'Linux';
    return 'Device';
  }

  @override
  void dispose() {
    _baseUrl.dispose();
    _code.dispose();
    _name.dispose();
    super.dispose();
  }

  Future<void> _pair() async {
    final baseUrl = ApiClient.normalizeBaseUrl(_baseUrl.text);
    final code = _code.text.trim();
    if (baseUrl.isEmpty) {
      setState(() => _error = '请填写服务器地址');
      return;
    }
    if (code.isEmpty) {
      setState(() => _error = '请填写配对码');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await ApiClient.pair(baseUrl: baseUrl, code: code, name: _name.text);
      await Credentials.save(
        apiKey: result.apiKey,
        deviceId: result.deviceId,
        deviceName: result.deviceName,
      );
      await ApiClient.saveBaseUrl(baseUrl);
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (err) {
      if (mounted) setState(() => _error = '$err');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      appBar: AppBar(title: const Text('配对设备')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text('配对后这台设备会拿到一把专属密钥，只存在系统安全存储里。', style: theme.textTheme.bodyMedium),
          const SizedBox(height: 20),
          TextField(
            controller: _baseUrl,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(
              labelText: '服务器地址',
              hintText: 'https://xxx.nip.io 或 http://192.168.1.10:8000',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 16),
          TextField(
            controller: _code,
            keyboardType: TextInputType.number,
            maxLength: 6,
            decoration: const InputDecoration(
              labelText: '配对码',
              hintText: '服务端控制台打印的 6 位数字',
              border: OutlineInputBorder(),
            ),
          ),
          TextField(
            controller: _name,
            decoration: const InputDecoration(
              labelText: '设备名',
              hintText: '方便以后在服务端辨认',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 8),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
            ),
          FilledButton.icon(
            onPressed: _busy ? null : _pair,
            icon: _busy
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.link),
            label: Text(_busy ? '配对中…' : '开始配对'),
          ),
          const SizedBox(height: 16),
          Text(
            '配对码在服务端启动时打印，5 分钟内有效、用过即废。\n'
            '密钥不会显示在任何界面上；设备丢了就在服务端 `python -m tonicuisc_server devices` 里吊销。',
            style: theme.textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}
