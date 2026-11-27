#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从仓库现成产物自动组装 fund-data 文档站（mkdocs-material，GitHub Pages 用）。

定位：网站是「给人看」的门面——内容全部复用仓库已审查产物，零手工第三副本：
  index        = README 头部 + 特性一览 + 架构总览 + 快速上手 → 首页（非对称 Hero、
                 请求账本、数字带、特性索引、层级地图；数字全部从 README/源码推导）
  start        = README 快速上手（按场景拆块）+ 平台支持
  catalog      = SKILL 场景路由表 + README 端点能力清单（函数名自动链到 API）+ 申万层补段
  api          = ast 从 fund_data.py 抽全部公开函数（签名 + docstring），按能力清单分层，
                 带可筛选的函数索引；SKILL 场景路由与 examples 场景作为每个函数的旁注
  recipes/faq  = examples.md 按「场景 / FAQ」切两页
  antiban      = README 防封设计（请求预算契约 / 四层防线 / 风控阈值）+ SKILL 失效备源路由
  architecture = README 架构总览（ASCII 图解析成层级地图）+ 数据覆盖（表格改账本式列表）
  pitfalls     = README 坑区速查（P 号上限从 README 动态推导）
  changelog    = README 版本历史（逐版本折叠）
  about        = README 头部 + 平台支持 / 测试与质量 / 生态关系 / 边界 / 仓库结构 / License

防漂移与隐私断言（与 build_skill_md.py 同风格，fail-loud）：
  版本四处一致（fund_data.py __version__ / SKILL front-matter / README 首段 / examples 首行）；
  全部公开函数进 api 页且全部映射到能力清单的分组；每个分组都登记到架构总览的层；
  README / examples / SKILL 的每个 H2 都被消费或显式跳过（新增小节即报错，不静默丢页）；
  页面集合与导航一致；产物（含拷入的站点素材）零本机身份（隐私红线）。
上游 README 修复后自动退位：catalog 的申万补段在 README 自己收编 sw_* 后跳过。
解析不了的展示性内容（架构 ASCII 图）回退为原样代码块并告警，不阻断构建。

用法（只写 --out，绝不触碰 --repo；out 的任何子目录解析后落在仓库内都拒绝写入）：
  python build_docs.py --repo <仓库路径> --out <站点工程目录>
  之后：python -m mkdocs build --strict -f <out>/mkdocs.yml
"""
import argparse
import ast
import getpass
import hashlib
import html
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════

REPO_URL = "https://github.com/hotsauce000/fund-data"        # 仓库已公开的坐标，非本机身份
SITE_URL = "https://hotsauce000.github.io/fund-data/"
ASSETS_SUBDIR = "site_assets"        # 手写的样式/脚本/模板，与生成器同目录
SW_LAYER = "申万行业指数层（V0.8.2）"

HOME = "index"
PAGES = (HOME, "start", "catalog", "api", "recipes", "faq", "antiban",
         "architecture", "pitfalls", "changelog", "about")
NAV = [
    ("开始使用", "start"), ("功能目录", "catalog"), ("API 参考", "api"),
    ("使用菜谱", "recipes"), ("FAQ", "faq"), ("防封设计", "antiban"),
    ("架构与数据覆盖", "architecture"), ("实测坑区", "pitfalls"),
    ("版本历史", "changelog"), ("关于", "about"),
]
TITLES = {
    "start": "开始使用", "catalog": "功能目录", "api": "API 参考",
    "recipes": "使用菜谱", "faq": "FAQ", "antiban": "防封设计",
    "architecture": "架构与数据覆盖", "pitfalls": "实测坑区",
    "changelog": "版本历史", "about": "关于",
}
ASSET_FILES = (          # 站点素材必须齐备（缺文件 = 样式/脚本静默失效）
    "overrides/assets/stylesheets/extra.css",
    "overrides/assets/javascripts/extra.js",
    "overrides/assets/images/favicon.svg",
    "overrides/partials/source.html",
    "overrides/partials/copyright.html",
    "overrides/partials/logo.html",
)
MANIFEST = ".fd-manifest.json"    # 上次生成的文件清单：旧页清理只认它，绝不按通配符删

# README 端点能力清单未收录的函数 → 层（em_market_code 官方口径并入板块行；
# sw_* 是 V0.8.2 整层漏收——上游 README 补 4 行后自动退位）
LAYER_OVERRIDES = {
    "em_market_code": "跨域穿透层",
    "sw_index_list": SW_LAYER,
    "sw_index_valuation": SW_LAYER,
    "sw_index_daily": SW_LAYER,
    "sw_index_components": SW_LAYER,
}

# 能力清单分组（括号前的名字）→ 架构总览 ASCII 图的层号。两节命名不同（清单按能力分、
# 架构图按模块分）且是多对多；新加分组或层而没登记在这里即构建失败，README 两节的漂移在此拦下。
LAYER_TREE = {
    "主键与消歧层": (1,),
    "快照与净值层": (2, 3),
    "持仓层": (2, 3),
    "评测计算层": (3,),
    "跨域穿透层": (4,),
    "宏观利率层": (5,),
    "全市场数据层": (6,),
    "经理画像、专项组装与基准指数层": (7, 8),
    "中证官方全收益指数层": (9,),
    "申万行业指数层": (10,),
    "公募REITs层": (11,),
    "防封基础设施": (1,),
}

SW_CATALOG_PATCH = f"""### {SW_LAYER}

| 端点 | 数据 |
|---|---|
| 官方清单 `sw_index_list(indextype)` | 申万宏源研究官网直连（P34 自组 CA bundle）：一级/二级/风格/金创五类 indextype 白名单；翻页 count 强校验，一级行业实收 ≥31 且含 801010 锚；24h 缓存 |
| 官方估值 `sw_index_valuation(index_code,start,end)` ★ | 行业基金对标核心增量：官方日度 PE/PB/股息率/换手率/流通市值（亿元）；按年切片缓存、当日盘后 `pending_today` 豁免、pe None 亏损期口径容错 |
| 日线全史 `sw_index_daily(index_code,period)` | trend 一发全史（1999-12-30 起，实测 6497 行），day/week/month 键隔离按年切片；close 与 change_pct 勾稽 ±0.5% 警告 |
| 成分股 `sw_index_components(index_code)` | 单发 page_size=10000 拿全（801030 实测 413 只）：权重/since；权重和 ∉[95,105] 警告不拒绝 |

> 本小节由 build_docs.py 补齐：README 端点能力清单漏收 V0.8.2 申万层 4 个函数
> （架构总览层 10 与数据覆盖 #17 有、清单没有）。上游 README 补上后本段自动跳过。
"""

# ══════════════════════════════════════════════════════════════════
# 文本小工具
# ══════════════════════════════════════════════════════════════════

CJK = r"\u3400-\u4dbf\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
_CJK_CHAR = re.compile(rf"[{CJK}]")
_FENCE = re.compile(r"(`{3,}|~{3,})")
_WARN = re.compile("[\u26a0\U0001f534]\ufe0f?")    # ⚠ / 🔴（可带变体选择符）：源文件里都标记「坑 / 硬约束」
ICON_TOKEN = "\x01"               # ⚠ / 🔴 的占位符：落盘前统一换成 ICON
STAR_TOKENS = {"\u2b50": "\x04", "\u2605": "\x05"}    # ⭐ / ★：能力清单的「主入口 / 重点」标记，交给 tagify
TOKENS = (ICON_TOKEN, *STAR_TOKENS.values())
PLACEHOLDER = re.compile(r"\x02\d+\x03")              # Linker.html 的片段占位符（见 CONTROL）
# README 特性一览的装饰性项目符号：纯装饰，删除。其余 emoji 一律报错——含义标记不许被静默吞掉
DECOR_EMOJI = frozenset("\U0001f310\U0001f4e6\U0001f3d7\U0001f9ed\U0001fa7a\U0001f4b0\U0001f50d"
                        "\U0001f507\U0001f4c9\U0001f4c8\U0001f3e6\U0001f3e2\U0001f9fe")
# 线性警示图标（原创几何：描边三角 + 感叹号），全站替代 ⚠️ emoji
ICON = ('<svg class="fd-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
        '<path d="M12 3.5 22 20H2z" /><path d="M12 10v4.6" /><path d="M12 17.2v.3" /></svg>')


def split_heads(md: str, level: int = 2):
    """按行首 `#{level} ` 切分（跳过代码围栏内的行）。返回 (头部, [(标题, 含标题行的整段)])。"""
    marks, fence, offset = [], None, 0
    for line in md.splitlines(keepends=True):
        f = _FENCE.match(line.lstrip())
        if f:
            tok = f.group(1)
            if fence is None:
                fence = tok
            elif tok[0] == fence[0] and len(tok) >= len(fence):
                fence = None
        elif fence is None:
            hm = re.match(rf"#{{{level}}} (.+?)[ \t]*\r?\n?$", line)
            if hm:
                marks.append((offset, hm.group(1).strip()))
        offset += len(line)
    if not marks:
        return md, []
    pairs = []
    for i, (pos, title) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(md)
        pairs.append((title, md[pos:end]))
    return md[: marks[0][0]], pairs


class Sections:
    """H2 小节查取（前缀唯一匹配，fail-loud）。"""

    def __init__(self, md: str):
        self.head, self.pairs = split_heads(md, 2)
        self.map, self.used = {}, set()
        for t, b in self.pairs:
            if t in self.map:
                raise AssertionError(f"H2 标题重复：{t}")
            strip_emoji(t)                          # 标题里不许有 emoji / 星标（含 ⚠），有即报错
            self.map[t] = b

    def __call__(self, prefix: str) -> str:
        hits = [t for t in self.map if t.startswith(prefix)]
        if len(hits) != 1:
            raise AssertionError(f"H2 前缀 {prefix!r} 命中 {len(hits)} 个：{hits}")
        self.used.add(hits[0])
        return self.map[hits[0]]

    def unused(self, skip=()) -> list:
        """没被取用过、也没登记跳过的小节标题（按出现顺序）——新增小节靠它拦下，不静默丢页。"""
        return [t for t, _ in self.pairs
                if t not in self.used and not any(t.startswith(s) for s in skip)]

    def body(self, prefix: str) -> str:
        """小节正文（去掉标题行）。"""
        return self(prefix).split("\n", 1)[1].strip("\n")

    def has(self, prefix: str) -> bool:
        return any(t.startswith(prefix) for t in self.map)


def table_rows(md: str) -> list:
    """Markdown 表格 → [[单元格…]]（跳过代码围栏与分隔行；`\\|` 还原为 `|`）。"""
    rows, fence = [], None
    for line in md.splitlines():
        f = _FENCE.match(line.lstrip())
        if f:
            fence = None if fence else f.group(1)
            continue
        if fence or not line.startswith("|"):
            continue
        cells = [c.strip().replace("\\|", "|")
                 for c in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if cells and all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def _is_emoji(ch: str) -> bool:
    """So 类符号里的 emoji 区段（技术符号、杂项符号、装饰符号、箭头扩展、表情）。
    制表符（U+2500 段）与 U+25A0–25FF 的几何符号（▲ ▶ 等）按纯文本呈现时不算——金融文案里
    它们是内容；带了 emoji 变体选择符的另算（见 _strip_prose）。"""
    cp = ord(ch)
    return (unicodedata.category(ch) == "So"
            and (0x2300 <= cp <= 0x27BF or 0x2B00 <= cp <= 0x2BFF or 0x1F000 <= cp <= 0x1FAFF))


def _segments(line: str) -> list:
    """一行 → [("t", 文本) | ("c", 行内代码)]。行内代码 = 成对的反引号串（含定界符），与 CommonMark 同规则。"""
    out, pos = [], 0
    for m in re.finditer(r"`+", line):
        if m.start() < pos:
            continue
        close = re.search(r"(?<!`)" + "`" * len(m.group(0)) + "(?!`)", line[m.end():])
        if not close:
            break
        end = m.end() + close.end()
        out.append(("t", line[pos:m.start()]))
        out.append(("c", line[m.start():end]))
        pos = end
    out.append(("t", line[pos:]))
    return out


def _prose(seg: str, marks: bool, stars: str) -> str:
    """围栏外、行内代码外的一段文本：含义标记 → 占位符，装饰 emoji 删除，其余 emoji 报错。"""
    seg = _WARN.sub(ICON_TOKEN, seg)
    for star, tok in STAR_TOKENS.items():        # ⭐ / ★：能力清单里是「主入口 / 重点」标记（marks 留占位符）；
        i = seg.find(star)                       # 其余处不许静默丢——确属装饰的由调用方显式传 stars="drop"
        if i >= 0:
            if marks:
                pass
            elif stars != "drop":
                raise AssertionError(f"出现星标 {star}（U+{ord(star):04X}）：能力清单之外不收星标，"
                                     f"请删掉或改用文字——{seg[max(0, i - 24):i + 24]!r}")
            seg = seg.replace(star + "\ufe0f", tok if marks else "").replace(star, tok if marks else "")
    for deco in DECOR_EMOJI:
        seg = seg.replace(deco + "\ufe0f", deco)
    out, i, n = [], 0, len(seg)
    while i < n:
        ch, nxt = seg[i], seg[i + 1:i + 2]
        if nxt == "\ufe0f":
            raise AssertionError(f"出现 emoji 序列 {ch + nxt}（U+{ord(ch):04X} U+FE0F）：{seg[max(0, i - 20):i + 20]!r}")
        if ch in "\ufe0f\u200d":                  # 残留的变体选择符 / 零宽连接符
            i += 1
        elif _is_emoji(ch):
            if ch not in DECOR_EMOJI:
                raise AssertionError(f"出现未登记的 emoji {ch}（U+{ord(ch):04X}）：{seg[max(0, i - 20):i + 20]!r}")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def strip_emoji(text: str, marks: bool = False, stars: str = "raise") -> str:
    """围栏外文本去 emoji。代码围栏与行内代码内逐字保留（那是示例代码，不改写）。
    ⚠ / 🔴 → ICON_TOKEN；⭐ / ★ 仅能力清单（marks=True）留作占位符 → tagify；
    其余小节默认 raise，确属装饰强调的调用方显式传 stars="drop"。"""
    out, fence = [], None
    for line in text.splitlines(keepends=True):
        f = _FENCE.match(line.lstrip())
        if f:
            fence = None if fence else f.group(1)
        if fence:
            out.append(line)
        else:
            out.append("".join(seg if kind == "c" else _prose(seg, marks, stars)
                               for kind, seg in _segments(line)))
    return "".join(out)


def emoji_in(text: str) -> dict:
    """围栏外、行内代码外残留的 emoji → {字符: 次数}（落盘前终检，与 strip_emoji 同一套判定）。"""
    found, fence = {}, None
    for line in text.splitlines():
        f = _FENCE.match(line.lstrip())
        if f:
            fence = None if fence else f.group(1)
        if fence:
            continue
        for kind, seg in _segments(line):
            for ch in seg if kind == "t" else "":
                if _is_emoji(ch) or ch == "\ufe0f":
                    found[ch] = found.get(ch, 0) + 1
    return found


def plain(text: str) -> str:
    """markdown 内联文本 → 纯文本（去 emoji/含义标记占位符、粗体、反引号、链接语法）。"""
    text = strip_emoji(text)
    for tok in TOKENS:
        text = text.replace(tok, "")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return " ".join(re.sub(r"[*`]", "", text).split())


def h(text: str) -> str:
    """纯文本 → 可安全放进 HTML 属性/文本位的转义串。"""
    return html.escape(plain(text), quote=True)


def grab(pattern: str, text: str, what: str) -> str:
    """小节内取值（有分组取第 1 组，否则取整段匹配）；取不到即 fail-loud（防历史措辞被静默误配）。"""
    m = re.search(pattern, text)
    if not m:
        raise AssertionError(f"README 措辞漂移：找不到「{what}」（{pattern}）")
    return m.group(1) if m.re.groups else m.group(0)


def glue(a: str, b: str) -> str:
    """两段文字相接处该不该留空格：任一侧是中日韩字符或全角标点则不留。"""
    if not a or not b:
        return ""
    return "" if (_CJK_CHAR.match(a[-1]) or _CJK_CHAR.match(b[0])) else " "


def raw(block: str) -> str:
    """原始 HTML 块前后留空行（Python-Markdown 要求 HTML 块与正文用空行隔开）。"""
    return "\n\n" + block.strip("\n") + "\n\n"


# ══════════════════════════════════════════════════════════════════
# 解析层：README / examples / SKILL
# ══════════════════════════════════════════════════════════════════


def parse_features(md: str) -> list:
    """特性一览：`- emoji **标题**：说明` → [(标题, 说明)]。有解析不了的条目即 fail-loud。"""
    feats, cur = [], None
    for line in md.splitlines():
        m = re.match(r"^- (\S+) \*\*(.+?)\*\*[：:]\s*(.*)$", line)
        if m:
            cur = [m.group(2).strip(), m.group(3).strip()]
            feats.append(cur)
        elif line.startswith("- "):
            raise AssertionError(f"特性条目解析失败：{line[:40]!r}")
        elif cur and line.strip():                      # 折行续行
            cur[1] += glue(cur[1], line.strip()) + line.strip()
        else:
            cur = None
    if len(feats) < 8:
        raise AssertionError(f"特性条目只有 {len(feats)} 条（预期 ≥8）")
    return [(strip_emoji(t), strip_emoji(d)) for t, d in feats]


def parse_arch(md: str):
    """架构总览里的 ASCII 树 → (前言, [层]) 或 (前言, None)。
    层 = {num, name, note, items: [(入口串, 说明, 是否子项, 是否主入口)]}。
    解析不出 11 层返回 None（调用方回退为原样代码块并告警，不阻断构建）。"""
    m = re.search(r"(?ms)^```\n(.*?)^```", md)
    if not m:
        return md, None
    pre = md[: m.start()]
    layers, cur = [], None
    for raw_line in m.group(1).splitlines():
        line = raw_line.rstrip()
        hm = re.match(r"^层 (\d+) · (.+?)（(.+)）$", line)
        if hm:
            cur = {"num": int(hm.group(1)), "name": hm.group(2).strip(),
                   "note": hm.group(3).strip(), "items": []}
            layers.append(cur)
            continue
        if not line.strip():
            continue
        top = re.match(r"^[├└]── (.+)$", line)
        sub = re.match(r"^[│\s]+└── (.+)$", line)
        if cur is None or not (top or sub):
            return pre, None                      # 出现结构外的行：整图回退原样
        text = (top or sub).group(1).strip()
        sep = re.match(r"^(.+?)\s*(?: → |：)\s*(.+)$", text)
        names, desc = (sep.group(1).strip(), sep.group(2).strip()) if sep else (text, "")
        cur["items"].append((" ".join(names.split()), desc, bool(sub), "⭐" in names))
    return pre, (layers if len(layers) == 11 else None)


def parse_coverage(md: str) -> list:
    """数据覆盖表 → [(编号, 类别, 包含数据, 来源)]。"""
    rows = [r for r in table_rows(md) if len(r) == 4 and r[0].isdigit()]
    want = grab(r"数据覆盖（(\d+) 类）", md, "数据覆盖类数")
    if str(len(rows)) != want:
        raise AssertionError(f"数据覆盖表 {len(rows)} 行，标题写 {want} 类")
    return rows


CATALOG_HEAD = (("端点", "数据"), ("组件", "数据"))     # 能力清单各层表格的表头（措辞漂移即报错，防表头被算成数据）


def data_rows(body: str, layer: str) -> list:
    """能力清单小节的表行（首行必须是表头，随后是分隔行与数据行；列数必须一致）。"""
    raw = table_rows(body)
    if bad := [r for r in raw if len(r) != 2]:
        raise AssertionError(f"端点能力清单「{layer}」有非 2 列的行（多写或漏写竖线）：{bad[:2]}")
    if not raw:
        raise AssertionError(f"端点能力清单「{layer}」下没有表格（H3 后必须紧跟表头与数据行）")
    if (head := tuple(raw[0])) not in CATALOG_HEAD:
        raise AssertionError(f"端点能力清单「{layer}」的表头措辞漂移：{head}")
    return [tuple(r) for r in raw[1:]]


def parse_catalog(md: str):
    """端点能力清单 → (前言, {层名: [(端点单元格, 数据单元格)]}, {层名: 层间备注})。"""
    head, pairs = split_heads(md, 3)
    tables, notes = {}, {}
    for name, body in pairs:
        strip_emoji(name)                           # 层名里不许有 emoji / 星标
        tables[name] = data_rows(body, name)
        if not tables[name]:
            raise AssertionError(f"端点能力清单「{name}」没有任何数据行（H3 下必须紧跟表格）")
        notes[name] = "\n\n".join(
            ln.strip() for ln in body.splitlines()[1:]
            if ln.strip() and not ln.startswith("|"))
    if len(tables) < 8:
        raise AssertionError(f"端点能力清单只解析出 {len(tables)} 个层小节")
    return head, tables, notes


def split_budget(cell: str) -> tuple:
    """预算单元格 → (主值, 备注)：`**常态 2 请求**（E2+限额页），上限 3`
    → ('常态 2 请求', '（E2+限额页），上限 3')。"""
    c = plain(cell)
    m = re.match(r"^(.*?)(（.*）.*)$", c)
    return (m.group(1).strip(), m.group(2).strip()) if m else (c, "")


def parse_changelog(md: str) -> list:
    """版本历史 → [(版本, 日期, 标题, 正文)]。"""
    entries, cur = [], None
    for line in md.splitlines():
        m = re.match(r"^- \*\*(V[\d.]+)(?:（([^）]+)）)?([^*]*)\*\*[：:]\s*(.*)$", line)
        if m:
            cur = [m.group(1), m.group(2) or "", m.group(3).strip(), m.group(4)]
            entries.append(cur)
        elif line.startswith("- "):
            raise AssertionError(f"版本历史条目行解析失败（粗体漏星号或措辞漂移）：{line[:60]!r}")
        elif cur and line.strip():
            cur[3] += glue(cur[3], line.strip()) + line.strip()
        else:
            cur = None
    if len(entries) < 15:
        raise AssertionError(f"版本条目只有 {len(entries)} 条（预期 ≥15）")
    return [tuple(e) for e in entries]


def parse_examples(md: str):
    """examples.md → (文件首行, 环境准备正文, [(编号, 标题, 正文)], FAQ 整段)。"""
    faq = re.search(r"(?m)^#{1,2} FAQ", md)
    if not faq:
        raise AssertionError("examples.md 找不到行首 # FAQ 切分点")
    part1, faq_body = md[: faq.start()], md[faq.start():]
    first_line, pairs = part1.split("\n", 1)[0], split_heads(part1, 2)[1]
    env, scenes = "", []
    for title, body in pairs:
        sm = re.match(r"场景 (\d+) · (.+)$", title)
        if sm:
            scenes.append((int(sm.group(1)), sm.group(2).strip(), body.split("\n", 1)[1].strip("\n")))
        elif title.startswith("环境准备"):
            env = body.split("\n", 1)[1].strip("\n")
        else:
            raise AssertionError(f"examples.md 出现未归类的小节「{title}」——请决定它进哪一页")
    nums = [n for n, _, _ in scenes]
    if nums != list(range(1, len(nums) + 1)):
        raise AssertionError(f"examples.md 场景编号不连续：{nums}")
    return first_line, env, scenes, faq_body


ROUTE_HEAD = ("场景", "调用链", "请求")
FAILOVER_HEAD = ("故障", "行为", "硬边界")


def parse_skill(md: str) -> dict:
    """SKILL.md 的「场景路由」「失效与备源路由」两表 → {"routes": 行, "failover": 行}。"""
    out = {}
    for name, body in split_heads(md, 2)[1]:
        if name.startswith("场景路由"):
            key, want = "routes", ROUTE_HEAD
        elif name.startswith("失效与备源路由"):
            key, want = "failover", FAILOVER_HEAD
        else:
            continue
        rows = table_rows(body)
        if bad := [r for r in rows if len(r) != 3]:
            raise AssertionError(f"SKILL.md「{name}」表有非 3 列的行：{bad[:2]}")
        rows = [tuple(r) for r in rows]
        if not rows or tuple(rows[0]) != want:
            raise AssertionError(f"SKILL.md「{name}」表头措辞漂移：{rows[:1]}")
        out[key] = rows[1:]
    if set(out) != {"routes", "failover"}:
        raise AssertionError(f"SKILL.md 路由表解析失败：{list(out)}")
    return out


# ══════════════════════════════════════════════════════════════════
# 解析层：源码 ast
# ══════════════════════════════════════════════════════════════════


def pub_functions(src: str) -> list:
    """模块级公开函数的 ast 节点（保持源码顺序；含 async def）。"""
    nodes = [n for n in ast.parse(src).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")]
    if len({n.name for n in nodes}) != len(nodes):
        raise AssertionError("公开函数重名")
    return nodes


def sig_parts(node) -> list:
    """签名 → [(名, 类型注解, 默认值)]。posonly / kwonly 当前模块零使用，出现即 fail-loud。"""
    a = node.args
    if a.posonlyargs or a.kwonlyargs:
        raise AssertionError(f"{node.name} 使用了 posonly/kwonly 参数——请扩展 sig_parts")
    pad = len(a.args) - len(a.defaults)
    parts = [(x.arg,
              ast.unparse(x.annotation) if x.annotation else "",
              ast.unparse(a.defaults[i - pad]) if i >= pad else "")
             for i, x in enumerate(a.args)]
    if a.vararg:
        parts.append((f"*{a.vararg.arg}", "", ""))
    if a.kwarg:
        parts.append((f"**{a.kwarg.arg}", "", ""))
    return parts


def sig_html(node) -> str:
    """签名条 HTML：参数按词元排布，过长时在参数之间换行。"""
    out = [f'<div class="fd-sig"><code><span class="fd-sig__head"><span class="fd-sig__kw">'
           f'{"async def" if isinstance(node, ast.AsyncFunctionDef) else "def"}</span> '
           f'<span class="fd-sig__name">{node.name}</span>(</span>']
    parts = sig_parts(node)
    for i, (name, ann, default) in enumerate(parts):
        piece = f'<span class="fd-sig__n">{html.escape(name)}</span>'
        if ann:
            piece += f'<span class="fd-sig__t">: {html.escape(ann)}</span>'
        if default:
            piece += f'<span class="fd-sig__d"> = {html.escape(default)}</span>'
        out.append(f'<span class="fd-sig__arg">{piece}{", " if i < len(parts) - 1 else ""}</span>')
    out.append('<span class="fd-sig__tail">)')
    if node.returns:
        out.append(f'<span class="fd-sig__r"> -&gt; {html.escape(ast.unparse(node.returns))}</span>')
    out.append("</span></code></div>")
    return "".join(out)


_BULLET = re.compile(r"^\s*[-•]\s+")
_KEYLINE = re.compile(r"^(?P<k>[\w\u4e00-\u9fff]{1,10})[：:](?!\S*//)(?=\S)")    # 排除 https:// 这类 URL 的冒号
_BRACE_OK = re.compile(r"^[\w\u4e00-\u9fff{},\[\]: .%/*'\-]+$")
_SENT_END = "。；！？"


def brace_to_code(text: str) -> str:
    """docstring 里的结构字面量 `{code, name}` / `[{date, …}]` → 行内代码（最外层整包）。"""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "{":
            depth, j = 0, i
            while j < n:
                depth += (text[j] == "{") - (text[j] == "}")
                if depth == 0:
                    break
                j += 1
            if depth == 0 and 2 < j - i <= 140 and _BRACE_OK.match(text[i:j + 1]):
                out.append(f"`{text[i:j + 1]}`")
                i = j + 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def render_doc(doc: str) -> list:
    """docstring 正文 → [("p" | "li", 文本)]。合并软换行；
    「键：说明」行、列表项、图标行各自成块。"""
    blocks, para, items = [], [], []

    def flush_para():
        if para:
            for i in range(1, len(para)):
                para[i] = glue(para[i - 1], para[i]) + para[i]
            blocks.append(("p", "".join(para)))
            para.clear()

    def flush_all():
        if items:
            blocks.extend(("li", x) for x in items)
            items.clear()
        flush_para()

    item_open = False
    for raw_line in doc.strip().splitlines():
        s = raw_line.strip()
        if not s:
            flush_all()
            item_open = False
            continue
        if _BULLET.match(raw_line):
            flush_para()
            items.append(s.lstrip("-• ").strip())
            item_open = True
            continue
        if item_open and items and items[-1][-1:] not in _SENT_END:
            items[-1] += glue(items[-1], s) + s          # 列表项的折行续行
            continue
        if _KEYLINE.match(s) or s.startswith(ICON_TOKEN):
            flush_all()                                  # 「键：说明」/ 图标行另起一段
        para.append(s)
    flush_all()
    return blocks


# ══════════════════════════════════════════════════════════════════
# 渲染层：文本变换与小组件
# ══════════════════════════════════════════════════════════════════


class Linker:
    """函数名 → API 锚点。
    代码串形态 `func()` / `func` 一律可链；裸函数名与私有名（_xxx → 代码）仅在 bare=True 时处理。
    已是链接语法 [文字](目标) 的片段整体不动（防嵌套 <a>，也防链接文字被改写）；代码围栏由 block() 跳过。"""

    def __init__(self, pub: set):
        self.pub = pub
        alt = "|".join(re.escape(n) for n in sorted(pub, key=len, reverse=True))
        self._any = re.compile(rf"(?<![\w.`])(_[A-Za-z]\w*|{alt})(?!\w)")
        self._call = re.compile(rf"^({alt})(\([^`\n]*\))?$")
        # 带捕获组：split 之后奇数位是链接本身、原样保留（无捕获组的 split 会把整段链接删掉）
        self._mdlink = re.compile(r"(\[[^\]]*\]\([^)]*\))")
        self._mdtarget = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")

    def _plain(self, seg: str, bare: bool) -> str:
        if not bare:
            return seg

        def sub(m):
            tok = m.group(1)
            return f"`{tok}`" if tok.startswith("_") else f"[`{tok}`](api.md#{tok})"

        return "".join(p if i % 2 else self._any.sub(sub, p)
                       for i, p in enumerate(self._mdlink.split(seg)))

    def _codes(self, seg: str, bare: bool) -> str:
        """链接之外的片段：代码串 `func()` → 链接，代码串之外交给 _plain。"""
        out = []
        for kind, piece in _segments(seg):
            if kind == "t":
                out.append(self._plain(piece, bare))
                continue
            n = len(piece) - len(piece.lstrip("`"))
            call = self._call.match(piece[n:-n])
            out.append(f"[{piece}](api.md#{call.group(1)})" if call else piece)
        return "".join(out)

    def line(self, text: str, bare: bool = False) -> str:
        """一行内联 markdown：先按已有链接切分，只处理链接之外的片段。"""
        return "".join(seg if i % 2 else self._codes(seg, bare)
                       for i, seg in enumerate(self._mdlink.split(text)))

    def block(self, md: str, bare: bool = False) -> str:
        """逐行处理，代码围栏内不动。"""
        out, fence = [], None
        for line in md.splitlines():
            f = _FENCE.match(line.lstrip())
            if f:
                fence = None if fence else f.group(1)
            out.append(line if fence else self.line(line, bare))
        return "\n".join(out)

    def html(self, text: str, url, bare: bool = False) -> str:
        """内联 markdown → HTML。供原始 HTML 结构内部使用：md_in_html 只处理 markdown 文本层，
        不处理原始 HTML 子树（如 <dl> 里的 <dt>）。支持代码串、**粗体**、[文字](页.md#锚点)、站外链接，
        以及 bare=True 时的裸函数名 / 私有名。⚠ 的占位符原样保留，落盘前统一替换。
        代码串与链接先存成片段、正文里只留 \\x02N\\x03 占位符，最后还原——这样粗体可以跨过链接，
        链接文字里的代码串用不可点的形态（防 <a> 套 <a>）。"""
        frags = []                                  # [(可点的 HTML, 供链接文字用的 HTML)]

        def esc(s):
            return html.escape(s, quote=False)       # 文本位：引号不必转义

        def put(linked, plain=None):
            frags.append((linked, linked if plain is None else plain))
            return f"\x02{len(frags) - 1}\x03"

        def code(m):
            content = m.group(1)
            plain = f"<code>{html.escape(content)}</code>"
            call = self._call.match(content)
            return put(f'<a href="{url("api", call.group(1))}">{plain}</a>' if call else plain, plain)

        text = re.sub(r"`([^`\n]+)`", code, text)
        out = []
        for i, piece in enumerate(self._mdlink.split(text)):
            if not i % 2:
                out.append(piece)
                continue
            lm = self._mdtarget.fullmatch(piece)     # 链接：目标决定去哪，解析不了即报错
            inner = lm and re.fullmatch(r"([\w.-]+)\.md(?:#([\w-]+))?", lm.group(2))
            if inner:
                href, rel = html.escape(url(inner.group(1), inner.group(2) or ""), quote=True), ""
            elif lm and re.match(r"(?:https?://|mailto:)", lm.group(2)) and "(" not in lm.group(2):
                href, rel = html.escape(lm.group(2), quote=True), ' target="_blank" rel="noopener"'
            else:
                raise AssertionError(f"链接语法解析失败（既非站内 页.md#锚点，也非站外地址）：{piece!r}")
            label = re.sub(r"\x02(\d+)\x03", lambda m: frags[int(m.group(1))][1], esc(lm.group(1)))
            out.append(put(f'<a href="{href}"{rel}>{label}</a>'))
        stream = "".join(out)
        if bare:
            stream = self._any.sub(
                lambda t: put(f"<code>{html.escape(t.group(1))}</code>" if t.group(1).startswith("_")
                              else f'<a href="{url("api", t.group(1))}"><code>{html.escape(t.group(1))}</code></a>'),
                stream)
        stream = "".join(p if PLACEHOLDER.fullmatch(p) else esc(p)
                         for p in re.split(r"(\x02\d+\x03)", stream))
        stream = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stream)
        return re.sub(r"\x02(\d+)\x03", lambda m: frags[int(m.group(1))][0], stream)

    def first(self, text: str):
        """文本里第一个公开函数名（代码串或裸名），没有返回 None。"""
        bare = self._mdlink.sub("", text).replace("`", "")
        for m in self._any.finditer(bare):
            if not m.group(1).startswith("_"):
                return m.group(1)
        return None


def doc_md(doc: str, linker: Linker) -> str:
    """docstring 正文 → markdown：合并软换行，「键：说明」行的键加粗，结构字面量转代码。"""
    out = []
    for kind, text in render_doc(strip_emoji(doc, stars="drop")):   # docstring 里的 ⭐ 是装饰强调
        text = linker.line(brace_to_code(text.replace("<", "&lt;")), bare=True)
        km = _KEYLINE.match(text)
        if kind == "p" and km:
            text = f"**{km.group('k')}**{text[km.end() - 1:]}"     # 保留冒号
        out.append(f"- {text}" if kind == "li" else text)
    rendered, prev = [], None                   # 相邻列表项连成列表；其余块之间空行分隔
    for (kind, _), p in zip(render_doc(strip_emoji(doc, stars="drop")), out):
        if rendered and not (kind == "li" and prev == "li"):
            rendered.append("")
        rendered.append(p)
        prev = kind
    return "\n".join(rendered).strip()


def summary_md(doc: str, linker: Linker) -> str:
    """docstring 首行（摘要）。"""
    return linker.line(brace_to_code(doc.strip().split("\n", 1)[0].replace("<", "&lt;")),
                       bare=True).strip()


def tagify(text: str) -> str:
    """⭐ / ★（原字符，或 strip_emoji(marks=True) 留下的占位符）→ 文字标签（主入口 / 重点）。"""
    main, key = STAR_TOKENS["\u2b50"], STAR_TOKENS["\u2605"]
    text = text.replace("\u2b50", main).replace("\u2605", key)
    return (text.replace(main, '<span class="fd-tag">主入口</span>')
                .replace(key, '<span class="fd-tag fd-tag--soft">重点</span>'))


def esc_pipe(cell: str) -> str:
    """单元格里的竖线转义（表格行重新拼装用）。"""
    return re.sub(r"(?<!\\)\|", r"\\|", cell)


def ledger_html(rows: list) -> str:
    """请求账本：[(场景, 主值, 备注)] → dl。"""
    out = ["<dl>"]
    for label, main, note in rows:
        out.append(f'<div class="fd-ledger__row"><dt>{h(label)}</dt><dd>{h(main)}</dd>')
        if note:
            out.append(f'<dd class="fd-ledger__note">{h(note)}</dd>')
        out.append("</div>")
    out.append("</dl>")
    return "\n".join(out)


def chips_html(names: list, url) -> str:
    items = "".join(f'<li><a class="fd-chip" href="{url("api", n)}"><code>{n}</code></a></li>'
                    for n in names)
    return f'<ul class="fd-chips">{items}</ul>'


def layer_map_html(layers: list, linker: Linker, url, pub_order: list, full: bool) -> tuple:
    """层级地图。full=False（首页）：编号 | 名称 + 备注 | 函数 chip；
    full=True（架构页）：逐条「入口 | 说明」。返回 (HTML 块, 图中出现的公开函数)。"""
    out, covered = [], []
    for layer in layers:
        no, name, note = layer["num"], layer["name"], layer["note"]
        funcs, rows = [], []
        for names, desc, is_sub, main in layer["items"]:
            names = names.replace("\u2b50", "").strip()          # 星标改用文字标签，不留在名称里
            label = linker.html(names, url, bare=True) + (tagify("\u2b50") if main else "")
            cls = "fd-maprow fd-maprow--sub" if is_sub else "fd-maprow"
            dt = "<dt></dt>" if is_sub else f"<dt>{label}</dt>"
            text = f"{names} → {desc}" if (is_sub and desc) else desc
            rows.append(f'<div class="{cls}">{dt}<dd>{linker.html(text, url, bare=True)}</dd></div>')
            for n in pub_order:
                if re.search(rf"(?<![\w.]){re.escape(n)}(?!\w)", names) and n not in funcs:
                    funcs.append(n)
        covered.extend(funcs)
        if full:
            out.append(f'<div class="fd-maplayer" id="layer-{no:02d}" markdown>\n\n'
                       f'<p class="fd-eyebrow">LAYER {no:02d}</p>\n\n## {name}\n\n</div>\n\n'
                       "<dl>\n" + "\n".join(rows) + "\n</dl>")
        else:
            meta = f'<span class="fd-layer__meta">{h(note)}</span>' if note else ""
            body = chips_html(funcs, url) if funcs else ""
            out.append(f'<div class="fd-layer"><span class="fd-layer__no">{no:02d}</span>'
                       f'<span class="fd-layer__head"><a class="fd-layer__name" '
                       f'href="{url("architecture", f"layer-{no:02d}")}">{h(name)}</a>{meta}</span>'
                       f'<div class="fd-layer__body">{body}</div></div>')
    return "\n".join(out), covered


def front(page: str) -> str:
    """页面 front matter。首页不带 title（避免 <title> 里站名重复），并隐藏两侧导航与页内目录。"""
    title = f"title: {TITLES[page]}\n" if page in TITLES else ""
    hide = "hide:\n  - navigation\n  - toc\n" if page == HOME else ""
    return f"---\n{title}{hide}---\n\n"


def lead(text: str) -> str:
    """页面导语。text 可含行内 markdown 与 HTML。"""
    return f'<p class="fd-lead" markdown="span">{text}</p>'


def meta_bar(*items: str) -> str:
    return '<div class="fd-meta">' + "".join(f"<span>{h(x)}</span>" for x in items) + "</div>"


def sechead(eyebrow: str, title: str, aside: str) -> str:
    """首页区块标题行：眉题 + 标题，右侧一行说明。"""
    extra = f'\n\n<p class="fd-sechead__aside" markdown="span">{aside}</p>' if aside else ""
    return (f'<div class="fd-sechead" markdown>\n\n<p class="fd-eyebrow">{eyebrow}</p>\n\n'
            f'## {title}{extra}\n\n</div>\n\n')


# ══════════════════════════════════════════════════════════════════
# 页面：首页 / 开始使用 / 功能目录 / API 参考
# ══════════════════════════════════════════════════════════════════


def render_home(D: dict) -> str:
    linker, head, feats = D["linker"], D["rd"].head, D["features"]
    ver, py, quick = D["ver"], D["py"], D["quick"]
    desc = strip_emoji(grab(r"零 key、零登录、零第三方封装，为.+?提供底层数据原语。", head, "首页导语"))
    st = re.search(r"核心立场：(.+?)\*\*——(.+?)；(所有上游坑.+?)；(宁可显式报错，绝不静默空。)", head)
    if not st:
        raise AssertionError("README 措辞漂移：核心立场句")
    principles = [(st.group(1), st.group(2)), ("实测坑，内嵌防线", st.group(3)),
                  (st.group(4).rstrip("。"), st.group(4))]
    first_scene = quick["blocks"][0][2] if quick["blocks"] else ""

    # 特性索引：一行一项；标题链到说明里出现的第一个函数
    rows = []
    for i, (title, text) in enumerate(feats):
        target = linker.first(text)
        name = f"[{title}](api.md#{target})" if target else title
        rows.append(f"| {i + 1:02d} | {name} | {linker.line(text, bare=True)} |\n")
    stats = "".join(f'<div><dt>{h(k)}</dt><dd>{v}</dd></div>' for k, v in D["stats"])
    stance = "".join(f'<li><h3>{h(t)}</h3><p class="fd-principles__text">{h(b)}</p></li>'
                     for t, b in principles)

    out = [front(HOME), '<div class="fd-home" markdown>\n']
    # ── Hero 三栏：简介 + 动作 | 请求账本 | 规模 + 立场 ──
    out.append(
        '<div class="fd-hero" markdown>\n\n'
        '<div class="fd-hero__lead" markdown>\n\n'
        f'<p class="fd-eyebrow">v{ver} · MIT · Python {py}+ · Linux / macOS / Windows</p>\n\n'
        '<h1 class="fd-wordmark">fund-data</h1>\n\n'
        '<p class="fd-hero__tagline">基金数据底座 FundBase</p>\n\n'
        f'<p class="fd-hero__desc" markdown="span">{desc}</p>\n\n'
        '<div class="fd-actions" markdown>\n\n'
        "[开始使用](start.md){ .fd-btn .fd-btn--primary }\n\n"
        f"[GitHub]({REPO_URL}){{ .fd-btn .fd-btn--ghost target=_blank }}\n\n"
        "</div>\n\n"
        f'<div class="fd-hero__code" markdown>\n\n```bash\n{D["install"].split("#", 1)[0].strip()}\n```\n\n'
        f"```python\n{first_scene}\n```\n\n</div>\n\n"
        "</div>\n\n"
        '<aside class="fd-ledger" markdown>\n\n<p class="fd-label">请求账本 · 每次调用花多少请求</p>\n\n'
        + ledger_html(D["ledger"]) + "\n\n"
        '<p class="fd-ledger__more" markdown="span">完整预算表与四层防线 → [防封设计](antiban.md)</p>\n\n'
        "</aside>\n\n"
        '<div class="fd-hero__side" markdown>\n\n<p class="fd-label">规模</p>\n\n'
        f'<dl class="fd-stats">{stats}</dl>\n\n<p class="fd-label">立场</p>\n\n'
        f'<ul class="fd-principles">{stance}</ul>\n\n'
        "</div>\n\n</div>\n\n")
    # ── 特性索引 ──
    out.append(sechead("FEATURES", "特性一览", f"共 {len(feats)} 项"))
    out.append('<div class="fd-features" markdown>\n\n'
               "| # | 特性 | 说明 |\n|---|---|---|\n" + "".join(rows) + "\n</div>\n\n")
    # ── 层级地图 ──
    out.append(sechead("ARCHITECTURE", "十一层，编号即数据流顺序",
                       "逐函数说明 → [架构与数据覆盖](architecture.md)"))
    if D["arch"]:
        block, _ = layer_map_html(D["arch"], linker, D["url"], D["pub_order"], full=False)
        out.append(raw(f'<div class="fd-layers">{block}</div>'))
    else:
        out.append("层级地图见 [架构与数据覆盖](architecture.md)。\n\n")
    # ── 快速开始 ──
    out.append(sechead("QUICK START", "三步跑起来", "[完整上手](start.md)"))
    steps = [("1 · 安装依赖", D["deps_note"]), ("2 · 诊断一只基金", D["budget_line"]),
             ("3 · 先读铁律", D["rules_line"] + " 详见 [防封设计](antiban.md)。")]
    out.append('<div class="fd-start" markdown>\n\n' + "".join(
        '<div class="fd-step-item" markdown>\n\n### ' + t + '\n\n<p class="fd-step" markdown="span">' + b + "</p>\n\n</div>\n\n"
        for t, b in steps) + "</div>\n\n")
    return "".join(out)


def render_start(D: dict) -> str:
    linker, quick = D["linker"], D["quick"]
    out = [front("start"), "# 开始使用\n",
           raw(lead("`fund_data.py` 没有命令行入口，用法是 import 模块。")), "## 两种用法\n"]
    out.append(raw(
        '<div class="fd-ways" markdown>\n\n<div markdown>\n\n### 作为 Python 模块\n\n'
        "克隆仓库，在仓库目录内运行（或把仓库路径加入 `sys.path`）：\n\n"
        f'```bash\n{D["install"]}\n```\n\n'
        '```python\nfrom fund_data import fund_snapshot, fund_metrics\n\n'
        'snap = fund_snapshot("161725")\n```\n\n</div>\n\n'
        '<div markdown>\n\n### 作为 Agent skill\n\n'
        + linker.line(D["skill_install"], bare=True) + "\n\n</div>\n\n</div>"))
    out.append("## 快速上手\n")
    blocks = [f"### 场景 {n}：{t}\n\n```python\n{code.strip()}\n```" for n, t, code in quick["blocks"]]
    if quick["prelude"].strip():
        blocks.insert(0, "### 引入\n\n```python\n" + quick["prelude"].strip() + "\n```")
    out.append("\n\n".join(blocks) + "\n")
    out.append("\n## 平台支持\n" + linker.block(strip_emoji(D["rd"].body("平台支持")), bare=True) + "\n")
    return "".join(out)


def render_catalog(D: dict) -> str:
    linker = D["linker"]
    tables, notes, layers = D["catalog"], D["catalog_notes"], D["layers"]
    n_rows = sum(len(tables[x]) for x in layers)
    out = [front("catalog"), "# 功能目录\n",
            raw(lead('全部公开函数按能力层分组，表内函数名可直接点进 <a href="api/">API 参考</a>；'
                     "「我想做 X 该调什么」先看场景路由表。")),
            raw(meta_bar(f"{len(D['pub_order'])} 个公开函数", f"{n_rows} 条能力", f"{len(layers)} 个分组")),
            "## 场景路由：我想做 X，调什么\n",
            "| " + " | ".join(ROUTE_HEAD) + " |\n|---|---|---|\n"]
    for scenario, chain, cost in D["skill"]["routes"]:
        out.append(f"| {plain(scenario)} | {linker.line(strip_emoji(chain), bare=True)} | {plain(cost)} |\n")
    out.append("\n## 端点能力清单\n")
    for layer in layers:
        out.append(f"\n### {layer}\n\n| 端点 | 数据 |\n|---|---|\n")
        for title_cell, data_cell in tables[layer]:
            a = esc_pipe(tagify(linker.line(strip_emoji(title_cell, marks=True))))    # 星标 → 文字标签
            b = esc_pipe(linker.line(strip_emoji(data_cell)))
            out.append(f"| {a} | {b} |\n")
        if notes.get(layer):
            out.append("\n" + linker.block(strip_emoji(notes[layer])) + "\n")
    return "".join(out)


def render_api(D: dict) -> str:
    linker, url, nodes = D["linker"], D["url"], D["nodes"]
    by_layer = {}
    for n in nodes:
        by_layer.setdefault(D["layer_of"][n.name], []).append(n)
    groups = [(x, by_layer[x]) for x in D["layers"] if x in by_layer]

    idx = ["<div data-fd-filter>", '<div class="fd-filter">',
           f'<input type="search" placeholder="筛选 {len(nodes)} 个函数：输入函数名、用途或分组名" '
           'aria-label="筛选函数" autocomplete="off" spellcheck="false">',
           '<span class="fd-filter__count" data-fd-count aria-live="polite"></span>', "</div>",
           '<ul class="fd-fnindex">']
    for gi, (layer, group) in enumerate(groups):
        idx.append(f'<li class="fd-fnindex__group" data-fd-group="g{gi}">{h(layer)}</li>')
        for node in group:
            s = D["summary"][node.name]
            idx.append(f'<li class="fd-fnindex__item" data-fd-in="g{gi}" '
                       f'data-fd-item="{h(f"{node.name} {s} {layer}".lower())}">'
                       f'<a href="#{node.name}"><code>{node.name}</code></a>'
                       f'<span class="fd-fnindex__sum">{h(s)}</span></li>')
    idx.append('<li class="fd-fnindex__empty" data-fd-empty hidden>'
               '没有匹配「<b data-fd-empty-query></b>」的函数。换个关键词试试，或在下方按分组浏览。</li></ul></div>')

    body = []
    for layer, group in groups:
        body.append(f'\n## {layer}\n\n<p class="fd-aside">{len(group)} 个函数</p>\n')
        for node in group:
            name = node.name
            body.append(f"\n### {name} {{ #{name} .fd-fn }}\n")
            body.append(raw(sig_html(node)))
            routes = D["routes_of"].get(name)
            if routes:
                lis = "".join(f"<li><b>{h(s)}</b><code>{h(c)}</code></li>" for s, c in routes)
                body.append(raw(f'<ul class="fd-routes">{lis}</ul>'))
            body.append(doc_md(D["doc"][name], linker) + "\n")
            row = D["row_of"].get(name)
            if row:
                body.append(raw(f'<details class="fd-row"><summary>能力清单 · {h(row[0])}</summary>\n\n'
                                f'<div>{linker.html(strip_emoji(row[1]), url)}</div>\n\n</details>'))
            scenes = D["scenes_of"].get(name)
            if scenes:
                links = "、".join(f'<a href="{url("recipes", f"scene-{n}")}">场景 {n} · {h(t)}</a>'
                                 for n, t in scenes)
                body.append(raw(f'<p class="fd-scenes">菜谱：{links}</p>'))
    out = [front("api"), "# API 参考\n",
           raw(lead("由 build_docs.py 从 `fund_data.py` 源码 ast 生成，签名与说明和代码同源。"
                    '场景选型看 <a href="catalog/">功能目录</a>，复制即用看 <a href="recipes/">使用菜谱</a>。')),
           raw(meta_bar(f"{len(nodes)} 个公开函数", f"{len(groups)} 个分组", "源码 ast 同源")),
           raw("".join(idx))]
    return "".join(out) + "".join(body)


# ══════════════════════════════════════════════════════════════════
# 页面：菜谱 / FAQ / 防封 / 架构 / 坑区 / 版本 / 关于
# ══════════════════════════════════════════════════════════════════


def render_recipes(D: dict) -> str:
    linker = D["linker"]
    out = [front("recipes"), "# 使用菜谱\n",
           raw(lead(f"复制即跑的场景菜谱，共 {len(D['scenes'])} 个。示例按 Agent skill 目录写路径，"
                    "作为 Python 模块使用时把环境准备里的 `sys.path` 一行换成仓库路径即可。"
                    "函数细节见 [API 参考](api.md)，请求预算见 [防封设计](antiban.md)。")),
           "## 环境准备\n" + linker.block(strip_emoji(D["env"])) + "\n"]
    for n, title, text in D["scenes"]:
        out.append(f"\n## 场景 {n} · {title} {{ #scene-{n} }}\n" + linker.block(strip_emoji(text)) + "\n")
    return "".join(out)


def render_faq(D: dict) -> str:
    rest = D["faq"].split("\n", 1)[1]
    return "".join([
        front("faq"), "# FAQ\n",
        raw(lead("按现象找解法，每条不超过 5 行。文中「→ P数字」指向的完整档案见 [实测坑区](pitfalls.md)。")),
        D["linker"].block(strip_emoji(rest)), "\n"])


def render_antiban(D: dict) -> str:
    linker = D["linker"]
    body = re.sub(r"(?m)^### ", "## ", "\n".join(D["rd"]("防封设计").split("\n")[1:]))
    out = [front("antiban"), "# 防封设计\n",
           raw(lead("请求预算是稀缺资源——每个调用花多少请求、失败时怎么降级，都在这一页。")),
           linker.block(strip_emoji(body)), "\n",
           "## 失效与备源路由\n\n| " + " | ".join(FAILOVER_HEAD) + " |\n|---|---|---|\n"]
    for fault, action, bound in D["skill"]["failover"]:
        out.append(f"| {plain(fault)} | {linker.line(strip_emoji(action), bare=True)} | {plain(bound)} |\n")
    return "".join(out)


def render_architecture(D: dict) -> str:
    linker = D["linker"]
    out = [front("architecture"), "# 架构与数据覆盖\n", "## 架构总览\n"]
    if D["arch"]:
        pre = D["arch_pre"].split("\n", 1)[1].strip()          # 去掉 README 的 H2 行
        if pre:
            out.append(strip_emoji(pre) + "\n\n")
        block, _ = layer_map_html(D["arch"], linker, D["url"], D["pub_order"], full=True)
        out.append(raw(block))
    else:
        out.append(linker.block(strip_emoji(D["rd"].body("架构总览"))) + "\n")
    rows = []
    for no, cat, data, src in D["coverage"]:
        rows.append(f'<div class="fd-cov"><span class="fd-cov__no">{h(no)}</span>'
                    f'<dt>{h(cat)}<span class="fd-cov__src">{h(src)}</span></dt>'
                    f'<dd>{linker.html(strip_emoji(data), D["url"])}</dd></div>')
    out.append("\n## 数据覆盖\n" + raw('<dl class="fd-coverage">' + "\n".join(rows) + "</dl>"))
    if D["coverage_post"]:
        out.append(linker.block(strip_emoji(D["coverage_post"])) + "\n")
    return "".join(out)


def render_pitfalls(D: dict) -> str:
    ref = f'<a href="{REPO_URL}/blob/main/reference.md" target="_blank" rel="noopener">reference.md</a>'
    return "".join([
        front("pitfalls"), f"# 实测坑区（P1~P{D['pit_max']}）\n",
        raw(lead("每一条都来自真实踩坑，防线已内嵌在库里。本页是速查表；"
                 f"完整档案与修复证据见仓库 {ref}。")),
        '<div class="fd-pits" markdown>\n\n',
        D["linker"].block(strip_emoji(D["rd"].body("坑区速查"))), "\n\n</div>\n"])


def render_changelog(D: dict) -> str:
    linker = D["linker"]
    note = grab(r"(?m)^> (.+)$", D["rd"].body("版本历史"), "版本历史注释")
    out = [front("changelog"), "# 版本历史\n", raw(lead(strip_emoji(note)))]
    for i, (v, date, title, text) in enumerate(D["changelog"]):
        d = f'<span class="fd-log__d">{h(date)}</span>' if date else ""
        out.append(f'<details class="fd-log"{" open" if i == 0 else ""}>\n'
                   f'<summary><span class="fd-log__v">{h(v)}</span>{d}'
                   f'<span class="fd-log__t">{h(title)}</span></summary>\n\n'
                   f'<div class="fd-log__body">{linker.html(strip_emoji(text, stars="drop"), D["url"])}</div>\n\n'
                   "</details>\n\n")
    return "".join(out)


def render_about(D: dict) -> str:
    linker, rd = D["linker"], D["rd"]
    intro = "\n".join(rd.head.splitlines()[1:]).strip()      # 去掉 README 的 H1
    out = [front("about"), "# 关于 fund-data\n", linker.block(strip_emoji(intro)) + "\n"]
    for prefix in ("平台支持", "测试与质量", "与 a-stock-data", "边界", "仓库结构", "License"):
        title = strip_emoji(rd(prefix).split("\n", 1)[0][3:].strip())   # H2 原文进页面，emoji 在这里就报错
        out.append(f"\n## {title}\n" + linker.block(strip_emoji(rd.body(prefix))) + "\n")
    return "".join(out)


# ══════════════════════════════════════════════════════════════════
# 站点配置
# ══════════════════════════════════════════════════════════════════


def render_mkdocs(D: dict) -> str:
    nav = "\n".join(f"  - {title}: {page}.md" for title, page in NAV)
    return f"""# 由 build_docs.py 生成——手改会被下次构建覆盖；站点调整请改生成器与 {ASSETS_SUBDIR}/。
site_name: fund-data
site_url: {SITE_URL}
repo_url: {REPO_URL}
site_description: >
  基金领域底层数据工具包：零 key 零登录，{D['funds_wan']} 万只基金清单消歧、单基金一站式快照、
  本地评测计算、持仓穿透白话地图、宏观利率、全市场排行、经理画像、中证全收益指数、
  申万行业指数、公募 REITs。请求预算制防封，绝不静默降级。
copyright: hotsauce000 · MIT
strict: true

theme:
  name: material
  language: zh
  custom_dir: overrides
  font: false          # 不引 Google Fonts：大陆访问慢/被墙，用系统字体栈
  favicon: assets/images/favicon.svg
  icon:
    repo: fontawesome/brands/github
  palette:
    - media: "(prefers-color-scheme: light)"
      scheme: default
      primary: custom
      accent: custom
      toggle:
        icon: material/weather-night
        name: 切换到深色
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      primary: custom
      accent: custom
      toggle:
        icon: material/weather-sunny
        name: 切换到浅色
  features:
    - navigation.instant
    - navigation.tracking
    - navigation.top
    - navigation.footer
    - toc.follow
    - search.suggest
    - search.highlight
    - content.code.copy

extra:
  generator: false
  version: {D['ver']}

extra_css:
  - assets/stylesheets/extra.css?v={D['asset_v']['css']}
extra_javascript:
  - assets/javascripts/extra.js?v={D['asset_v']['js']}

plugins:
  - search   # 中文分词：构建环境装有 jieba 时自动启用

markdown_extensions:
  - abbr
  - attr_list
  - def_list
  - footnotes
  - md_in_html
  - nl2br            # 中文文案手工换行按硬断行渲染
  - tables
  - admonition
  - toc:
      permalink: true
      # 中文标题的锚点保留 Unicode（默认 slugify 会把中文剥成 x、_2 这类无意义 id）
      slugify: !!python/object/apply:pymdownx.slugs.slugify {{kwds: {{case: lower, unicode: true}}}}
  - pymdownx.highlight
  - pymdownx.inlinehilite
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.details

nav:
  - 首页: index.md
{nav}
"""


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

# 隐私红线：本机身份不得进站点。用户名不写字面量（这份文件本身要进公开仓），
# 运行时取当前用户名做检查；CI 的通用账号（runner/root）跳过。
_PRIV_USER = getpass.getuser()
PRIVACY = (r"/Users/", r"/home/", r"@[\w.-]+\.local\b",
           *(() if _PRIV_USER in ("root", "runner") else (re.escape(_PRIV_USER),)))
ASSET_TEXT = (".css", ".js", ".html", ".svg")
README_H2_SKIP = ()                # README 的每个 H2 都必须进站点；确有不要的，在这里登记并写明原因
SKILL_H2_SKIP = {                  # SKILL.md 不进站点的小节 → 原因
    "函数速查": "与 API 页重复（api 页由源码 ast 现算）",
    "When to Activate": "写给 Agent 的加载说明，站点读者用不上",
    "深读与边界": "指向仓库内其它文件的深读入口",
}
CONTROL = ("\x01", "\x02", "\x03", "\x04", "\x05")     # 生成器占位符用的控制符；源文件里不得出现


def privacy_check(name: str, text: str) -> None:
    for pat in PRIVACY:
        if re.search(pat, text):
            raise AssertionError(f"隐私红线：{name} 命中 {pat!r}")


def asset_versions(assets: Path) -> dict:
    """extra.css / extra.js 的内容指纹，拼进资源 URL：站点更新后浏览器立刻取新文件（否则会被缓存）。"""
    return {kind: hashlib.sha256((assets / "overrides" / "assets" / sub / f"extra.{kind}").read_bytes()).hexdigest()[:10]
            for kind, sub in (("css", "stylesheets"), ("js", "javascripts"))}


def guard_out(path: Path, repo: Path) -> Path:
    """输出路径解析后必须落在仓库之外（防 out 或其子目录是指向仓库的 symlink）。返回解析后的路径。"""
    resolved = path.resolve()
    if resolved == repo or repo in resolved.parents:
        raise SystemExit(f"拒绝写入：{path} 解析后落在仓库内（symlink 穿透？）")
    return resolved


def icon_in_code(text: str) -> int:
    """代码围栏与行内代码里出现的 SVG 个数——应为 0（示例代码逐字保留，不往里塞图标）。"""
    n, fence = 0, None
    for line in text.splitlines():
        f = _FENCE.match(line.lstrip())
        if f:
            fence = None if fence else f.group(1)
        elif fence:
            n += line.count("<svg")
        else:
            n += sum(seg.count("<svg") for kind, seg in _segments(line) if kind == "c")
    return n


def write(path: Path, text: str, repo: Path) -> int:
    """落盘（统一 LF，返回落盘字节数）。⚠ 占位符只在这里换成 SVG；
    占位符残留、emoji 残留、图标混进代码，都是上游 bug，在这里报错。"""
    guard_out(path, repo)
    final = text.replace(ICON_TOKEN, ICON).rstrip() + "\n"
    if left := sorted(ord(ch) for ch in set(final) if ch in CONTROL):
        raise AssertionError(f"{path.name} 残留占位符/控制符 {left}")
    if bad := emoji_in(final):
        raise AssertionError(f"{path.name} 残留 emoji：{bad}")
    if n := icon_in_code(final):
        raise AssertionError(f"{path.name} 有 {n} 个图标混进了代码")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(final)
    return len(final.encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description="从仓库产物生成 fund-data 文档站工程")
    ap.add_argument("--repo", required=True, help="fund-data 仓库根目录（只读）")
    ap.add_argument("--out", required=True, help="mkdocs 工程输出目录（只写这里）")
    ap.add_argument("--assets", default=str(Path(__file__).parent / ASSETS_SUBDIR),
                    help="站点素材目录（样式/脚本/模板）")
    args = ap.parse_args()
    repo, out, assets = Path(args.repo).resolve(), Path(args.out).resolve(), Path(args.assets).resolve()
    if out == repo or repo in out.parents or out in repo.parents:
        raise SystemExit("--out 必须在仓库之外")
    if assets == out or assets in out.parents or out in assets.parents:
        raise SystemExit("--assets 与 --out 不得相同或互相包含")
    for rel in ASSET_FILES:
        if not (assets / rel).is_file():
            raise SystemExit(f"站点素材缺失：{assets / rel}")
    docs, ov = guard_out(out / "docs", repo), guard_out(out / "overrides", repo)   # 动任何文件之前先验输出目录
    if (out / "docs").is_symlink() or (out / "overrides").is_symlink():            # 用未解析路径查：guard_out 已 resolve
        raise SystemExit(f"拒绝写入：docs / overrides 是 symlink（{out / 'docs'} | {out / 'overrides'}）")

    src = (repo / "fund_data.py").read_text(encoding="utf-8")
    readme = (repo / "README.md").read_text(encoding="utf-8")
    examples = (repo / "examples.md").read_text(encoding="utf-8")
    skill = (repo / "SKILL.md").read_text(encoding="utf-8")
    for name, text in (("fund_data.py", src), ("README.md", readme),
                       ("examples.md", examples), ("SKILL.md", skill)):
        if bad := sorted(ord(ch) for ch in set(text) if ch in CONTROL):
            raise AssertionError(f"{name} 含生成器占位符用的控制符 {bad}")

    # ── 版本四处一致 + README 首行 ──
    ver = grab(r'(?m)^__version__ = "([^"]+)"', src, "模块版本")
    ver_skill = grab(r"(?m)^version: (\S+)", skill, "SKILL 版本")
    rd = Sections(readme)
    ver_readme = grab(r"基金领域底层数据工具包 V(\d+\.\d+\.\d+)（", rd.head, "README 首段版本")
    if not ver == ver_skill == ver_readme:
        raise AssertionError(f"版本漂移：module={ver} skill={ver_skill} README={ver_readme}")
    if not rd.head.startswith("# fund-data\n"):
        raise AssertionError(f"README 首行措辞漂移：{rd.head.splitlines()[0]!r}")

    # ── ast：公开函数、docstring ──
    nodes = pub_functions(src)
    pub_order = [n.name for n in nodes]
    pub = set(pub_order)
    if grab(r"函数速查（(\d+) 个", skill, "SKILL 函数速查条数") != str(len(pub_order)):
        raise AssertionError(f"SKILL.md 函数速查的条数与源码不符（源码 {len(pub_order)} 个公开函数）")
    doc = {n.name: (ast.get_docstring(n) or "").strip() for n in nodes}
    if missing_doc := [k for k, v in doc.items() if not v]:
        raise AssertionError(f"公开函数缺 docstring：{missing_doc}")
    summary = {k: v.split("\n", 1)[0].strip() for k, v in doc.items()}
    linker = Linker(pub)

    # ── 能力清单 → 层映射；README 没收编申万层时补段（上游补齐后自动退位）──
    catalog_md = rd("端点能力清单")
    _, tables, notes = parse_catalog(catalog_md)
    layers = list(tables)
    sw_covered = False

    def map_rows(rows, layer):
        nonlocal sw_covered
        for title_cell, data_cell in rows:
            for name in re.findall(r"`([a-z_][a-z0-9_]*)\(", title_cell):
                layer_of.setdefault(name, layer)
                row_of.setdefault(name, (plain(title_cell.split("`")[0]), data_cell))
                sw_covered = sw_covered or name.startswith("sw_")

    layer_of, row_of = {}, {}
    for layer in layers:
        map_rows(tables[layer], layer)
    if not (rd.has("申万行业指数层") or f"### {SW_LAYER}" in catalog_md or sw_covered):
        _, patch = split_heads(SW_CATALOG_PATCH, 3)
        pname, pbody = patch[0]
        tables[pname] = data_rows(pbody, pname)
        notes[pname] = "\n".join(ln for ln in pbody.splitlines() if ln.startswith(">"))
        map_rows(tables[pname], pname)
        reit = next(i for i, x in enumerate(layers) if x.startswith("公募REITs层"))
        layers.insert(reit, pname)
    for name, layer in LAYER_OVERRIDES.items():        # README 已收编的以 README 为准
        layer_of.setdefault(name, layer)
    if unknown := set(layer_of) - pub:
        raise AssertionError(f"清单里的非公开函数：{sorted(unknown)}")
    if missing := pub - set(layer_of):
        raise AssertionError(f"未映射到层的函数：{sorted(missing)}")
    if bad := set(layer_of.values()) - set(layers):
        raise AssertionError(f"层不在清单里：{sorted(bad)}")

    # ── README 各小节（小节限定取值，防历史措辞被误配）──
    head = rd.head
    if "十一层" not in rd("架构总览"):
        raise AssertionError("README 架构总览不再写「十一层」——请同步生成器")
    py = grab(r"Python ≥ ([\d.]+)", rd("平台支持"), "Python 版本")
    pit_max = grab(r"P1~P(\d+)", head, "坑号上限")
    install_line = grab(r"(?m)^[^\n]*pip install[^\n]*", rd("快速上手"), "安装行")
    deps_note = grab(r"# (.+)$", install_line, "依赖说明")
    diag = re.search(r"单基金深度诊断常态 \*\*(.+?)\*\*（(.+?)）", rd("特性一览"))
    if not diag:
        raise AssertionError("README 措辞漂移：特性一览里的诊断预算")
    budget_line = f"单基金深度诊断常态 {diag.group(1)}（{diag.group(2)}）。"
    rules_line = grab(r"(?m)^(东财风控阈值：[^\n]+)", skill, "风控阈值句").rstrip("：") + "。"
    sub = dict(split_heads(rd("防封设计"), 3)[1])
    budget_head = [k for k in sub if k.startswith("预算表")]
    if len(budget_head) != 1 or tuple(table_rows(sub[budget_head[0]])[0]) != ("场景", "预算"):
        raise AssertionError("README 预算表表头措辞漂移")
    braw = table_rows(sub[budget_head[0]])
    if bad := [r for r in braw if len(r) != 2]:
        raise AssertionError(f"README 预算表有非 2 列的行：{bad[:2]}")
    budget = [tuple(r) for r in braw][1:]
    ledger = [tuple(r) for r in budget if "`" not in r[1] and len(plain(r[1])) <= 44][:7]
    coverage = parse_coverage(rd("数据覆盖"))
    cov_lines = [ln for ln in rd.body("数据覆盖").splitlines()]
    last_row = max(i for i, ln in enumerate(cov_lines) if ln.startswith("|"))
    coverage_post = "\n".join(cov_lines[last_row + 1:]).strip()
    arch_pre, arch = parse_arch(rd("架构总览"))     # 返回 (前言, 层列表)
    if arch is None:
        print("告警：架构总览 ASCII 图未解析出 11 层，页面回退为原样代码块", file=sys.stderr)

    # ── 能力分组 ↔ 架构总览的层：多对多，靠 LAYER_TREE 交叉校验 ──
    def tree_of(layer: str) -> tuple:
        base = layer.split("（", 1)[0].strip()
        if base not in LAYER_TREE:
            raise AssertionError(f"能力分组「{layer}」未登记到 LAYER_TREE（登记名 = 括号前 = {base!r}）")
        return LAYER_TREE[base]

    mapped = {n for x in layers for n in tree_of(x)}
    if arch and (lost := mapped - {L["num"] for L in arch}):
        raise AssertionError(f"LAYER_TREE 指向架构总览里不存在的层：{sorted(lost)}")
    if arch and (lost := {L["num"] for L in arch} - mapped):
        raise AssertionError(f"架构总览的这些层没有对应的能力分组：{sorted(lost)}")

    # ── examples / SKILL ──
    ex_first, env, scenes, faq = parse_examples(examples)
    if not ex_first.startswith("# fund-data 复制即用示例") or not re.search(rf"V{re.escape(ver)}$", ex_first):
        raise AssertionError(f"examples.md 首行措辞或版本漂移：{ex_first!r}")
    skill_heads = [t for t, _ in split_heads(skill, 2)[1]]
    if odd := [t for t in skill_heads if not t.startswith(("防封铁律", "场景路由", "失效与备源路由"))
               and t.split("（")[0] not in SKILL_H2_SKIP]:
        raise AssertionError(f"SKILL.md 出现未归类的小节：{odd}")
    if stale := [s for s in SKILL_H2_SKIP if not any(t.startswith(s) for t in skill_heads)]:
        raise AssertionError(f"SKILL_H2_SKIP 里登记的小节已不存在：{stale}")
    routes_tables = parse_skill(skill)
    routes_of, scenes_of = {}, {}
    for scenario, chain, cost in routes_tables["routes"]:
        for name in pub_order:
            if re.search(rf"`{re.escape(name)}(?!\w)", chain):      # 只认代码串形态；长名不被短名前缀误配
                routes_of.setdefault(name, []).append((plain(scenario), plain(cost)))
    for n, title, text in scenes:
        for name in pub_order:
            if re.search(rf"(?<![\w.]){re.escape(name)}(?!\w)", text):
                scenes_of.setdefault(name, []).append((n, title))
    quick = parse_quick(rd("快速上手"))
    stats = [
        ("公开函数", f"{len(pub_order)}<small>个</small>"),
        ("上游接口", grab(r"覆盖 (\d+) 个上游接口", head, "上游接口数") + "<small>个</small>"),
        ("离线断言", grab(r"默认离线：(\d+) 条断言", rd("测试与质量"), "离线断言数") + "<small>条</small>"),
        ("core 测试", grab(r"独立测试 (\d+) 条", head, "core 测试数") + "<small>条</small>"),
        ("实测坑入册", f"{pit_max}<small>条</small>"),
        ("数据覆盖", f"{len(coverage)}<small>类</small>"),
    ]

    def url(page: str, anchor: str = "") -> str:
        """原始 HTML 里的站内链接（MkDocs 不改写 HTML 属性，必须直接写最终 URL 形态）。"""
        return ("../" if page == HOME else f"{page}/") + (f"#{anchor}" if anchor else "")

    D = dict(
        rd=rd, linker=linker, pub_order=pub_order, ver=ver, py=py, url=url,
        asset_v=asset_versions(assets),
        nodes=nodes, doc=doc, summary=summary, layer_of=layer_of, row_of=row_of,
        layers=layers, catalog=tables, catalog_notes=notes,
        features=parse_features(rd.body("特性一览")), arch=arch, arch_pre=arch_pre,
        coverage=coverage, coverage_post=coverage_post,
        ledger=[(r[0], *split_budget(r[1])) for r in ledger],
        budget_line=budget_line, rules_line=rules_line, deps_note=deps_note,
        install=quick["install"], quick=quick, skill=routes_tables,
        routes_of=routes_of, scenes_of=scenes_of, scenes=scenes, env=env, faq=faq,
        stats=stats, pit_max=pit_max, changelog=parse_changelog(rd.body("版本历史")),
        skill_install=grab(r"(安装为 Agent skill：.+)", rd("仓库结构"), "skill 安装说明"),
        funds_wan=grab(r"全量 (\d+(?:\.\d+)?) 万只", catalog_md, "基金只数"),
    )

    # ── 渲染 + 断言 ──
    pages = {
        HOME: render_home(D), "start": render_start(D), "catalog": render_catalog(D),
        "api": render_api(D), "recipes": render_recipes(D), "faq": render_faq(D),
        "antiban": render_antiban(D), "architecture": render_architecture(D),
        "pitfalls": render_pitfalls(D), "changelog": render_changelog(D), "about": render_about(D),
    }
    mkdocs = render_mkdocs(D)
    if set(pages) != set(PAGES) or [p for _, p in NAV] != [x for x in PAGES if x != HOME]:
        raise AssertionError("页面集合与导航不一致")
    if unused := rd.unused(README_H2_SKIP):        # 页面都渲染完再看：README 的小节一个都不许漏
        raise AssertionError(f"README 有小节没进站点：{unused}——请决定它进哪一页")

    for name, text in {**pages, "mkdocs.yml": mkdocs}.items():
        if not text.strip():
            raise AssertionError(f"{name} 为空")
        privacy_check(name, text)
    for name in pub_order:                       # 每个公开函数在 api 页恰有一个锚点
        hits = re.findall(rf"(?m)^### {re.escape(name)} \{{ #{re.escape(name)} ", pages["api"])
        if len(hits) != 1:
            raise AssertionError(f"api 页中 {name} 的锚点出现 {len(hits)} 次（应为 1）")
    for page, text in pages.items():             # 全站的函数锚点链接必须真实存在
        for target in re.findall(r"api(?:\.md#|/#)(\w+)", text):
            if target not in pub:
                raise AssertionError(f"{page} 链到不存在的函数锚点 {target}")
        for target in re.findall(r"architecture/#(layer-\d+)", text):
            if f'id="{target}"' not in pages["architecture"]:
                raise AssertionError(f"{page} 链到不存在的层锚点 {target}")
        for target in re.findall(r'recipes/#(scene-\d+)', text):
            if f"{{ #{target} }}" not in pages["recipes"]:
                raise AssertionError(f"{page} 链到不存在的场景锚点 {target}")

    # ── 落盘：输出目录已验过；旧页只按上次的清单清，不按通配符删 ──
    files = ["mkdocs.yml", *(f"docs/{p}.md" for p in pages)]
    manifest = out / MANIFEST
    old = json.loads(manifest.read_text(encoding="utf-8"))["files"] if manifest.is_file() else []
    for rel in sorted(set(old) - set(files)):
        p_rel = Path(rel)
        if p_rel.is_absolute() or ".." in p_rel.parts:
            raise AssertionError(f".fd-manifest.json 里的路径不合法：{rel!r}")
        orig = out / rel
        if orig.is_symlink():                      # 链接只摘链接本身，unlink 不跟随
            orig.unlink()
            continue
        stale = guard_out(orig, repo)
        if stale != out and out not in stale.parents:
            raise AssertionError(f"清单路径解析后落在输出目录之外：{rel!r}")
        if stale.is_file():
            stale.unlink()
    sizes = {"mkdocs.yml": write(out / "mkdocs.yml", mkdocs, repo)}
    for page, text in pages.items():
        sizes[f"docs/{page}.md"] = write(docs / f"{page}.md", text, repo)
    manifest.write_text(json.dumps({"files": sorted(files)}, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")

    # ── 站点素材：隐私扫描全部文本文件 → 整目录换新（旧素材不残留；rmtree 不跟随目录内的 symlink）──
    for f in sorted((assets / "overrides").rglob("*")):
        if f.is_file() and f.suffix in ASSET_TEXT:
            privacy_check(f"site_assets/{f.relative_to(assets)}", f.read_text(encoding="utf-8"))
    if ov.exists():
        shutil.rmtree(ov)
    shutil.copytree(assets / "overrides", ov, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for rel in ASSET_FILES:
        if not (out / rel).is_file():
            raise AssertionError(f"站点素材未拷入：{rel}")
    if (docs / "docs").exists():
        raise AssertionError("出现 docs/docs 嵌套——落盘路径错误")

    sw_patched = not (rd.has("申万行业指数层") or f"### {SW_LAYER}" in catalog_md or sw_covered)
    print(f"fund-data 文档站 v{ver}：{len(pub_order)} 个公开函数 · {len(layers)} 个能力分组 · "
          f"{len(scenes)} 个场景 · {len(D['changelog'])} 个版本条目")
    for rel, n in sorted(sizes.items()):
        print(f"  {n:>8,} B  {rel}")
    print(f"  {sum(sizes.values()):>8,} B  合计（不含站点素材）")
    if sw_patched:
        print("提示：README 端点能力清单缺申万层 4 行——站点已补段，建议上游 README 同步补齐。")
    if arch is None:
        print("提示：架构总览 ASCII 图未解析出 11 层，页面回退为原样代码块。")
    return 0


def parse_quick(md: str) -> dict:
    """README 快速上手 → {install: bash 行, prelude: 首个场景标记前的 python 行,
    blocks: [(编号, 标题, 代码)]}。场景以注释行 `# ── 场景 N：标题 ──` 为界。"""
    bash = re.search(r"(?ms)^```bash\n(.*?)^```", md)
    py = re.search(r"(?ms)^```python\n(.*?)^```", md)
    if not bash or not py:
        raise AssertionError("README 快速上手找不到 bash / python 代码块")
    parts = re.split(r"(?m)^# ── 场景 (\d+)：(.*?)\s*──\s*$", py.group(1))
    blocks = [(int(parts[i]), parts[i + 1].strip(), parts[i + 2].strip("\n"))
              for i in range(1, len(parts), 3)]
    if len(blocks) < 5:
        raise AssertionError(f"README 快速上手只拆出 {len(blocks)} 个场景（预期 ≥5）")
    return {"install": bash.group(1).strip(), "prelude": parts[0].strip("\n"), "blocks": blocks}


if __name__ == "__main__":
    sys.exit(main())
