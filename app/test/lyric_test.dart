import 'package:flutter_test/flutter_test.dart';
import 'package:tonicuisc/models/lyric.dart';

void main() {
  test('解析带时间戳的 LRC', () {
    final sheet = LyricSheet.parse('[00:12.50]第一句\n[00:20.00]第二句\n');

    expect(sheet.synced, isTrue);
    expect(sheet.lines.length, 2);
    expect(sheet.lines.first.text, '第一句');
    expect(sheet.lines.first.time, const Duration(seconds: 12, milliseconds: 500));
    expect(sheet.indexOf(const Duration(seconds: 5)), -1);
    expect(sheet.indexOf(const Duration(seconds: 15)), 0);
    expect(sheet.indexOf(const Duration(seconds: 25)), 1);
  });

  test('纯文本歌词不做时间轴高亮', () {
    final sheet = LyricSheet.parse('只是文字\n第二行');

    expect(sheet.synced, isFalse);
    expect(sheet.lines.length, 2);
    expect(sheet.indexOf(const Duration(seconds: 10)), -1);
  });

  test('空歌词与 NULL', () {
    expect(LyricSheet.parse('').lines, isEmpty);
    expect(LyricSheet.parse('NULL').lines, isEmpty);
    expect(LyricSheet.parse(null).lines, isEmpty);
  });

  test('一行多个时间戳会展开成多行', () {
    final sheet = LyricSheet.parse('[00:01.00][00:10.00]重复');
    expect(sheet.lines.length, 2);
    expect(sheet.lines[1].time, const Duration(seconds: 10));
  });

  test('分秒与毫秒补零', () {
    final sheet = LyricSheet.parse('[1:02.5]短横杠');
    expect(sheet.lines.first.time, const Duration(minutes: 1, seconds: 2, milliseconds: 500));
  });

  test('跳过 [by:] / [offset:] 这类元信息行', () {
    final sheet = LyricSheet.parse('[by:someone]\n[offset:0]\n[ti:大城小爱]\n[00:01.00]第一句\n[00:05.00]第二句');

    expect(sheet.synced, isTrue);
    expect(sheet.lines.length, 2, reason: '元信息行不该当歌词');
    expect(sheet.lines.first.text, '第一句');
  });

  test('整首只有元信息时算没有歌词', () {
    expect(LyricSheet.parse('[by:someone]\n[offset:0]').lines, isEmpty);
  });

  test('同一时间戳只留第一条（英文歌别显示中文翻译）', () {
    final sheet = LyricSheet.parse('[00:10.00]I am what I am\n[00:10.00]我就是我\n[00:15.00]Second line\n[00:15.00]第二行');

    expect(sheet.lines.length, 2);
    expect(sheet.lines.first.text, 'I am what I am');
    expect(sheet.lines.last.text, 'Second line');
  });

  test('拼音在前、中文在后时以中文原文为准', () {
    final sheet = LyricSheet.parse('[00:10.00]qíng tiān\n[00:10.00]晴天\n[00:15.00]gù shì\n[00:15.00]故事');

    expect(sheet.lines.length, 2);
    expect(sheet.lines.first.text, '晴天');
    expect(sheet.lines.last.text, '故事');
  });

  test('解析增强歌词：逐字、翻译、拼音', () {
    final sheet = LyricSheet.parseEnhanced({
      'format': 'enhanced',
      'ready': true,
      'provider': 'kugou',
      'lines': [
        {
          'start': 0,
          'end': 1000,
          'text': '晴天',
          'translation': 'Sunny day',
          'roman': 'qíng tiān',
          'words': [
            {'s': 0, 'e': 500, 't': '晴', 'r': 'qíng'},
            {'s': 500, 'e': 1000, 't': '天', 'r': 'tiān'},
          ],
        },
      ],
    });

    expect(sheet.synced, isTrue);
    expect(sheet.hasWords, isTrue);
    expect(sheet.lines.single.text, '晴天', reason: '原文不能被拼音替换');
    expect(sheet.lines.single.translation, 'Sunny day');
    expect(sheet.lines.single.roman, 'qíng tiān');
    expect(sheet.lines.single.words.length, 2);
    expect(sheet.lines.single.words.first.roman, 'qíng');
  });

  test('逐字进度按行内时间线性计算（进度条式填充）', () {
    const line = LyricLine(
      Duration.zero,
      '晴天',
      words: [
        LyricWord(start: Duration.zero, end: Duration(milliseconds: 500), text: '晴'),
        LyricWord(start: Duration(milliseconds: 500), end: Duration(seconds: 1), text: '天'),
      ],
    );

    expect(line.progressAt(Duration.zero), 0.0);
    expect(line.progressAt(const Duration(milliseconds: 250)), closeTo(0.25, 0.0001));
    expect(line.progressAt(const Duration(milliseconds: 500)), closeTo(0.5, 0.0001));
    expect(line.progressAt(const Duration(seconds: 2)), 1.0);
  });

  test('扫速按每个字的时间差算，不是整行匀速', () {
    const line = LyricLine(
      Duration.zero,
      '晴天',
      words: [
        LyricWord(start: Duration.zero, end: Duration(milliseconds: 900), text: '晴'),
        LyricWord(start: Duration(milliseconds: 900), end: Duration(seconds: 1), text: '天'),
      ],
    );

    // 时间过半了，但第一个字占一半宽度，所以只扫过 1/4
    expect(line.progressAt(const Duration(milliseconds: 450)), closeTo(0.25, 0.0001));
    expect(line.progressAt(const Duration(milliseconds: 900)), closeTo(0.5, 0.0001));
    expect(line.progressAt(const Duration(milliseconds: 950)), closeTo(0.75, 0.0001));
  });

  test('字与字之间的空隙停住不扫', () {
    const line = LyricLine(
      Duration.zero,
      '晴天',
      words: [
        LyricWord(start: Duration.zero, end: Duration(milliseconds: 400), text: '晴'),
        LyricWord(start: Duration(milliseconds: 800), end: Duration(seconds: 1), text: '天'),
      ],
    );

    expect(line.progressAt(const Duration(milliseconds: 600)), closeTo(0.5, 0.0001));
  });

  test('没有逐字时间轴时按整行时间计算进度', () {
    const line = LyricLine(Duration(seconds: 2), '普通歌词');

    expect(line.progressAt(const Duration(seconds: 1)), 0.0);
    expect(line.progressAt(const Duration(seconds: 2)), 1.0);
  });

  test('增强歌词没有 lines 时退回空', () {
    expect(LyricSheet.parseEnhanced({'ready': false}).lines, isEmpty);
    expect(LyricSheet.parseEnhanced(null).lines, isEmpty);
  });

  test('去掉开头的歌名/歌手行', () {
    // 酷我头部写的歌名可能和真实歌名不一样（琵琶曲 vs 琵琶行）
    final sheet = LyricSheet.parse(
      '琵琶曲\n郑浩\n[00:12.00]第一句歌词\n[00:20.00]第二句歌词',
      title: '琵琶行',
      artist: '郑浩',
    );

    expect(sheet.lines.length, 2, reason: '头部两行掐掉');
    expect(sheet.lines.first.text, '第一句歌词');
  });

  test('歌名对得上时也掐掉头部', () {
    final sheet = LyricSheet.parse(
      '[ti:琵琶行]\n琵琶行\n郑浩\n[00:12.00]第一句歌词',
      title: '琵琶行',
      artist: '郑浩',
    );

    expect(sheet.lines.length, 1);
    expect(sheet.lines.first.text, '第一句歌词');
  });

  test('正常歌词不会被误删', () {
    final sheet = LyricSheet.parse(
      '[00:00.00]第一句就是歌词\n[00:05.00]第二句',
      title: '琵琶行',
      artist: '郑浩',
    );

    expect(sheet.lines.length, 2, reason: '和歌名/歌手没关系，别动');
  });

  test('歌词中间和歌名一样的句子不会被误删', () {
    final sheet = LyricSheet.parse('[00:01.00]晴天\n[00:05.00]别的话\n[00:09.00]晴天', title: '晴天');

    expect(sheet.lines.length, 3, reason: '掐头不掐中间');
  });
}
