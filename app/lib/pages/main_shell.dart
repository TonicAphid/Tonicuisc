import 'dart:async';

import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../api/credentials.dart';
import '../player/player_controller.dart';
import '../state/library_state.dart';
import '../widgets/player_bar.dart';
import 'library_page.dart';
import 'login_page.dart';
import 'profile_page.dart';
import 'search_page.dart';

/// 应用外壳：底部三个 tab（搜索 / 列表 / 我的），未登录时直接显示设备码登录页。
class MainShell extends StatefulWidget {
  const MainShell({super.key});

  @override
  State<MainShell> createState() => _MainShellState();
}

class _MainShellState extends State<MainShell> {
  final PlayerController _player = PlayerController();

  ApiClient _api = ApiClient(defaultBaseUrl());
  late LibraryState _library = LibraryState(_api);
  String _baseUrl = defaultBaseUrl();
  String? _apiKey;
  String? _deviceName;
  String? _username;
  String? _notice;
  bool _booted = false;
  int _tab = 0;

  bool get _loggedIn => _apiKey != null && _apiKey!.isNotEmpty;

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  @override
  void dispose() {
    _player.dispose();
    super.dispose();
  }

  Future<void> _bootstrap() async {
    final url = await ApiClient.loadBaseUrl();
    final key = await Credentials.apiKey();
    final name = await Credentials.deviceName();
    final username = await Credentials.username();
    if (!mounted) return;
    setState(() {
      _baseUrl = url;
      _apiKey = key;
      _deviceName = name;
      _username = username;
      _api = ApiClient(url, apiKey: key);
      _library = LibraryState(_api);
      _booted = true;
    });
    if (key != null && key.isNotEmpty) {
      unawaited(_library.refresh());
    }
  }

  Future<void> _handleLoggedIn(String apiKey, String deviceId, String username) async {
    await Credentials.save(
      apiKey: apiKey,
      deviceId: deviceId,
      deviceName: _deviceName ?? defaultDeviceName(),
      username: username,
    );
    if (!mounted) return;
    setState(() {
      _apiKey = apiKey;
      _username = username.isEmpty ? null : username;
      _api = ApiClient(_baseUrl, apiKey: apiKey);
      _library = LibraryState(_api);
      _notice = null;
      _tab = 0;
    });
    unawaited(_library.refresh());
  }

  Future<void> _handleUnauthorized(String message) async {
    await Credentials.clear();
    if (!mounted) return;
    setState(() {
      _apiKey = null;
      _username = null;
      _api = ApiClient(_baseUrl);
      _notice = message;
    });
  }

  Future<void> _logout() async {
    await Credentials.clear();
    if (!mounted) return;
    setState(() {
      _apiKey = null;
      _username = null;
      _api = ApiClient(_baseUrl);
      _notice = null;
    });
  }

  Future<void> _editServerUrl() async {
    final controller = TextEditingController(text: _baseUrl);
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('服务器地址'),
        content: TextField(
          controller: controller,
          autofocus: true,
          keyboardType: TextInputType.url,
          decoration: const InputDecoration(hintText: kDefaultServerUrl),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          FilledButton(onPressed: () => Navigator.pop(context, controller.text.trim()), child: const Text('保存')),
        ],
      ),
    );
    if (value == null || value.isEmpty) return;
    final normalized = ApiClient.normalizeBaseUrl(value);
    await ApiClient.saveBaseUrl(normalized);
    if (!mounted) return;
    setState(() {
      _baseUrl = normalized;
      _api = ApiClient(normalized, apiKey: _apiKey);
      _library = LibraryState(_api);
    });
  }

  static const List<String> _titles = ['Tonicuisc', '列表', '我的'];

  @override
  Widget build(BuildContext context) {
    if (!_booted) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (!_loggedIn) {
      return LoginPage(
        key: ValueKey('login-$_baseUrl'),
        baseUrl: _baseUrl,
        initialDeviceName: _deviceName,
        onLoggedIn: _handleLoggedIn,
      );
    }

    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_tab]),
      ),
      body: Column(
        children: [
          if (_notice != null)
            Material(
              color: Theme.of(context).colorScheme.errorContainer,
              child: ListTile(
                dense: true,
                leading: const Icon(Icons.info_outline),
                title: Text(_notice!),
                trailing: IconButton(
                  icon: const Icon(Icons.close),
                  onPressed: () => setState(() => _notice = null),
                ),
              ),
            ),
          Expanded(
            child: IndexedStack(
              index: _tab,
              children: [
                SearchPage(
                  api: _api,
                  player: _player,
                  library: _library,
                  onUnauthorized: _handleUnauthorized,
                ),
                LibraryPage(
                  api: _api,
                  library: _library,
                  player: _player,
                  onUnauthorized: _handleUnauthorized,
                ),
                ProfilePage(
                  api: _api,
                  baseUrl: _baseUrl,
                  deviceName: _deviceName,
                  username: _username,
                  onChangeServer: _editServerUrl,
                  onRelogin: _logout,
                  onLogout: _logout,
                  onUnauthorized: _handleUnauthorized,
                ),
              ],
            ),
          ),
        ],
      ),
      bottomNavigationBar: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          PlayerBar(controller: _player, api: _api, library: _library),
          NavigationBar(
            selectedIndex: _tab,
            onDestinationSelected: (index) {
              setState(() => _tab = index);
              if (index == 1) unawaited(_library.refresh()); // 切到列表就刷新一次数字
            },
            destinations: const [
              NavigationDestination(icon: Icon(Icons.search), label: '搜索'),
              NavigationDestination(icon: Icon(Icons.library_music_outlined), label: '列表'),
              NavigationDestination(icon: Icon(Icons.person_outline), label: '我的'),
            ],
          ),
        ],
      ),
    );
  }
}
