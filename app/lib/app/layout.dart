import 'package:flutter/widgets.dart';

/// 响应式断点：一套代码、两套布局。
///
/// 宽到能放下「左侧导航 + 宽内容区」就切桌面布局，否则用手机布局
/// （底部三个 tab）。
///
/// **只看宽度不够**：手机横屏也能到 840+ 逻辑像素，但短边只有 ~390，
/// 那种「宽而扁」的比例塞一条左侧导航很难看，所以同时要求短边够长——
/// 只有平板横屏 / 桌面窗口才满足。
const double kDesktopMinWidth = 840;
const double kDesktopMinShortestSide = 480;

/// 桌面布局下内容区的最大宽度：4K 屏上别让列表拉成一条横线。
/// 超过就左右平分留白（不是把内容压扁，约束和手机布局完全一样）。
const double kDesktopMaxContentWidth = 1200;

/// 当前窗口该用桌面布局还是手机布局。
bool useDesktopLayout(Size size) =>
    size.width >= kDesktopMinWidth && size.shortestSide >= kDesktopMinShortestSide;
