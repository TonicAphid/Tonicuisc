import 'package:flutter/material.dart';

import '../player/player_controller.dart';
import '../widgets/play_mode_icons.dart';
import '../widgets/song_avatar.dart';

/// 当前播放队列的列表，全屏播放页和「列表」tab 共用。
class QueueList extends StatelessWidget {
  const QueueList({
    super.key,
    required this.controller,
    this.shrinkWrap = false,
    this.padding = const EdgeInsets.symmetric(vertical: 4),
  });

  final PlayerController controller;
  final bool shrinkWrap;
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: controller,
      builder: (context, _) {
        final queue = controller.queue;
        if (queue.isEmpty) {
          return const Center(child: Padding(padding: EdgeInsets.all(24), child: Text('播放列表是空的')));
        }
        return ListView.separated(
          shrinkWrap: shrinkWrap,
          physics: shrinkWrap ? const NeverScrollableScrollPhysics() : null,
          padding: padding,
          itemCount: queue.length,
          separatorBuilder: (_, __) => const Divider(height: 1),
          itemBuilder: (context, index) {
            final song = queue[index];
            final isCurrent = index == controller.index;
            return ListTile(
              dense: true,
              leading: SongAvatar(song: song, size: 40, highlight: isCurrent),
              title: Text(
                song.name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: isCurrent ? TextStyle(color: Theme.of(context).colorScheme.primary) : null,
              ),
              subtitle: Text(song.singers, maxLines: 1, overflow: TextOverflow.ellipsis),
              trailing: IconButton(
                tooltip: '从列表移除',
                icon: const Icon(Icons.close),
                onPressed: () => controller.removeAt(index),
              ),
              onTap: () => controller.playAt(index),
            );
          },
        );
      },
    );
  }
}

/// 播放队列整页（「列表」tab 里点「播放列表」进来）。
class QueuePage extends StatelessWidget {
  const QueuePage({super.key, required this.controller});

  final PlayerController controller;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('播放列表'),
        actions: [
          AnimatedBuilder(
            animation: controller,
            builder: (context, _) => TextButton.icon(
              onPressed: controller.cycleMode,
              icon: Icon(playModeIcon(controller.mode)),
              label: Text(controller.mode.label),
            ),
          ),
        ],
      ),
      body: QueueList(controller: controller),
    );
  }
}

/// 播放队列底部弹层（全屏播放页里点列表按钮）。
class QueueSheet extends StatelessWidget {
  const QueueSheet({super.key, required this.controller});

  final PlayerController controller;

  static Future<void> show(BuildContext context, PlayerController controller) {
    return showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (_) => SizedBox(
        height: MediaQuery.of(context).size.height * 0.6,
        child: QueueSheet(controller: controller),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        AnimatedBuilder(
          animation: controller,
          builder: (context, _) => Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(
              children: [
                const Text('播放列表', style: TextStyle(fontWeight: FontWeight.w600)),
                const SizedBox(width: 8),
                Text('${controller.queue.length} 首', style: Theme.of(context).textTheme.labelSmall),
                const Spacer(),
                TextButton.icon(
                  onPressed: controller.cycleMode,
                  icon: Icon(playModeIcon(controller.mode), size: 18),
                  label: Text(controller.mode.label),
                ),
              ],
            ),
          ),
        ),
        const Divider(height: 1),
        Expanded(child: QueueList(controller: controller)),
      ],
    );
  }
}
