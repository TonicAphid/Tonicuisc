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

  /// LRC 的元信息标签，例如 `[by:xxx]`、`[offset:0]`、`[ti:歌名]`——不该当歌词显示。
  static final RegExp _metaTag = RegExp(r'\[[^\]]*\]');

  static LyricSheet parse(String? raw, {String? title, String? artist}) {
    final text = (raw ?? '').replaceAll('\r\n', '\n').trim();
    if (text.isEmpty || text.toUpperCase() == 'NULL') return empty;

    final lines = <LyricLine>[];
    final seenTimes = <int>{};
    for (final rawLine in text.split('\n')) {
      // 先去掉时间戳，再去掉剩下的元信息标签
      final content = rawLine.replaceAll(_stamp, '').replaceAll(_metaTag, '').trim();
      final stamps = _stamp.allMatches(rawLine).toList();
      if (stamps.isEmpty) {
        if (content.isNotEmpty) lines.add(LyricLine(Duration.zero, content));
        continue;
      }
      for (final stamp in stamps) {
        final fraction = stamp.group(3);
        final millis = fraction == null ? 0 : int.parse(fraction.padRight(3, '0').substring(0, 3));
        final time = Duration(
          minutes: int.parse(stamp.group(1)!),
          seconds: int.parse(stamp.group(2)!),
          milliseconds: millis,
        );
        // 同一个时间戳只留第一条：酷我英文歌是「原文 + 翻译」两行同一时间，
        // 留后面的就会变成一直显示中文翻译。
        if (time > Duration.zero && !seenTimes.add(time.inMilliseconds)) continue;
        lines.add(LyricLine(time, content));
      }
    }
    lines.removeWhere((line) => line.text.isEmpty);
    if (lines.isEmpty) return empty;

    final body = _stripHeader(lines, title, artist);
    if (body.isEmpty) return empty;

    final synced = body.any((line) => line.time > Duration.zero);
    if (synced) body.sort((a, b) => a.time.compareTo(b.time));
    return LyricSheet(body, synced);
  }

  static String _norm(String value) =>
      value.toLowerCase().replaceAll(RegExp(r'[\s\-_–—·:：()（）\[\]【】]+'), '');

  /// 去掉开头那几行「歌名 / 歌手 / 歌名 - 歌手」——酷我的 LRC 头部会写这些。
  static List<LyricLine> _stripHeader(List<LyricLine> lines, String? title, String? artist) {
    final wanted = <String>{
      if (title != null) _norm(title),
      if (artist != null) _norm(artist),
      if (title != null && artist != null) _norm('$title$artist'),
      if (title != null && artist != null) _norm('$title-$artist'),
    }..remove('');

    var index = 0;
    while (index < lines.length && lines[index].time == Duration.zero && wanted.contains(_norm(lines[index].text))) {
      index++;
    }
    return index == 0 ? lines : lines.sublist(index);
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
