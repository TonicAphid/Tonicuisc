/// 搜索结果模型，字段与后端 `/api/search` 返回的 item 一一对应。
class Song {
  const Song({
    required this.id,
    required this.source,
    required this.sourceLabel,
    required this.name,
    required this.singers,
    required this.album,
    required this.ext,
    required this.duration,
    required this.fileSize,
    required this.coverUrl,
    required this.hasLyric,
  });

  final String id;
  final String source;
  final String sourceLabel;
  final String name;
  final String singers;
  final String album;
  final String ext;
  final String duration;
  final String fileSize;
  final String coverUrl;
  final bool hasLyric;

  factory Song.fromJson(Map<String, dynamic> json) {
    String str(String key) => (json[key] ?? '').toString();
    return Song(
      id: str('id'),
      source: str('source'),
      sourceLabel: str('source_label'),
      name: str('name'),
      singers: str('singers'),
      album: str('album'),
      ext: str('ext'),
      duration: str('duration'),
      fileSize: str('file_size'),
      coverUrl: str('cover_url'),
      hasLyric: json['has_lyric'] == true,
    );
  }

  String get subtitle => [singers, album, duration].where((e) => e.isNotEmpty).join(' · ');

  /// 下载时使用的文件名（去掉文件系统非法字符）。
  String get fileBaseName =>
      '$name - $singers'.replaceAll(RegExp(r'[\\/:*?"<>|\x00-\x1f]'), '_').trim();
}
