import 'package:flutter/material.dart';

import '../models/song.dart';

/// 歌曲封面：优先显示搜到的 cover，没有封面或加载失败才退回音源首字（咪/酷）。
class SongAvatar extends StatelessWidget {
  const SongAvatar({
    super.key,
    required this.song,
    this.size = 44,
    this.radius = 8,
    this.highlight = false,
  });

  final Song song;
  final double size;
  final double radius;

  /// 正在播放的那首：加一圈主题色描边。
  final bool highlight;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final fallback = Container(
      width: size,
      height: size,
      color: scheme.surfaceContainerHighest,
      alignment: Alignment.center,
      child: Text(
        song.sourceLabel.isEmpty ? '♪' : song.sourceLabel.substring(0, 1),
        style: TextStyle(fontSize: size * 0.4, color: scheme.onSurfaceVariant),
      ),
    );

    final image = song.coverUrl.isEmpty
        ? fallback
        : Image.network(
            song.coverUrl,
            width: size,
            height: size,
            fit: BoxFit.cover,
            errorBuilder: (_, __, ___) => fallback,
            loadingBuilder: (context, child, progress) => progress == null ? child : fallback,
          );

    return Container(
      width: size,
      height: size,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        border: highlight ? Border.all(color: scheme.primary, width: 2) : null,
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(radius - (highlight ? 2 : 0)),
        child: image,
      ),
    );
  }
}
