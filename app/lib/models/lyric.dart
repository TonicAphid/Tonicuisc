/// 歌词模型：兼容音源自带的逐行 LRC，以及服务端 `/api/lyric?format=enhanced`
/// 返回的结构化逐字歌词（含翻译 / 拼音 / 罗马音）。
class LyricWord {
  const LyricWord({
    required this.start,
    required this.end,
    required this.text,
    this.roman = '',
  });

  final Duration start;
  final Duration end;
  final String text;

  /// 这一小段对应的拼音 / 罗马音；没有就是空串。
  final String roman;

  /// 这个词唱到哪了（0~1）。
  ///
  /// 卡拉OK按**词**上色，不按整行：整行一条渐变时，长句折行后第二行会从左边
  /// 重新填充，看起来就像「一句歌词变成两段」。
  double progressAt(Duration position) {
    final span = (end - start).inMilliseconds;
    if (span <= 0) return position >= start ? 1 : 0;
    if (position <= start) return 0;
    if (position >= end) return 1;
    return (position - start).inMilliseconds / span;
  }
}

class LyricLine {
  const LyricLine(
    this.time,
    this.text, {
    this.words = const <LyricWord>[],
    this.translation = '',
    this.roman = '',
  });

  final Duration time;
  final String text;

  /// 逐字时间轴；为空表示这是普通逐行歌词。
  final List<LyricWord> words;

  /// 整行翻译（外语歌常见）。
  final String translation;

  /// 整行拼音 / 罗马音（中文歌拼音、日语 romaji）。
  final String roman;

  bool get hasWords => words.isNotEmpty;

  bool get hasExtra => translation.isNotEmpty || roman.isNotEmpty;

  /// 当前播放位置在这行里的进度（0~1），用来做「进度条式」卡拉OK填充。
  ///
  /// 不是按整行时长匀速：每个字/词占的宽度按字数算，字的起止时间各算各的，
  /// 所以短字扫得快、长字扫得慢，字与字之间的空隙会停住。
  /// 没有逐字时间轴时退化成整行：到点前 0，到点后 1。
  double progressAt(Duration position) {
    if (words.isEmpty) {
      return position >= time ? 1 : 0;
    }
    final start = words.first.start;
    if (position <= start) return 0;

    var total = 0;
    for (final word in words) {
      total += _weight(word.text);
    }
    if (total == 0) return position >= words.last.end ? 1 : 0;

    var done = 0;
    for (final word in words) {
      final weight = _weight(word.text);
      if (position >= word.end) {
        done += weight;
        continue;
      }
      if (position < word.start) {
        // 落在字与字的空隙里：停在当前进度，不要继续扫
        return (done / total).clamp(0.0, 1.0);
      }
      final span = (word.end - word.start).inMilliseconds;
      if (span <= 0) {
        done += weight;
        continue;
      }
      final fraction = (position - word.start).inMilliseconds / span;
      return ((done + fraction * weight) / total).clamp(0.0, 1.0);
    }
    return 1;
  }

  static int _weight(String text) {
    final length = text.runes.length;
    return length == 0 ? 1 : length;
  }
}

class LyricSheet {
  const LyricSheet(this.lines, this.synced, {this.hasWords = false});

  static const LyricSheet empty = LyricSheet(<LyricLine>[], false);

  final List<LyricLine> lines;

  /// 是否有时间轴（纯文本歌词没法跟着进度高亮）。
  final bool synced;

  /// 是否有逐字时间轴（决定要不要做卡拉OK着色）。
  final bool hasWords;

  static final RegExp _stamp = RegExp(r'\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]');

  /// LRC 的元信息标签，例如 `[by:xxx]`、`[offset:0]`、`[ti:歌名]`——不该当歌词显示。
  static final RegExp _metaTag = RegExp(r'\[[^\]]*\]');

  /// 拼音的声调符号：用来识别「同一时间戳的拼音行」，避免把中文原文挤掉。
  static final RegExp _pinyinTone = RegExp(r'[āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ]');
  static final RegExp _cjk = RegExp(r'[\u4e00-\u9fff]');

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
        // 同一个时间戳只留一条：酷我英文歌是「原文 + 翻译」，
        // 但中文歌偶尔会「拼音在前、中文在后」——那种情况要把中文换回来。
        if (time > Duration.zero && !seenTimes.add(time.inMilliseconds)) {
          _preferOriginalOverPinyin(lines, time, content);
          continue;
        }
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

  /// 解析服务端返回的增强歌词 JSON。
  static LyricSheet parseEnhanced(dynamic raw) {
    if (raw is! Map) return empty;
    final rawLines = raw['lines'];
    if (rawLines is! List) return empty;

    final lines = <LyricLine>[];
    for (final item in rawLines) {
      if (item is! Map) continue;
      final words = <LyricWord>[];
      final rawWords = item['words'];
      if (rawWords is List) {
        for (final rawWord in rawWords) {
          if (rawWord is! Map) continue;
          words.add(
            LyricWord(
              start: Duration(milliseconds: _ms(rawWord['s'])),
              end: Duration(milliseconds: _ms(rawWord['e'])),
              text: '${rawWord['t'] ?? ''}',
              roman: '${rawWord['r'] ?? ''}',
            ),
          );
        }
      }
      var text = '${item['text'] ?? ''}';
      if (text.isEmpty) text = words.map((word) => word.text).join();
      lines.add(
        LyricLine(
          Duration(milliseconds: _ms(item['start'])),
          text,
          words: words,
          translation: '${item['translation'] ?? ''}',
          roman: '${item['roman'] ?? ''}',
        ),
      );
    }
    lines.removeWhere((line) => line.text.isEmpty && line.words.isEmpty);
    if (lines.isEmpty) return empty;
    lines.sort((a, b) => a.time.compareTo(b.time));
    return LyricSheet(lines, true, hasWords: lines.any((line) => line.hasWords));
  }

  static int _ms(dynamic value) {
    final number = value is num ? value.toInt() : (int.tryParse('$value') ?? 0);
    return number < 0 ? 0 : number;
  }

  /// 同一时间戳上「拼音 + 中文」并存时，把带声调的拼音行换成中文原文。
  ///
  /// 不能无脑优先中文：英文歌的「原文 + 中文翻译」也是同一时间戳，
  /// 那时候必须保留第一条英文原文。只有第一条带拼音声调时才替换。
  static void _preferOriginalOverPinyin(List<LyricLine> lines, Duration time, String content) {
    if (content.isEmpty || !_cjk.hasMatch(content)) return;
    for (var i = 0; i < lines.length; i++) {
      final existing = lines[i];
      if (existing.time != time) continue;
      if (existing.text == content) return;
      if (_pinyinTone.hasMatch(existing.text) && !_cjk.hasMatch(existing.text)) {
        lines[i] = LyricLine(time, content);
      }
      return;
    }
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
