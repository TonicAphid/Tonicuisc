import 'package:flutter/cupertino.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:tonicuisc/app/app.dart';
import 'package:tonicuisc/app/nav_sidebar.dart';

/// 外壳「真跑一遍」的测试。
///
/// `flutter analyze` 只看类型，看不出**运行时**的问题——而从 Material 换到 Cupertino
/// 最容易踩的恰好就是这类：`CupertinoApp` 不提供 `ScaffoldMessenger`、没有 `Material`
/// 祖先、`CupertinoPageScaffold` 会插一层带底色的 `DecoratedBox`……这些挂载一次就会
/// 直接抛断言。布局撑破（`RenderFlex` 溢出）也会在这里报出来。
///
/// 两个关键点：
///
/// * **bootstrap 必须用 `tester.runAsync`**：它要读本地存储，是**真实**异步；
///   而 `testWidgets` 默认跑在 fake async 里，光 `pump()` 推不动它，会一直停在
///   加载圈上、永远看不到外壳。所以切回真实事件循环并轮询到加载圈消失。
/// * **不能用 `pumpAndSettle`**：`CupertinoActivityIndicator` 是无限动画，
///   永远 settle 不了，会超时。
/// * **凭据靠 SharedPreferences 喂**：测试里 `flutter_secure_storage` 会抛
///   `MissingPluginException`，`Credentials` try/catch 掉之后回落到 `fallback_`
///   前缀的普通存储，所以种上那一条就等价于「已登录」，能进到外壳。
void main() {
  /// 挂起整个 App，把窗口设成 [size]（逻辑像素），并等 bootstrap 跑完。
  Future<void> pumpApp(WidgetTester tester, Size size) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const TonicuiscApp());
    await tester.pump();

    for (var i = 0; i < 40; i++) {
      if (find.byType(CupertinoActivityIndicator).evaluate().isEmpty) return;
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 25)));
      await tester.pump();
    }
  }

  /// 已登录状态：API Key 走普通存储的兜底副本。
  Map<String, Object> loggedIn() => <String, Object>{
        'fallback_tonicuisc_api_key': 'test-key',
        'fallback_tonicuisc_device_name': '测试机',
      };

  testWidgets('窄屏（800×600）：顶栏 + 底部 tab 栏，没有侧边栏', (tester) async {
    SharedPreferences.setMockInitialValues(loggedIn());
    await pumpApp(tester, const Size(800, 600));

    expect(find.byType(CupertinoNavigationBar), findsOneWidget);
    expect(find.byType(CupertinoTabBar), findsOneWidget);
    expect(find.byType(NavSidebar), findsNothing);
  });

  testWidgets('宽屏（1400×900）：左侧侧边栏，没有顶栏 / 底部 tab 栏', (tester) async {
    SharedPreferences.setMockInitialValues(loggedIn());
    await pumpApp(tester, const Size(1400, 900));

    expect(find.byType(NavSidebar), findsOneWidget);
    expect(find.byType(CupertinoNavigationBar), findsNothing);
    expect(find.byType(CupertinoTabBar), findsNothing);
  });

  testWidgets('宽屏侧边栏点一下能切 tab', (tester) async {
    SharedPreferences.setMockInitialValues(loggedIn());
    await pumpApp(tester, const Size(1400, 900));

    // 一开始停在「搜索」
    expect(tester.widget<IndexedStack>(find.byType(IndexedStack).first).index, 0);

    // 点侧边栏里的「列表」（限定在 NavSidebar 里找，免得撞上页面内的同名文字）
    await tester.tap(
      find.descendant(of: find.byType(NavSidebar), matching: find.text('列表')),
    );
    await tester.pump(const Duration(milliseconds: 200));

    expect(tester.widget<IndexedStack>(find.byType(IndexedStack).first).index, 1);
  });

  testWidgets('手机横屏（844×390）：够宽但太扁，仍走窄屏布局', (tester) async {
    SharedPreferences.setMockInitialValues(loggedIn());
    await pumpApp(tester, const Size(844, 390));

    expect(find.byType(NavSidebar), findsNothing);
    expect(find.byType(CupertinoTabBar), findsOneWidget);
  });

  testWidgets('未登录：进登录页，不出现外壳（顺带验证过渡壳罩得住 Material 页面）',
      (tester) async {
    SharedPreferences.setMockInitialValues(<String, Object>{});
    await pumpApp(tester, const Size(800, 600));

    expect(find.byType(NavSidebar), findsNothing);
    expect(find.byType(CupertinoTabBar), findsNothing);
  });
}
