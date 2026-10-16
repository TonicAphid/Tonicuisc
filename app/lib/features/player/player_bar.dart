import 'package:flutter/cupertino.dart';

import '../../core/api_client.dart';
import 'now_playing_page.dart';
import 'player_controller.dart';
import '../library/library_state.dart';
import '../../shared/song_avatar.dart';

/// 底部播放条（苹果风）：封面 + 曲名 + 播放/暂停 + 进度条。
///
/// 点左侧信息区展开全屏播放页。播放/暂停用 `CupertinoButton`，自带苹果那套
/// 按下变淡的反馈；换歌时曲名 / 歌手整块淡入淡出。
class PlayerBar extends StatelessWidget {
  const PlayerBar({
    super.key,
    required this.controller,
    required this.api,
    required this.library,
    this.onUnauthorized,
  });

  final PlayerController controller;
  final ApiClient api;
  final LibraryState library;

  /// 401 交给外壳处理；不给就只在播放页里弹提示。
  final Future<void> Function(String message)? onUnauthorized;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final song = controller.current;
        if (song == null) return const SizedBox.shrink();
        final label = CupertinoColors.label.resolveFrom(context);
        final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
        final total = controller.duration ?? Duration.zero;
        final position =
            controller.position > total && total > Duration.zero ? total : controller.position;
        return DecoratedBox(
          decoration: BoxDecoration(
            color: CupertinoColors.secondarySystemGroupedBackground.resolveFrom(context),
            border: Border(
              top: BorderSide(color: CupertinoColors.separator.resolveFrom(context), width: 0.5),
            ),
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 6, 12, 4),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (controller.error != null)
                  Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      controller.error!,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        fontSize: 12,
                        color: CupertinoColors.systemRed.resolveFrom(context),
                      ),
                    ),
                  ),
                Row(
                  children: [
                    Expanded(
                      child: GestureDetector(
                        behavior: HitTestBehavior.opaque,
                        onTap: () => NowPlayingPage.open(
                          context,
                          controller,
                          api,
                          library,
                          onUnauthorized: onUnauthorized,
                        ),
                        child: Row(
                          children: [
                            SongAvatar(song: song, size: 40, radius: 6),
                            const SizedBox(width: 10),
                            Expanded(
                              child: AnimatedSwitcher(
                                duration: const Duration(milliseconds: 260),
                                switchInCurve: Curves.easeOut,
                                switchOutCurve: Curves.easeIn,
                                // key 挂在 song.id 上：换歌时整块淡入淡出
                                child: Column(
                                  key: ValueKey(song.id),
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                      song.name,
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(
                                        fontSize: 14,
                                        fontWeight: FontWeight.w500,
                                        color: label,
                                      ),
                                    ),
                                    Text(
                                      '${song.singers} · ${song.sourceLabel}',
                                      maxLines: 1,
                                      overflow: TextOverflow.ellipsis,
                                      style: TextStyle(fontSize: 12, color: secondary),
                                    ),
                                  ],
                                ),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                    if (controller.loading)
                      const Padding(
                        padding: EdgeInsets.symmetric(horizontal: 14),
                        child: CupertinoActivityIndicator(),
                      )
                    else
                      CupertinoButton(
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                        pressedOpacity: 0.5,
                        onPressed: controller.toggle,
                        child: Icon(
                          controller.playing ? CupertinoIcons.pause_circle_fill : CupertinoIcons.play_circle_fill,
                          size: 32,
                          color: label,
                        ),
                      ),
                  ],
                ),
                Row(
                  children: [
                    Text(formatDuration(position), style: TextStyle(fontSize: 11, color: secondary)),
                    Expanded(
                      child: CupertinoSlider(
                        value: total.inMilliseconds == 0
                            ? 0
                            : position.inMilliseconds.clamp(0, total.inMilliseconds).toDouble(),
                        max: total.inMilliseconds == 0 ? 1 : total.inMilliseconds.toDouble(),
                        onChanged: total.inMilliseconds == 0
                            ? null
                            : (value) => controller.seek(Duration(milliseconds: value.round())),
                      ),
                    ),
                    Text(formatDuration(controller.duration), style: TextStyle(fontSize: 11, color: secondary)),
                  ],
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
