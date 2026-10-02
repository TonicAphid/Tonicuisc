import 'package:flutter/material.dart';

import '../api/api_client.dart';
import '../models/song.dart';
import '../player/player_controller.dart';
import '../widgets/player_bar.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  final TextEditingController _keyword = TextEditingController();
  final PlayerController _player = PlayerController();

  late ApiClient _api = ApiClient(defaultBaseUrl());
  final Set<String> _sources = <String>{'migu', 'kuwo'};
  List<Song> _results = const [];
  bool _searching = false;
  String? _error;
  double? _elapsed;
  double? _serverElapsed;
  String _baseUrl = defaultBaseUrl();

  @override
  void initState() {
    super.initState();
    _bootstrap();
  }

  Future<void> _bootstrap() async {
    final url = await ApiClient.loadBaseUrl();
    if (!mounted) return;
    setState(() {
      _baseUrl = url;
      _api = ApiClient(url);
    });
  }

  @override
  void dispose() {
    _keyword.dispose();
    _player.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final keyword = _keyword.text.trim();
    if (keyword.isEmpty || _searching) return;
    setState(() {
      _searching = true;
      _error = null;
      _elapsed = null;
      _serverElapsed = null;
    });
    final stopwatch = Stopwatch()..start();
    try {
      final result = await _api.search(keyword, sources: _sources.toList());
      stopwatch.stop();
      if (!mounted) return;
      setState(() {
        _results = result.items;
        // 客户端实测的总耗时（含网络往返），服务端耗时单独显示
        _elapsed = stopwatch.elapsedMilliseconds / 1000;
        _serverElapsed = result.elapsed;
      });
    } catch (err) {
      stopwatch.stop();
      if (!mounted) return;
      setState(() {
        _results = const [];
        _error = '$err';
      });
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  Future<void> _download(Song song) async {
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(content: Text('正在下载 ${song.name} …')));
    try {
      final dir = await ApiClient.downloadDirectory();
      final path = await _api.downloadTo(song, dir);
      messenger.showSnackBar(SnackBar(content: Text('已保存到 $path')));
    } catch (err) {
      messenger.showSnackBar(SnackBar(content: Text('下载失败：$err')));
    }
  }

  Future<void> _editServerUrl() async {
    final controller = TextEditingController(text: _baseUrl);
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('后端地址'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(hintText: 'http://127.0.0.1:8000'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('取消')),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    if (value == null || value.isEmpty) return;
    await ApiClient.saveBaseUrl(value);
    setState(() {
      _baseUrl = value;
      _api = ApiClient(value);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Tonicuisc'),
        actions: [
          IconButton(tooltip: '后端地址', onPressed: _editServerUrl, icon: const Icon(Icons.dns_outlined)),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _keyword,
                    textInputAction: TextInputAction.search,
                    onSubmitted: (_) => _search(),
                    decoration: const InputDecoration(
                      prefixIcon: Icon(Icons.search),
                      hintText: '搜索歌曲 / 歌手',
                      border: OutlineInputBorder(),
                      isDense: true,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                FilledButton(onPressed: _searching ? null : _search, child: const Text('搜索')),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                const Text('音源：'),
                for (final entry in const {'migu': '咪咕', 'kuwo': '酷我'}.entries)
                  FilterChip(
                    label: Text(entry.value),
                    selected: _sources.contains(entry.key),
                    onSelected: (selected) => setState(() {
                      selected ? _sources.add(entry.key) : _sources.remove(entry.key);
                    }),
                  ),
                const Spacer(),
                if (_baseUrl.isNotEmpty)
                  Text(_baseUrl, style: Theme.of(context).textTheme.labelSmall),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 2, 16, 6),
            child: Row(
              children: [
                if (_searching) ...[
                  const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
                  const SizedBox(width: 10),
                  const Text('正在进行…'),
                ] else if (_elapsed != null)
                  Expanded(
                    child: Text(
                      '搜索完成 · 用时 ${_elapsed!.toStringAsFixed(1)} 秒'
                      '${_serverElapsed == null ? '' : '（服务端 ${_serverElapsed!.toStringAsFixed(1)} 秒）'}'
                      ' · 共 ${_results.length} 首',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
              ],
            ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.all(16),
              child: Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ),
          Expanded(
            child: _results.isEmpty
                ? Center(
                    child: Text(
                      _searching ? '正在进行…' : '输入关键词开始搜索',
                      style: Theme.of(context).textTheme.bodyMedium,
                    ),
                  )
                : ListView.separated(
                    itemCount: _results.length,
                    separatorBuilder: (_, __) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final song = _results[index];
                      final isCurrent = _player.current?.id == song.id;
                      return ListTile(
                        leading: CircleAvatar(
                          child: Text(song.sourceLabel.isEmpty ? '?' : song.sourceLabel.substring(0, 1)),
                        ),
                        title: Text(song.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                        subtitle: Text(song.subtitle, maxLines: 1, overflow: TextOverflow.ellipsis),
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(song.ext.toUpperCase(), style: Theme.of(context).textTheme.labelSmall),
                            IconButton(
                              tooltip: '下载',
                              icon: const Icon(Icons.download_outlined),
                              onPressed: () => _download(song),
                            ),
                          ],
                        ),
                        selected: isCurrent,
                        onTap: () => _player.play(song, _api),
                      );
                    },
                  ),
          ),
        ],
      ),
      bottomNavigationBar: PlayerBar(controller: _player, api: _api),
    );
  }
}
