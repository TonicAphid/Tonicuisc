import 'package:flutter_test/flutter_test.dart';
import 'package:tonicuisc/models/song.dart';

void main() {
  test('Song.fromJson 解析后端字段', () {
    final song = Song.fromJson(const {
      'id': 'MiguMusicClient:123',
      'source': 'MiguMusicClient',
      'source_label': '咪咕',
      'name': '天地龙鳞',
      'singers': '王力宏',
      'album': '专辑',
      'ext': 'flac',
      'duration': '00:04:05',
      'file_size': '41 MB',
      'cover_url': 'https://example.com/cover.jpg',
      'has_lyric': true,
    });

    expect(song.id, 'MiguMusicClient:123');
    expect(song.sourceLabel, '咪咕');
    expect(song.subtitle, '王力宏 · 专辑 · 00:04:05');
    expect(song.hasLyric, isTrue);
  });

  test('下载文件名过滤非法字符', () {
    final song = Song.fromJson(const {'name': 'a/b:c', 'singers': 'd*e'});
    expect(song.fileBaseName, 'a_b_c - d_e');
  });
}
