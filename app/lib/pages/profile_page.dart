import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../api/credentials.dart';
import '../version.dart';

/// 「我的」页面：账户、本机设备、服务端地址、设备管理与退出登录。
class ProfilePage extends StatefulWidget {
  const ProfilePage({
    super.key,
    required this.api,
    required this.baseUrl,
    required this.deviceName,
    required this.username,
    required this.onChangeServer,
    required this.onRelogin,
    required this.onLogout,
    required this.onUnauthorized,
  });

  final ApiClient api;
  final String baseUrl;
  final String? deviceName;
  final String? username;
  final Future<void> Function() onChangeServer;
  final Future<void> Function() onRelogin;
  final Future<void> Function() onLogout;
  final Future<void> Function(String message) onUnauthorized;

  @override
  State<ProfilePage> createState() => _ProfilePageState();
}

class _ProfilePageState extends State<ProfilePage> {
  Map<String, dynamic>? _me;
  List<Map<String, dynamic>> _devices = const [];
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(covariant ProfilePage oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.baseUrl != widget.baseUrl || oldWidget.username != widget.username) {
      _load();
    }
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final me = await widget.api.me();
      final devices = await widget.api.devices();
      if (!mounted) return;
      setState(() {
        _me = me;
        _devices = devices;
      });
    } catch (err) {
      if (!mounted) return;
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      setState(() => _error = '$err');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _revoke(Map<String, dynamic> device) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('吊销这台设备？'),
        content: Text('${device['name'] ?? '未命名设备'}\n\n吊销后它需要重新登录。'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('吊销')),
        ],
      ),
    );
    if (confirmed != true) return;
    final wasCurrent = device['current'] == true;
    final messenger = ScaffoldMessenger.of(context);
    try {
      await widget.api.revokeDevice('${device['id']}');
      if (wasCurrent) {
        // 把自己吊销了：立刻退出，别再拿着已经失效的 key
        await widget.onUnauthorized('本机设备已被吊销，请重新登录');
        return;
      }
      if (!mounted) return;
      // 先从列表里拿掉，再跟服务端对一次
      setState(() {
        _devices = _devices.where((item) => item['id'] != device['id']).toList();
      });
      messenger.showSnackBar(SnackBar(content: Text('已吊销 ${device['name'] ?? '该设备'}')));
      await _load();
    } catch (err) {
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      if (mounted) {
        messenger.showSnackBar(SnackBar(content: Text('吊销失败：$err')));
      }
    }
  }

  static String _formatTime(dynamic value) {
    final seconds = (value as num?)?.toDouble();
    if (seconds == null || seconds <= 0) return '-';
    final dt = DateTime.fromMillisecondsSinceEpoch((seconds * 1000).round());
    String two(int v) => v.toString().padLeft(2, '0');
    return '${dt.year}-${two(dt.month)}-${two(dt.day)} ${two(dt.hour)}:${two(dt.minute)}';
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final username = (_me?['username'] ?? widget.username ?? '未知账户').toString();
    final deviceName = (_me?['device_name'] ?? widget.deviceName ?? '-').toString();
    final deviceId = (_me?['device_id'] ?? '-').toString();

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                CircleAvatar(
                  radius: 26,
                  child: Text(username.isEmpty ? '?' : username.substring(0, 1).toUpperCase()),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(username, style: theme.textTheme.titleMedium),
                      const SizedBox(height: 4),
                      Text('本机：$deviceName', style: theme.textTheme.bodySmall),
                      Text('设备 ID：${deviceId.length > 8 ? deviceId.substring(0, 8) : deviceId}…',
                          style: theme.textTheme.bodySmall),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        const SizedBox(height: 8),
        if (Credentials.usingFallback)
          Card(
            color: Theme.of(context).colorScheme.errorContainer,
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  const Icon(Icons.warning_amber_outlined),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(
                      '本机没能用上系统安全存储，密钥退回了普通存储保存（照常使用，安全性略低）。',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                ],
              ),
            ),
          ),
        ListTile(
          leading: const Icon(Icons.dns_outlined),
          title: const Text('服务器地址'),
          subtitle: Text(widget.baseUrl),
          trailing: const Icon(Icons.edit_outlined),
          onTap: () async {
            await widget.onChangeServer();
            await _load();
          },
        ),
        ListTile(
          leading: const Icon(Icons.key_outlined),
          title: const Text('重新登录'),
          subtitle: const Text('生成新的设备码，换一个账户或密码登录'),
          onTap: widget.onRelogin,
        ),
        ListTile(
          leading: Icon(Icons.logout, color: theme.colorScheme.error),
          title: Text('退出登录', style: TextStyle(color: theme.colorScheme.error)),
          subtitle: const Text('清除本机保存的密钥'),
          onTap: widget.onLogout,
        ),
        const Divider(height: 32),
        ListTile(
          dense: true,
          leading: const Icon(Icons.info_outline),
          title: const Text('版本'),
          subtitle: Text('$kAppVersion（build $kAppBuild）'),
          onTap: _load,
        ),
        const Divider(height: 32),
        Row(
          children: [
            Text('已登录设备', style: theme.textTheme.titleSmall),
            const Spacer(),
            if (_loading) const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
            IconButton(tooltip: '刷新', onPressed: _loading ? null : _load, icon: const Icon(Icons.refresh)),
          ],
        ),
        if (_error != null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text(_error!, style: TextStyle(color: theme.colorScheme.error)),
          ),
        for (final device in _devices.where((item) => item['revoked'] != true))
          ListTile(
            dense: true,
            leading: Icon(device['current'] == true ? Icons.smartphone : Icons.devices_other),
            title: Text('${device['name'] ?? '未命名设备'}${device['current'] == true ? '（本机）' : ''}'),
            subtitle: Text('${device['username'] ?? '无账户（旧版残留）'} · 最后使用 ${_formatTime(device['last_seen'])}'),
            trailing: IconButton(
              tooltip: '吊销',
              icon: const Icon(Icons.block),
              onPressed: () => _revoke(device),
            ),
          ),
        if (_devices.any((item) => item['revoked'] == true))
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text(
              '另有 ${_devices.where((item) => item['revoked'] == true).length} 台已吊销设备未显示'
              '（服务器上执行 python -m tonicuisc_server cleanup 可彻底删除）',
              style: theme.textTheme.labelSmall,
            ),
          ),
      ],
    );
  }
}
