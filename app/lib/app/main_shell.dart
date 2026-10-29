import 'dart:async';

import 'package:flutter/cupertino.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../core/api_client.dart';
import '../core/credentials.dart';
import '../core/self_signed.dart';
import '../core/update_installer.dart';
import '../features/auth/device_login.dart';
import '../features/auth/login_page.dart';
import '../features/library/library_page.dart';
import '../features/library/library_state.dart';
import '../features/player/player_bar.dart';
import '../features/player/player_controller.dart';
import '../features/profile/profile_page.dart';
import '../features/search/search_page.dart';
import '../shared/cover_cache.dart';
import 'glass_tab_bar.dart';
import 'layout.dart';
import 'legacy_material_host.dart';
import 'nav_sidebar.dart';

/// 应用外壳：窄屏底部三个 tab（搜索 / 列表 / 我的），宽屏左侧导航栏；
/// 未登录时直接显示设备码登录页。
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
    // 四个读都是本地存储（SharedPreferences / 安全存储），**先全部发出去、再逐个 await**：
    // 串行 await 要多花三个来回，桌面端安全存储偶发变慢时更明显。
    //
    // 这里不用 `Future.wait`：它是 `Future<String>` 和 `Future<String?>` 混在一起，
    // 会被推成 `List<String?>`，于是 baseUrl 也成了可空类型（analyze 直接报错）。
    // 分开 await 既保持了并发，类型也各是各的。
    final urlFuture = loadBaseUrl();
    final keyFuture = Credentials.apiKey();
    final nameFuture = Credentials.deviceName();
    final usernameFuture = Credentials.username();

    final url = await urlFuture;
    final key = await keyFuture;
    final name = await nameFuture;
    final username = await usernameFuture;
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
    // 上次自动更新失败时安装脚本留了原因（UAC 被取消 / 安装程序报错），
    // 等首帧出来再弹，这时 Navigator 才在。
    WidgetsBinding.instance.addPostFrameCallback((_) => unawaited(_reportUpdateResult()));
  }

  /// 把「上次自动更新没成功」的原因提示一次，读完即删。
  Future<void> _reportUpdateResult() async {
    final message = await UpdateInstaller.consumeResult();
    if (message == null || !mounted) return;
    await showCupertinoDialog<void>(
      context: context,
      builder: (context) => CupertinoAlertDialog(
        title: const Text('自动更新没有完成'),
        content: Text('$message\n\n可以到 GitHub Releases 手动下载安装包。'),
        actions: [
          CupertinoDialogAction(
            onPressed: () => Navigator.pop(context),
            child: const Text('好'),
          ),
        ],
      ),
    );
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

  /// 登录成功：把凭据（含**用户刚填的设备名**）存下来并切换 ApiClient。
  Future<void> _handleLoggedIn(DeviceLoginResult result) async {
    final deviceName = result.deviceName.isEmpty ? defaultDeviceName() : result.deviceName;
    await Credentials.save(
      apiKey: result.apiKey,
      deviceId: result.deviceId,
      deviceName: deviceName,
      username: result.username,
    );
    if (!mounted) return;
    _adoptApi(ApiClient(_baseUrl, apiKey: result.apiKey));
    setState(() {
      _apiKey = result.apiKey;
      _deviceName = deviceName;
      _username = result.username.isEmpty ? null : result.username;
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

  /// 「我的」页长按版本号进来的隐藏入口：改服务器地址 + 自签证书开关。
  Future<void> _editServerUrl() async {
    final controller = TextEditingController(text: _baseUrl);
    var allowSelfSigned = SelfSignedOverrides.allow;
    final value = await showCupertinoDialog<String>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => CupertinoAlertDialog(
          title: const Text('服务器地址'),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const SizedBox(height: 14),
              CupertinoTextField(
                controller: controller,
                autofocus: true,
                keyboardType: TextInputType.url,
                placeholder: kDefaultServerUrl,
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text('允许自签证书', textAlign: TextAlign.left, style: TextStyle(fontSize: 15)),
                        SizedBox(height: 2),
                        Text(
                          '内网 HTTPS 用；公网域名不需要开',
                          textAlign: TextAlign.left,
                          style: TextStyle(fontSize: 12, color: CupertinoColors.systemGrey),
                        ),
                      ],
                    ),
                  ),
                  CupertinoSwitch(
                    value: allowSelfSigned,
                    onChanged: (next) => setDialogState(() => allowSelfSigned = next),
                  ),
                ],
              ),
            ],
          ),
          actions: [
            CupertinoDialogAction(
              onPressed: () => Navigator.pop(context),
              child: const Text('取消'),
            ),
            CupertinoDialogAction(
              isDefaultAction: true,
              onPressed: () => Navigator.pop(context, controller.text.trim()),
              child: const Text('保存'),
            ),
          ],
        ),
      ),
    );
    if (value == null || value.isEmpty) return;
    final normalized = normalizeBaseUrl(value);
    await saveBaseUrl(normalized);
    await saveAllowSelfSigned(allowSelfSigned);
    SelfSignedOverrides.allow = allowSelfSigned; // 立刻生效，不用重启 App
    if (!mounted) return;
    // 收藏列表、播放器的回退流都要跟着换到新地址（播放器里的旧 ApiClient 还指着旧主机）
    _adoptApi(ApiClient(normalized, apiKey: _apiKey));
    setState(() => _baseUrl = normalized);
  }

  static const List<String> _titles = ['Tonicuisc', '列表', '我的'];

  /// 三个目的地（图标 + 选中态图标 + 文字）。**窄屏悬浮胶囊和宽屏侧边栏共用这一份**，
  /// 免得哪天改了一边、另一边图标对不上。
  static const List<NavSidebarItem> _navItems = [
    NavSidebarItem(
      icon: CupertinoIcons.search,
      selectedIcon: CupertinoIcons.search_circle_fill,
      label: '搜索',
    ),
    NavSidebarItem(
      icon: CupertinoIcons.music_albums,
      selectedIcon: CupertinoIcons.music_albums_fill,
      label: '列表',
    ),
    NavSidebarItem(
      icon: CupertinoIcons.person,
      selectedIcon: CupertinoIcons.person_fill,
      label: '我的',
    ),
  ];

  /// 窄屏悬浮胶囊的三项 —— 从 `_navItems` 派生。
  static final List<GlassTabItem> _tabItems = [
    for (final item in _navItems)
      GlassTabItem(icon: item.icon, selectedIcon: item.selectedIcon, label: item.label),
  ];

  /// 三个 tab 的页面本体：**窄屏 / 宽屏共用同一份**，只换外面的壳。
  ///
  /// 用 `IndexedStack` 而不是每次重建：切 tab 不重来，搜索到一半的结果、
  /// 翻到第几页都留着。窗口在断点两侧拖动时，外面那层是「同结构、换参数」
  /// （见 `_content`），这棵子树原地复用，不会丢状态。
  Widget _pages() {
    // `LegacyMaterialSurface` 把 Material 祖先放在 `CupertinoPageScaffold` 的底色
    // **里面**，否则 ListTile 会报「底色/水波纹可能看不见」（见该类的注释）。
    return LegacyMaterialSurface(
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
    );
  }

  void _selectTab(int index) {
    setState(() => _tab = index);
    if (index == 1) unawaited(_library.refresh()); // 切到列表就刷新一次数字
  }

  /// 「掉线 / 被登出」的提示条：窄屏贴在顶栏下面，宽屏贴在内容区顶上。
  Widget _noticeBanner(BuildContext context) {
    final danger = CupertinoColors.systemRed.resolveFrom(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: danger.withValues(alpha: 0.12),
        border: Border(
          bottom: BorderSide(color: CupertinoColors.separator.resolveFrom(context), width: 0.5),
        ),
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 6, 8),
        child: Row(
          children: [
            Icon(CupertinoIcons.info_circle, size: 18, color: danger),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                _notice!,
                style: TextStyle(fontSize: 13, color: CupertinoColors.label.resolveFrom(context)),
              ),
            ),
            CupertinoButton(
              padding: const EdgeInsets.all(8),
              pressedOpacity: 0.5,
              onPressed: () => setState(() => _notice = null),
              child: Icon(
                CupertinoIcons.xmark,
                size: 16,
                color: CupertinoColors.secondaryLabel.resolveFrom(context),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _playerBar() {
    return PlayerBar(
      controller: _player,
      api: _api,
      library: _library,
      onUnauthorized: _handleUnauthorized,
    );
  }

  /// 窄屏底部的**悬浮胶囊** tab 栏（iOS 26 那套）。
  ///
  /// 底部安全区（home indicator）在这里补：`GlassTabBar` 故意不自己吃
  /// `viewPadding.bottom`，否则胶囊会被撑高、圆角比例就散了。
  /// 也正因为 tab 栏在最底下，**播放条不用再加一层**安全区。
  Widget _tabBar() {
    final safeBottom = MediaQuery.viewPaddingOf(context).bottom;
    return Padding(
      padding: EdgeInsets.only(bottom: safeBottom) + kFloatingTabBarMargin,
      child: GlassTabBar(currentIndex: _tab, items: _tabItems, onTap: _selectTab),
    );
  }

  /// 内容区。
  ///
  /// 宽屏时两侧平分留白，把内容收在 `kDesktopMaxContentWidth` 以内——但**不改
  /// 传给页面的约束**（用 `Padding` 而不是 `ConstrainedBox`），所以搜索列表、
  /// 歌单这些页面一行都不用动。
  ///
  /// 结构在两种布局下完全一致（只换 padding 数值），这样在断点两侧拖动窗口时
  /// `IndexedStack` 里那三页原地复用，不会重建。
  Widget _content(bool desktop) {
    final pages = Column(
      children: [
        if (_notice != null) _noticeBanner(context),
        Expanded(child: _pages()),
      ],
    );
    return LayoutBuilder(
      builder: (context, constraints) {
        final slack = desktop ? constraints.maxWidth - kDesktopMaxContentWidth : 0.0;
        final pad = slack > 0 ? slack / 2 : 0.0;
        return Padding(padding: EdgeInsets.symmetric(horizontal: pad), child: pages);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    if (!_booted) {
      return const CupertinoPageScaffold(
        child: Center(child: CupertinoActivityIndicator()),
      );
    }
    if (!_loggedIn) {
      return LoginPage(
        key: ValueKey('login-$_baseUrl'),
        baseUrl: _baseUrl,
        initialDeviceName: _deviceName,
        onLoggedIn: _handleLoggedIn,
      );
    }

    // 一份代码、两套布局：窗口够宽（且不是手机横屏那种「宽而扁」）就走桌面布局。
    final desktop = useDesktopLayout(MediaQuery.sizeOf(context));

    return CupertinoPageScaffold(
      // 宽屏有左侧导航指路，不再占一条顶栏；窄屏沿用原来的顶栏标题
      navigationBar: desktop ? null : CupertinoNavigationBar(middle: Text(_titles[_tab])),
      child: Column(
        children: [
          Expanded(
            child: Row(
              children: [
                if (desktop)
                  NavSidebar(
                    currentIndex: _tab,
                    items: _navItems,
                    onSelected: _selectTab,
                  ),
                Expanded(child: _content(desktop)),
              ],
            ),
          ),
          // 播放条两种布局都在，而且**横跨整个窗口**（侧边栏下面也铺到）
          _playerBar(),
          if (!desktop) _tabBar(),
        ],
      ),
    );
  }
}
