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
}
