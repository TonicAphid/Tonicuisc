import 'dart:async';

import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../api/api_client.dart';
import '../api/credentials.dart';
import '../player/player_controller.dart';
import '../state/cover_cache.dart';
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

class _MainShellState extends State<MainShell> with WidgetsBindingObserver {
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
    WidgetsBinding.instance.addObserver(this);
    unawaited(_keepScreenAwake(true));
    _bootstrap();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    unawaited(_keepScreenAwake(false));
    _player.dispose();
    super.dispose();
  }

  /// 前台不让屏幕自动息屏；切到后台就放开，别在后台耗电。
  Future<void> _keepScreenAwake(bool enabled) async {
    try {
      await (enabled ? WakelockPlus.enable() : WakelockPlus.disable());
    } catch (_) {
      // 平台不支持就算了，不影响使用
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    unawaited(_keepScreenAwake(state == AppLifecycleState.resumed));
  }

  Future<void> _bootstrap() async {
    final url = await ApiClient.loadBaseUrl();
    final key = await Credentials.apiKey();
    final name = await Credentials.deviceName();
    final username = await Credentials.username();
    if (!mounted) return;
    _adoptApi(ApiClient(url, apiKey: key));
    setState(() {
      _baseUrl = url;
      _apiKey = key;
      _deviceName = name;
      _username = username;
      _booted = true;
    });
    if (key != null && key.isNotEmpty) {
      unawaited(_library.refresh());
    }
  }

  /// 统一的「换 ApiClient」动作：重建 LibraryState + 让播放器跟着换。
  ///
  /// 换 key、换服务器、退出登录都必须走这里——漏掉播放器那一步就会出现
  /// 「界面已经登出/换了地址，播放器还在拿旧 key 往旧地址发回退流和播放历史」。
  void _adoptApi(ApiClient api) {
    _api = api;
    _library = LibraryState(api);
    _player.updateApi(api);
  }

  Future<void> _handleLoggedIn(String apiKey, String deviceId, String username) async {
    await Credentials.save(
      apiKey: apiKey,
      deviceId: deviceId,
      deviceName: _deviceName ?? defaultDeviceName(),
      username: username,
    );
    if (!mounted) return;
    _adoptApi(ApiClient(_baseUrl, apiKey: apiKey));
    setState(() {
      _apiKey = apiKey;
      _username = username.isEmpty ? null : username;
      _notice = null;
      _tab = 0;
    });
    unawaited(_library.refresh());
  }

  Future<void> _handleUnauthorized(String message) async {
    // 幂等：一次 401 会从好几个页面同时冒出来，只清一次凭据、只提示一次
    if (!_loggedIn) return;
    await Credentials.clear();
    CoverCache.clear();
    if (!mounted) return;
    _apiKey = null;
    _username = null;
    _notice = message;
    _adoptApi(ApiClient(_baseUrl));
    setState(() {});
  }

  Future<void> _logout() async {
    if (!_loggedIn) return;
    await Credentials.clear();
    CoverCache.clear();
    if (!mounted) return;
    _apiKey = null;
    _username = null;
    _notice = null;
    _adoptApi(ApiClient(_baseUrl));
    setState(() {});
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
    // 收藏列表、播放器的回退流都要跟着换到新地址（播放器里的旧 ApiClient 还指着旧主机）
    _adoptApi(ApiClient(normalized, apiKey: _apiKey));
    setState(() => _baseUrl = normalized);
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
          PlayerBar(
            controller: _player,
            api: _api,
            library: _library,
            onUnauthorized: _handleUnauthorized,
          ),
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
