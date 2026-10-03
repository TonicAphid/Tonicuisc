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

  /// 去掉开头那几行「歌名 / 歌手」——酷我的 LRC 头部会写这些。
  ///
  /// 头部那几行不一定和真实歌名完全一样（见过 `琵琶曲` vs 歌名 `琵琶行`），
  /// 所以规则是：开头的短行里，**歌手行**（或紧跟在歌名行后面的歌手行）以及它上面的
  /// 歌名行都算头部；只要有一行既不是歌名也不是歌手，就认为头部结束。
  static List<LyricLine> _stripHeader(List<LyricLine> lines, String? title, String? artist) {
    final wantTitle = title == null ? '' : _norm(title);
    final wantArtist = artist == null ? '' : _norm(artist);
    if (wantTitle.isEmpty && wantArtist.isEmpty) return lines;

    // 只认短名字，免得把「郑浩唱得好」这种歌词行当成人名
    bool isArtist(String text) =>
        wantArtist.isNotEmpty &&
        (text == wantArtist || (text.length <= 12 && text.contains(wantArtist)));

    final limit = lines.length < 3 ? lines.length : 3;
    var cut = 0;
    for (var i = 0; i < limit; i++) {
      // 头部那几行是没有时间轴的；已经带时间轴（哪怕 00:00）就是正式歌词，不能动
      if (lines[i].time != Duration.zero) break;
      final text = _norm(lines[i].text);
      if (text.isEmpty || lines[i].text.length > 15) break; // 头部行都很短
      if (wantTitle.isNotEmpty && text == wantTitle) {
        cut = i + 1;
        continue;
      }
      if (isArtist(text)) {
        cut = i + 1;
        break;
      }
      // 这一行不是歌手，但下一行是 → 那它是歌名那一行，一起掐掉
      if (i + 1 < limit && isArtist(_norm(lines[i + 1].text))) {
        cut = i + 2;
      }
      break;
    }
    return cut == 0 ? lines : lines.sublist(cut);
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
