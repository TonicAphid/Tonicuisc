import 'dart:async';
import 'dart:io';

import 'package:flutter/cupertino.dart';
import 'package:flutter/services.dart' show LogicalKeyboardKey, TextEditingValue;
// 搜索框用 Material 的 `TextField`（见 `_searchRow` 的注释）：只引这几个符号，
// 避免和 Cupertino 里同名的 `showDialog` / `TextTheme` 之类撞车。
// 图标改用 `CupertinoIcons`，所以不再需要 Material 的 `Icons` / `Icon` 字体。
import 'package:flutter/material.dart'
    show BorderSide, InputDecoration, OutlineInputBorder, TextField, TextInputAction;

import '../../core/api_client.dart';
import '../../core/download_target.dart';
import '../../models/song.dart';
import '../player/player_controller.dart';
import '../../shared/cover_cache.dart';
import '../library/library_state.dart';
import '../../shared/song_avatar.dart';
import '../../shared/toast.dart';
import 'artist_page.dart';

/// 搜索页（首页 tab）：搜索、试听、下载、点红心。
///
/// 整体是 Cupertino 风格，但**搜索框用 Material 的 `TextField`**（原因见
/// `_searchRow`）；列表行自己画（`ListTile` 那套 Material 的选中态 / 水波纹
/// 在这里不合适）。音源现在固定酷我，不再显示选择胶囊。
class SearchPage extends StatefulWidget {
  const SearchPage({
    super.key,
    required this.api,
    required this.player,
    required this.library,
    required this.onUnauthorized,
  });

  final ApiClient api;
  final PlayerController player;
  final LibraryState library;
  final Future<void> Function(String message) onUnauthorized;

  @override
  State<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends State<SearchPage> {
  /// 一次要多少条；滑到底再要同样多（服务端会去重，不会给重复的）。
  static const int _pageSize = 15;

  /// 打字停手多久才自动搜。太短会把每个字都发出去，太长像没反应。
  static const Duration _typingPause = Duration(milliseconds: 500);

  /// 自动搜至少几个字：单字命中太多，而且拼音组字中途容易误触发。
  /// 单个字照样能搜——按回车就行。
  static const int _minAutoLength = 2;

  final TextEditingController _keyword = TextEditingController();
  final FocusNode _focus = FocusNode();
  final ScrollController _scroll = ScrollController();

  /// 打字防抖计时器；`null` = 当前没有待发的搜索。
  Timer? _typing;

  List<Song> _results = const [];

  /// 服务端那边已经取过多少条。**不能用 `_results.length`**：翻页回来的重复条目会被去重，
  /// 一旦用显示条数当 offset，就会反复要同一段（永远要不到新歌，还一直显示「加载更多」）。
  int _offset = 0;

  /// 搜索代次：每次重新搜索 +1，让还在飞的翻页请求知道自己的结果该被丢掉。
  int _epoch = 0;

  bool _searching = false;
  bool _loadingMore = false;
  bool _hasMore = false;
  String _lastKeyword = '';
  String? _error;
  double? _elapsed;
  double? _serverElapsed;

  @override
  void initState() {
    super.initState();
    _scroll.addListener(_onScroll);
  }

  @override
  void dispose() {
    _typing?.cancel();
    _scroll.dispose();
    _focus.dispose();
    _keyword.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scroll.hasClients) return;
    final remaining = _scroll.position.maxScrollExtent - _scroll.position.pixels;
    if (remaining < 400) _loadMore();
  }

  /// 输入变化：组字中不搜（否则拼音还没上屏就拿半截字母去查），停手再搜。
  ///
  /// 注意先 `cancel` 再判断组字：已经排好的那个计时器不会自己看输入法状态，
  /// 不取消的话「打完两个字、接着打第三个字的拼音」会被上一次的计时器
  /// 拿「周杰lun」这种半截内容去查。
  void _onChanged(String _) {
    _typing?.cancel();
    if (!_keyword.value.composing.isCollapsed) return;
    final keyword = _keyword.text.trim();
    if (keyword.isEmpty) {
      // 清空输入框 = 回到初始态，不该再发一个空关键词的请求
      _reset();
      return;
    }
    if (keyword == _lastKeyword) return; // 改了又改回来，没必要再搜一遍
    if (keyword.length < _minAutoLength) return;
    _typing = Timer(_typingPause, _search);
  }

  /// 回车 / 键盘上的「搜索」：立刻搜，不等防抖。
  void _submit() {
    _typing?.cancel();
    if (_keyword.text.trim().isEmpty) {
      _reset();
      return;
    }
    _search();
  }

  /// 点 × 或按 Esc：清输入、清结果，焦点留在框里（方便接着打下一首）。
  void _clear() {
    _typing?.cancel();
    _keyword.clear();
    _reset();
    _focus.requestFocus();
  }

  /// 回到「还没搜过」的状态，同时让还在飞的请求作废。
  void _reset() {
    _epoch++;
    setState(() {
      _results = const [];
      _offset = 0;
      _hasMore = false;
      _loadingMore = false;
      _searching = false;
      _lastKeyword = '';
      _error = null;
      _elapsed = null;
      _serverElapsed = null;
    });
  }

  Future<void> _search() async {
    final keyword = _keyword.text.trim();
    if (keyword.isEmpty) return;
    // 每次搜索换一代：旧请求回来时 epoch 对不上就丢掉。
    // 以前这里是 `if (_searching) return;`——搜着的时候改词再回车**毫无反应**，
    // 看着像卡死；现在改成「新的永远顶掉旧的」。
    final epoch = ++_epoch;
    setState(() {
      _searching = true;
      _loadingMore = false;
      _error = null;
      _elapsed = null;
      _serverElapsed = null;
      _hasMore = false;
      _lastKeyword = keyword;
    });
    final stopwatch = Stopwatch()..start();
    try {
      final result = await widget.api.search(
        keyword,
        offset: 0,
        limit: _pageSize,
        refresh: true,
      );
      stopwatch.stop();
      if (!mounted || epoch != _epoch) return; // 已经被更新的搜索顶掉了
      setState(() {
        _results = result.items;
        _offset = result.items.length;
        _hasMore = result.hasMore;
        _elapsed = stopwatch.elapsedMilliseconds / 1000;
        _serverElapsed = result.elapsed;
      });
      unawaited(CoverCache.resolve(widget.api, result.items));
    } catch (err) {
      stopwatch.stop();
      if (!mounted || epoch != _epoch) return; // 旧请求的失败不该盖掉新结果
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      setState(() {
        _results = const [];
        _offset = 0;
        _error = friendlyError(err, widget.api.baseUrl);
      });
    } finally {
      if (mounted && epoch == _epoch) setState(() => _searching = false);
    }
  }

  /// 滑到底：再要 15 条，按 id 去重，绝不重复显示。
  Future<void> _loadMore() async {
    if (!_hasMore || _loadingMore || _searching || _lastKeyword.isEmpty) return;
    final epoch = _epoch;
    setState(() => _loadingMore = true);
    try {
      final result = await widget.api.search(
        _lastKeyword,
        offset: _offset,
        limit: _pageSize,
      );
      if (!mounted) return;
      if (epoch != _epoch) return; // 期间用户已经搜了别的词，这批结果不要了
      final existing = _results.map((song) => song.id).toSet();
      final fresh = result.items.where((song) => !existing.contains(song.id)).toList();
      setState(() {
        _results = [..._results, ...fresh];
        // 偏移量按「服务端给了多少」推进：即使去重去掉了几条，下一次也要接着往后要
        _offset += result.items.length;
        // 服务端说没有了、或者这一页压根是空的，就不再要了
        _hasMore = result.hasMore && result.items.isNotEmpty;
      });
      unawaited(CoverCache.resolve(widget.api, fresh));
    } on UnauthorizedException catch (err) {
      // 翻页也可能是 401：不能悄悄吞掉，否则用户停在过期的 key 上出不去
      if (!mounted) return;
      setState(() => _hasMore = false);
      await widget.onUnauthorized('$err');
    } catch (_) {
      if (mounted) setState(() => _hasMore = false);
    } finally {
      if (mounted) setState(() => _loadingMore = false);
    }
  }

  Future<void> _download(Song song) async {
    try {
      final dir = await DownloadTarget.resolve();
      if (dir == null) return; // 桌面端取消了选择文件夹
      if (!mounted) return;
      Toast.show(context, '正在下载 ${song.name} …');
      final path = await widget.api.downloadTo(song, dir);
      if (!mounted) return;
      Toast.show(context, '已保存到 $path');
    } catch (err) {
      // macOS 沙盒重启后旧目录可能没权限了：清掉记录，下次重新选
      if (err is FileSystemException && Platform.isMacOS) await DownloadTarget.clear();
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      if (!mounted) return;
      Toast.show(context, '下载失败：$err');
    }
  }

  Future<void> _toggleLike(Song song) async {
    try {
      final wasLiked = widget.library.liked(song.id);
      await widget.library.toggle('like', song.id);
      if (!mounted) return;
      Toast.show(
        context,
        wasLiked ? '已取消喜欢 ${song.name}' : '已加入我喜欢 ${song.name}',
        duration: const Duration(seconds: 1),
      );
    } catch (err) {
      if (err is UnauthorizedException) {
        await widget.onUnauthorized('$err');
        return;
      }
      if (!mounted) return;
      Toast.show(context, '操作失败：$err');
    }
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: widget.library,
      builder: (context, _) => Column(
        children: [
          _searchRow(context),
          _statusRow(context),
          Expanded(child: _body(context)),
        ],
      ),
    );
  }

  /// 搜索框。
  ///
  /// 输入框用 **Material 的 `TextField`**，不是 `CupertinoSearchTextField`：
  /// 0.2.x 把搜索框换成 Cupertino 版本之后，Windows 上焦点 / 键盘输入就不正常
  /// （点进输入框打不出字、输入法组字不生效），换回 Material 输入框才恢复。
  /// 外观仍按苹果风画（圆角、填充底色、无边框），只借它的文本编辑能力。
  ///
  /// 交互（这轮重写的重点）：
  /// - 打字停手 [_typingPause] 自动搜（≥ [_minAutoLength] 字），组字中不搜；
  /// - 回车 / 键盘上的「搜索」立刻搜，不等防抖；
  /// - 有内容时右侧出现 ×，点一下清空回到初始态；Esc 同效；
  /// - 搜索中左侧放大镜换成小转圈，右侧 × 仍在（点它 = 清空并作废这轮请求）；
  /// - 聚焦时描一圈主色细边——以前四条边都是 `none`，压根看不出焦点在哪。
  Widget _searchRow(BuildContext context) {
    final accent = CupertinoTheme.of(context).primaryColor;
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    final label = CupertinoColors.label.resolveFrom(context);
    final radius = BorderRadius.circular(12);
    final idle = OutlineInputBorder(borderRadius: radius, borderSide: BorderSide.none);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 6),
      child: CallbackShortcuts(
        bindings: {
          // Esc：框里有字就清空，已经空了就退出输入（桌面端习惯）
          const SingleActivator(LogicalKeyboardKey.escape): () {
            if (_keyword.text.isEmpty) {
              _focus.unfocus();
            } else {
              _clear();
            }
          },
        },
        // 输入框要在「有字 / 没字」「搜索中 / 空闲」之间换右侧图标，
        // 所以跟着 controller 重建这一层（只重建输入框，不碰下面的列表）。
        child: ValueListenableBuilder<TextEditingValue>(
          valueListenable: _keyword,
          builder: (context, value, _) => TextField(
            controller: _keyword,
            focusNode: _focus,
            textInputAction: TextInputAction.search,
            onSubmitted: (_) => _submit(),
            onChanged: _onChanged,
            cursorColor: accent,
            style: TextStyle(fontSize: 15, color: label),
            decoration: InputDecoration(
              hintText: '搜索歌曲 / 歌手',
              hintStyle: TextStyle(fontSize: 15, color: secondary),
              // 搜索中把放大镜换成小转圈，右侧的 × 始终留着——
              // 一边搜一边想清空时，不至于「点了没反应」（× 会顺手作废这轮请求）
              prefixIcon: _searching
                  ? const CupertinoActivityIndicator(radius: 7)
                  : Icon(CupertinoIcons.search, size: 18, color: secondary),
              prefixIconConstraints: const BoxConstraints(minWidth: 38, minHeight: 38),
              suffixIcon: _trailing(context, value.text),
              suffixIconConstraints: const BoxConstraints(minWidth: 38, minHeight: 38),
              isDense: true,
              filled: true,
              fillColor: CupertinoColors.tertiarySystemFill.resolveFrom(context),
              contentPadding: const EdgeInsets.symmetric(vertical: 12),
              border: idle,
              enabledBorder: idle,
              disabledBorder: idle,
              focusedBorder: OutlineInputBorder(
                borderRadius: radius,
                borderSide: BorderSide(color: accent.withValues(alpha: 0.55), width: 1.5),
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// 搜索框右侧：有内容就显示 ×（搜索中也留着，点了等于取消这轮）。
  Widget? _trailing(BuildContext context, String text) {
    if (text.isEmpty) return null;
    return CupertinoButton(
      padding: const EdgeInsets.all(6),
      minimumSize: Size.zero,
      pressedOpacity: 0.4,
      borderRadius: BorderRadius.circular(12),
      onPressed: _clear,
      child: Icon(
        CupertinoIcons.xmark_circle_fill,
        size: 16,
        color: CupertinoColors.secondaryLabel.resolveFrom(context),
      ),
    );
  }

  /// 状态行：搜索中 / 搜索完成 / 出错（带「重试」）。
  ///
  /// 以前错误是状态行下面另起一块红字，出错时既挤又没法一键重来；
  /// 现在合成一行，错误直接跟一个「重试」。
  Widget _statusRow(BuildContext context) {
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    final red = CupertinoColors.systemRed.resolveFrom(context);
    final Widget child;
    if (_error != null) {
      child = Row(
        children: [
          Icon(CupertinoIcons.exclamationmark_circle, size: 13, color: red),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              _error!,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12, color: red),
            ),
          ),
          CupertinoButton(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
            minimumSize: Size.zero,
            onPressed: _search,
            child: const Text('重试', style: TextStyle(fontSize: 12)),
          ),
        ],
      );
    } else if (_searching) {
      child = Row(
        children: [
          const CupertinoActivityIndicator(radius: 7),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              _lastKeyword.isEmpty ? '正在搜索…' : '正在搜索「$_lastKeyword」…',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 13, color: secondary),
            ),
          ),
        ],
      );
    } else if (_elapsed != null) {
      child = Text(
        '${_results.length} 首 · 用时 ${_elapsed!.toStringAsFixed(1)} 秒'
        '${_serverElapsed == null ? '' : '（服务端 ${_serverElapsed!.toStringAsFixed(1)} 秒）'}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(fontSize: 12, color: secondary),
      );
    } else {
      // 空闲时不留字（提示语在列表区的空状态里），但高度留着，免得一搜就跳
      child = const SizedBox(height: 14);
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 6),
      child: child,
    );
  }

  Widget _body(BuildContext context) {
    if (_results.isEmpty) {
      final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
      final String hint;
      if (_error != null) {
        // 技术性错误在状态行里，这里只说该干什么
        hint = '检查一下网络或服务器地址，然后点上面的「重试」';
      } else if (_searching) {
        hint = '正在搜索…';
      } else if (_lastKeyword.isNotEmpty) {
        // 以前搜完没结果还显示「输入关键词开始搜索」，看着像没搜
        hint = '没找到「$_lastKeyword」相关的歌\n换个说法，或者少打几个字试试';
      } else {
        hint = '输入关键词开始搜索\n停手 0.5 秒自动搜，回车也可以';
      }
      return Center(
        child: Text(
          hint,
          textAlign: TextAlign.center,
          style: TextStyle(fontSize: 14, height: 1.6, color: secondary),
        ),
      );
    }
    return ListView.separated(
      // 换 epoch 就换 key：新一轮搜索从列表顶部开始，不会停在上一轮的滚动位置
      key: ValueKey<int>(_epoch),
      controller: _scroll,
      // 最后多一格：加载中 / 没有更多了
      itemCount: _results.length + 1,
      separatorBuilder: (_, __) => const _Hairline(),
      itemBuilder: (context, index) {
        if (index >= _results.length) return _footer(context);
        final song = _results[index];
        return _SongRow(
          song: song,
          current: widget.player.current?.id == song.id,
          liked: widget.library.liked(song.id),
          onTap: () => widget.player.playQueue(_results, index, widget.api),
          onSinger: song.singers.isEmpty
              ? null
              : () => ArtistPage.open(
                    context,
                    name: song.singers,
                    api: widget.api,
                    player: widget.player,
                    library: widget.library,
                    onUnauthorized: widget.onUnauthorized,
                  ),
          onLike: () => _toggleLike(song),
          onDownload: () => _download(song),
        );
      },
    );
  }

  /// 列表底部：还有就自动加载，没有就说一声。
  Widget _footer(BuildContext context) {
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    if (_loadingMore) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 20),
        child: Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const CupertinoActivityIndicator(radius: 8),
            const SizedBox(width: 10),
            Text('正在加载更多…', style: TextStyle(fontSize: 13, color: secondary)),
          ],
        ),
      );
    }
    if (_hasMore) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Center(
          child: CupertinoButton(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            onPressed: _loadMore,
            child: const Text('加载更多'),
          ),
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 16),
      child: Center(
        child: Text('共 ${_results.length} 首', style: TextStyle(fontSize: 11, color: secondary)),
      ),
    );
  }
}

/// 列表里的一行歌：封面 + 曲名 + 歌手（可点）+ 格式 + 喜欢 / 下载。
///
/// 没用 `CupertinoListTile`：它自带一条底边线（和 `separated` 的分隔线会重），
/// 而且没有「当前播放」这种选中态。
class _SongRow extends StatelessWidget {
  const _SongRow({
    required this.song,
    required this.current,
    required this.liked,
    required this.onTap,
    required this.onSinger,
    required this.onLike,
    required this.onDownload,
  });

  final Song song;
  final bool current;
  final bool liked;
  final VoidCallback onTap;
  final VoidCallback? onSinger;
  final VoidCallback onLike;
  final VoidCallback onDownload;

  @override
  Widget build(BuildContext context) {
    final accent = CupertinoTheme.of(context).primaryColor;
    final label = CupertinoColors.label.resolveFrom(context);
    final secondary = CupertinoColors.secondaryLabel.resolveFrom(context);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: onTap,
      child: Container(
        // 正在播放的那首淡淡铺一层主色（Material 的 ListTile 用 selected 也是这个意思）
        color: current ? accent.withValues(alpha: 0.10) : null,
        padding: const EdgeInsets.fromLTRB(12, 8, 4, 8),
        child: Row(
          children: [
            SongAvatar(song: song, highlight: current),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    song.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(
                      fontSize: 15,
                      fontWeight: current ? FontWeight.w600 : FontWeight.w400,
                      color: label,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Row(
                    children: [
                      // 点歌手名进歌手主页
                      Flexible(
                        child: GestureDetector(
                          onTap: onSinger,
                          child: Text(
                            song.singers,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 12.5, color: accent),
                          ),
                        ),
                      ),
                      if (song.subtitleTail.isNotEmpty)
                        Flexible(
                          child: Text(
                            ' · ${song.subtitleTail}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(fontSize: 12.5, color: secondary),
                          ),
                        ),
                    ],
                  ),
                ],
              ),
            ),
            Text(song.ext.toUpperCase(), style: TextStyle(fontSize: 11, color: secondary)),
            _IconTap(
              icon: liked ? CupertinoIcons.heart_fill : CupertinoIcons.heart,
              color: liked ? CupertinoColors.systemRed.resolveFrom(context) : secondary,
              onTap: onLike,
            ),
            _IconTap(icon: CupertinoIcons.cloud_download, color: secondary, onTap: onDownload),
          ],
        ),
      ),
    );
  }
}

/// 列表行尾部的小图标按钮（自带按下变淡）。
class _IconTap extends StatelessWidget {
  const _IconTap({required this.icon, required this.color, required this.onTap});

  final IconData icon;
  final Color color;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return CupertinoButton(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      pressedOpacity: 0.5,
      onPressed: onTap,
      child: Icon(icon, size: 22, color: color),
    );
  }
}

/// 0.5px 的分隔线（iOS 的 hairline）。
class _Hairline extends StatelessWidget {
  const _Hairline();

  @override
  Widget build(BuildContext context) => Container(
        height: 0.5,
        color: CupertinoColors.separator.resolveFrom(context),
      );
}
