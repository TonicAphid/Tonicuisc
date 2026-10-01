import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../pages/now_playing_page.dart';
import '../player/player_controller.dart';

/// 底部播放条：播放/暂停、进度、拖动跳转；点一下展开全屏播放页。
class PlayerBar extends StatelessWidget {
  const PlayerBar({super.key, required this.controller, required this.api});

  final PlayerController controller;
  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final song = controller.current;
        if (song == null) return const SizedBox.shrink();
        final theme = Theme.of(context);
        final total = controller.duration ?? Duration.zero;
        final position = controller.position > total && total > Duration.zero ? total : controller.position;
        return Material(
          color: theme.colorScheme.surfaceContainerHighest,
          child: SafeArea(
            top: false,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (controller.error != null)
                    Align(
                      alignment: Alignment.centerLeft,
                      child: Text(controller.error!, style: TextStyle(color: theme.colorScheme.error, fontSize: 12)),
                    ),
                  Row(
                    children: [
                      Expanded(
                        child: InkWell(
                          onTap: () => NowPlayingPage.open(context, controller, api),
                          child: Row(
                            children: [
                              if (song.coverUrl.isNotEmpty)
                                ClipRRect(
                                  borderRadius: BorderRadius.circular(6),
                                  child: Image.network(
                                    song.coverUrl,
                                    width: 40,
                                    height: 40,
                                    fit: BoxFit.cover,
                                    errorBuilder: (_, __, ___) => const SizedBox(width: 40, height: 40),
                                  ),
                                ),
                              const SizedBox(width: 10),
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(song.name,
                                        maxLines: 1, overflow: TextOverflow.ellipsis, style: theme.textTheme.titleSmall),
                                    Text('${song.singers} · ${song.sourceLabel}',
                                        maxLines: 1, overflow: TextOverflow.ellipsis, style: theme.textTheme.bodySmall),
                                  ],
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                      if (controller.loading)
                        const Padding(
                          padding: EdgeInsets.symmetric(horizontal: 12),
                          child: SizedBox(width: 20, height: 20, child: CircularProgressIndicator(strokeWidth: 2)),
                        )
                      else
                        IconButton(
                          tooltip: controller.playing ? '暂停' : '播放',
                          iconSize: 36,
                          icon: Icon(controller.playing ? Icons.pause_circle : Icons.play_circle),
                          onPressed: controller.toggle,
                        ),
                    ],
                  ),
                  Row(
                    children: [
                      Text(formatDuration(position), style: theme.textTheme.labelSmall),
                      Expanded(
                        child: Slider(
                          value: total.inMilliseconds == 0
                              ? 0
                              : position.inMilliseconds.clamp(0, total.inMilliseconds).toDouble(),
                          max: total.inMilliseconds == 0 ? 1 : total.inMilliseconds.toDouble(),
                          onChanged: total.inMilliseconds == 0
                              ? null
                              : (value) => controller.seek(Duration(milliseconds: value.round())),
                        ),
                      ),
                      Text(formatDuration(controller.duration), style: theme.textTheme.labelSmall),
                    ],
                  ),
                ],
              ),
            ),
          ),
        );
      },
    );
  }
}
