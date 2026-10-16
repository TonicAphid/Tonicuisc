import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:tonicuisc/app/layout.dart';

void main() {
  test('桌面窗口走桌面布局', () {
    expect(useDesktopLayout(const Size(1280, 800)), isTrue);
    expect(useDesktopLayout(const Size(1920, 1080)), isTrue);
  });

  test('刚好到断点也算桌面', () {
    expect(useDesktopLayout(const Size(kDesktopMinWidth, 600)), isTrue);
    expect(useDesktopLayout(const Size(kDesktopMinWidth - 1, 600)), isFalse);
  });

  test('手机竖屏走手机布局', () {
    expect(useDesktopLayout(const Size(390, 844)), isFalse);
    expect(useDesktopLayout(const Size(360, 800)), isFalse);
  });

  test('手机横屏够宽，但短边太短，还是手机布局', () {
    // 844x390：宽度过了 840，短边只有 390 —— 塞一条左侧导航很难看
    expect(useDesktopLayout(const Size(844, 390)), isFalse);
    expect(useDesktopLayout(const Size(932, 430)), isFalse);
  });

  test('平板竖屏宽度不够，仍用手机布局；横屏才切桌面', () {
    expect(useDesktopLayout(const Size(768, 1024)), isFalse);
    expect(useDesktopLayout(const Size(1024, 768)), isTrue);
  });
}
