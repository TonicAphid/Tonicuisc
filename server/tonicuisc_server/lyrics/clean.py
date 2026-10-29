"""歌词文本清理（不涉及网络/存储）。

两条规则，**都不删正文**：

1. 开头那行「歌名 - 歌手」不是歌词 → 删掉（只删这一行，署名行永不删）；
2. 开头的「制作名单」块（`词：` / `承制：` / `音乐发行：` / `项目总统筹：` …）
   → **保留文字，但不注音**。

第 2 条以前靠一张角色词表逐行判断，永远列不全：「承制」「音乐发行」「项目总统筹」
「海报设计团队」「策略行销」这些词不在表里，于是整块署名行底下都被注了拼音。
现在改成**按结构判断**，不依赖词表：

* 署名行几乎都长成「标签：内容」，标签很短且不含句子标点；
* 偶尔是「姓名/姓名/姓名」名单行，或「主题歌音乐专辑工作团队」这种小标题；
* 它们**连续出现在最开头**，正文第一句开始就断了；
* 旁证：KRC 给「不唱的行」留的是每字 ~27ms 的占位时间（正文一行 3~9 秒）。

判定只影响「要不要注音」，一个字都不会删。
"""

from __future__ import annotations

import re

from .matcher import normalize
from .models import LyricsLine

#: 头部行最长多少字（再长就不是「歌名 - 歌手」了，别误删正文第一句）
_MAX_HEADER_LEN = 60

#: 署名块最多往下看多少行（真有 60 多行制作名单的，比如《天地龙鳞》）
_MAX_PREFIX_SCAN = 150

#: 多歌手的连接符：``王力宏/谭维维``、``A、B``、``A feat. B`` …
_ARTIST_SPLIT_RE = re.compile(r"[/、,，;；|｜&]|\s(?:feat|ft|with|vs)\.?\s", re.IGNORECASE)

#: 歌手名里的拉丁部分（``王力宏 (Leehom Wang)`` 的括号内容）
_LATIN_RE = re.compile(r"[a-z0-9]+")

#: 「标签：内容」——标签最多 16 字，且不能带句子标点（``他说：我不爱你`` 里的「他说」也算，
#: 代价只是这一行不注音，不删字）
_LABEL_RE = re.compile(r"^\s*[\[\(【]?\s*([^：:]{1,16}?)\s*[\]\)】]?\s*[：:]")

#: 句子标点：标签里出现就说明这是歌词，不是署名
_SENTENCE_PUNCT = re.compile(r"[。，！？；…]")

#: 「张三/李四/王五」这种名单行
_NAME_LIST_RE = re.compile(
    r"^[\u4e00-\u9fffA-Za-z·．.\s]{2,8}(?:[/、][\u4e00-\u9fffA-Za-z·．.\s]{2,16})+$"
)

#: 署名块里的小标题：「主题歌音乐专辑工作团队」
_SECTION_RE = re.compile(r"^[^\s]{2,14}(?:团队|名单|阵容|人员|鸣谢|致谢|工作组|筹备组)$")

#: 整行被括号包住：开头那些「（未经著作人许可，不得翻唱翻录或使用）」的声明、
#: 以及「(Oh yeah)」这种和声标注，都不是要唱的主行
_WRAPPED_RE = re.compile(r"^[（(【\[][^（(【\[]*[）)】\]]$")

#: 占位时间：KRC 给不唱的行按每字 ~27ms 铺时间，正文一行要好几百毫秒一个字
_PLACEHOLDER_MS_PER_CHAR = 60
_PLACEHOLDER_MAX_SPAN = 1500


def artist_keys(artists: str) -> set[str]:
    """把歌手串拆成「能拿去和歌词行比对」的键。

    为什么要拆：``缘分一道桥 … - 王力宏 (Leehom Wang)/谭维维 (Sitar Tan)`` 这一行里，
    ``王力宏`` 和 ``谭维维`` 之间夹着 ``(Leehom Wang)/``，所以拿整串
    ``王力宏谭维维`` 去 ``in`` 是**匹配不到**的——头部因此没被删掉，最后还被注了音。

    每个名字给两个键：整名（含拉丁名）和去掉拉丁名后的中文部分，
    这样 ``王力宏 (Leehom Wang)`` 既能被 ``王力宏leehomwang`` 命中，也能被 ``王力宏`` 命中。
    """
    keys: set[str] = set()
    for part in _ARTIST_SPLIT_RE.split(artists or ""):
        name = normalize(part)
        if not name:
            continue
        keys.add(name)
        core = _LATIN_RE.sub("", name)
        if len(core) >= 2:
            keys.add(core)
    return keys


def is_credit_line(text: str) -> bool:
    """``词：周杰伦`` / ``承制：某某公司`` / ``Lyrics by 方文山`` 这类「标签：内容」行。"""
    text = (text or "").strip()
    if not text:
        return False
    match = _LABEL_RE.match(text)
    return bool(match) and not _SENTENCE_PUNCT.search(match.group(1))


def _has_placeholder_timing(line: LyricsLine) -> bool:
    """KRC 给「不唱的行」铺的是占位时间（每字几十毫秒），真歌词一行 3~9 秒。"""
    text = line.text.strip()
    if len(text) < 2:
        return False
    span = line.end_ms - line.start_ms
    if span <= 0 or span > _PLACEHOLDER_MAX_SPAN:
        return False
    return span / len(text) <= _PLACEHOLDER_MS_PER_CHAR


def looks_like_credit_line(line: LyricsLine) -> bool:
    """单行像不像制作名单 / 声明（不看位置）。"""
    text = line.text.strip()
    if not text:
        return False
    if is_credit_line(text) or _NAME_LIST_RE.match(text) or _SECTION_RE.match(text):
        return True
    if _WRAPPED_RE.match(text):
        return True
    return _has_placeholder_timing(line)


def credit_prefix_length(lines: tuple[LyricsLine, ...], *, max_scan: int = _MAX_PREFIX_SCAN) -> int:
    """开头连续多少行是制作名单。

    只返回「行数」，一个字都不删——调用方拿它决定这些行**不注音**（:func:`fill_pinyin`）。
    名单块一定在最开头、而且连续，正文第一句就断，所以从头走到第一个不像的行即可。
    """
    count = 0
    for index, line in enumerate(lines[:max_scan]):
        if not looks_like_credit_line(line):
            break
        count = index + 1
    return count


def strip_header(lines: tuple[LyricsLine, ...], title: str, artists: str) -> tuple[LyricsLine, ...]:
    """去掉开头那行「歌名 - 歌手」。

    KRC 的第一行经常是 ``[0,7340]晴天 - 周杰伦 (Jay Chou)``，它不是歌词。判定要**同时**
    满足才删：

    * 这一行含歌名，且含**任意一位**歌手（多歌手、带英文名都算，见 :func:`artist_keys`）；
    * 或者前两行正好是「歌名」「歌手」各一行；
    * 署名行一律不碰，遇到就直接收手。

    最多删 2 行，而且只看最前面——正文里出现歌名是常事（副歌反复唱歌名）。
    """
    keep = list(lines)
    want_title = normalize(title)
    keys = artist_keys(artists)
    if not keep or not want_title or not keys:
        return lines

    first = keep[0]
    if is_credit_line(first.text):
        return lines
    text = normalize(first.text)
    if not text or len(text) > _MAX_HEADER_LEN:
        return lines

    hit_title = want_title in text
    hit_artist = any(key in text for key in keys)
    if hit_title and hit_artist:
        return tuple(keep[1:])

    # 「歌名」一行、「歌手」一行的两行式头部
    if text == want_title and len(keep) > 1 and not is_credit_line(keep[1].text):
        second = normalize(keep[1].text)
        if second in keys:
            return tuple(keep[2:])

    return lines
