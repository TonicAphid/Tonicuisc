/// LRC 歌词解析：后端 `/api/lyric/{id}` 返回的可能是带时间戳的 LRC，也可能是纯文本。
class LyricLine {
  const LyricLine(this.time, this.text);

  final Duration time;
  final String text;
}

class LyricSheet {
  const LyricSheet(this.lines, this.synced);

  static const LyricSheet empty = LyricSheet(<LyricLine>[], false);

  final List<LyricLine> lines;

  /// 是否有时间轴（纯文本歌词没法跟着进度高亮）。
  final bool synced;

  static final RegExp _stamp = RegExp(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]');

  static LyricSheet parse(String? raw) {
    final text = (raw ?? '').replaceAll('\r\n', '\n').trim();
    if (text.isEmpty || text.toUpperCase() == 'NULL') return empty;

    final lines = <LyricLine>[];
    for (final rawLine in text.split('\n')) {
      final content = rawLine.replaceAll(_stamp, '').trim();
      final stamps = _stamp.allMatches(rawLine).toList();
      if (stamps.isEmpty) {
        if (content.isNotEmpty) lines.add(LyricLine(Duration.zero, content));
        continue;
      }
      for (final stamp in stamps) {
        final fraction = stamp.group(3);
        final millis = fraction == null ? 0 : int.parse(fraction.padRight(3, '0').substring(0, 3));
        lines.add(
          LyricLine(
            Duration(minutes: int.parse(stamp.group(1)!), seconds: int.parse(stamp.group(2)!), milliseconds: millis),
            content,
          ),
        );
      }
    }
    if (lines.isEmpty) return empty;

    final synced = lines.any((line) => line.time > Duration.zero);
    if (synced) lines.sort((a, b) => a.time.compareTo(b.time));
    return LyricSheet(lines, synced);
  }

  /// 当前播放位置对应的歌词行号，-1 表示还没有到第一句。
  int indexOf(Duration position) {
    if (!synced || lines.isEmpty) return -1;
    var index = -1;
    for (var i = 0; i < lines.length; i++) {
      if (lines[i].time <= position) {
        index = i;
      } else {
        break;
      }
    }
    return index;
  }
}
