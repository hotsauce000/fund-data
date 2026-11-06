# -*- coding: utf-8 -*-
"""
holding_penetration — 重仓白话地图组合器（R9 场景 04 原型）

⚠️ 已并入 fund-data V0.2.0 的 L6 跨域穿透层（fund_data.penetrate / plain_text_map，
含本原型所缺的确定性平局裁决等修复）——本文件仅作原型存档，请勿继续在此维护。

组合两个底层 skill：
  fund-data（基金域）: fund_holdings() 季度前十大 + 权重
  a-stock-data（股票域）: tencent_quote() 批量实时行情（腾讯通道不封IP，1请求）
                          eastmoney_concept_blocks() 个股板块归属（东财，1请求/股，限流）

请求预算：单基金穿透 = 1(持仓，缓存后0) + 1(腾讯批量) + N(板块，每股1个，当日磁盘缓存)
  → 白酒基金 10 只重仓首跑冷启动最多 ≈14 请求（含 fund_snapshot/fund_list 缓存 miss 各 1）
    约 15 秒；当日重复穿透 = 2 请求（持仓+板块全缓存，腾讯行情现查）

聚合方法（核心设计）：
  不依赖行业/概念分类法——对每只重仓股的板块标签做**共现加权**：
  同一标签被多只重仓股共有且权重高 = 该基金的真实主题。
  '白酒Ⅱ/白酒Ⅲ' 分级后缀归并成 '白酒'，指数成分/地域/两融类噪声标签入停用词表。
"""
import re
import time
import random
import urllib.request
from collections import defaultdict

import fund_data as fd

# ══════════════════════════════════════════════════════════════════
# 股票域通道（内嵌自 a-stock-data，保持其防封纪律）
# ══════════════════════════════════════════════════════════════════

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# 东财 push2 限流（与 a-stock-data 的 em_get 同款参数：1s+抖动+会话复用；
# 与 fund_data 的 ttfund_get 是两条独立限流线，分管不同子域）
import requests as _requests
_EM_SESSION = _requests.Session()
_EM_SESSION.headers.update({"User-Agent": UA})
_em_last = [0.0]
EM_MIN_INTERVAL = 1.0
EM_REQUEST_COUNT = [0]


def em_get(url, params=None, headers=None, timeout=15, **kw):
    wait = EM_MIN_INTERVAL - (time.time() - _em_last[0])
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.4))
    try:
        EM_REQUEST_COUNT[0] += 1
        return _EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kw)
    finally:
        _em_last[0] = time.time()


def em_market_code(code: str) -> int:
    """东财 secid 市场号：沪=1，深/北=0（照抄 a-stock-data 规则，勿用 startswith('6')）。

    ⚠️ 92 北交所现行号段必须先于 9x 判断（V3.7.0 实测 920982 → m=0，
    与深市共用 0）——错路由会拼出 "1.920982" → data:null。
    """
    c = str(code).lower()
    if c.startswith("92") or c[:1] in ("4", "8"):     # 北交所：920 现行 + 4x/8x 老段
        return 0
    if c.startswith(("sh", "1.")) or c[:1] in ("5", "6", "9"):
        return 1
    return 0


# ── 腾讯批量行情（不封 IP，1 请求拿全部重仓股的价/PE/PB/市值）──────────
_SH_INDEX = {"000300", "000905", "000016", "000688", "000852", "000010"}


def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯实时行情批量（内嵌自 a-stock-data §1.2，返回价/涨跌/PE/PB/市值）。"""
    prefixed = []
    for c in codes:
        c = str(c).lower()
        if c.startswith(("sh", "sz", "bj")):
            p = c
        elif c.startswith("92") or c[:1] in ("4", "8"):   # 北交所须先于 9x 判断（92→bj 非 sh）
            p = f"bj{c}"
        elif c in _SH_INDEX or c[:1] in ("5", "6", "9"):
            p = f"sh{c}"
        else:
            p = f"sz{c}"
        prefixed.append(p)
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    data = urllib.request.urlopen(req, timeout=10).read().decode("gbk")
    result = {}
    for line in data.strip().split(";"):
        if "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        v = line.split('"')[1].split("~")
        if len(v) < 53:
            continue
        f = lambda i: float(v[i]) if v[i] else 0.0
        result[key[2:]] = {
            "name": v[1], "price": f(3), "change_pct": f(32),
            "turnover_pct": f(38), "pe_ttm": f(39),
            "float_mcap_yi": f(44), "mcap_yi": f(45), "pb": f(46),
        }
    return result


# ── 个股板块归属（东财 slist，每股 1 请求，当日磁盘缓存）──────────────
def concept_blocks(stock_code: str, use_cache: bool = True) -> list[str]:
    """个股板块标签列表（内嵌自 a-stock-data §3.3；缓存到 ~/.fund_data_cache/penetration/）。"""
    if not re.fullmatch(r"\d{6}", str(stock_code)):
        raise ValueError(f"股票代码格式错误: {stock_code!r}（需 6 位数字，防异常上游数据拼入缓存路径）")
    rel = f"penetration/{stock_code}.json"
    if use_cache:
        cached = fd._cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    params = {"fltt": "2", "invt": "2",
              "secid": f"{em_market_code(stock_code)}.{stock_code}",
              "spt": "3", "pi": "0", "pz": "200", "po": "1",
              "fields": "f12,f14,f3,f128"}
    r = em_get("https://push2.eastmoney.com/api/qt/slist/get", params=params,
               headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    d = r.json()
    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    tags = [it.get("f14", "") for it in items if it.get("f14")]
    if not tags:
        raise RuntimeError(f"{stock_code} 板块归属为空（secid 路由或风控），拒绝静默空")
    fd._cache_write(rel, tags)
    return tags


# ══════════════════════════════════════════════════════════════════
# 聚合层：共现加权 → 主题词 / 估值概览 / 白话地图
# ══════════════════════════════════════════════════════════════════

# 噪声停用词：指数成分/交易机制/地域板块——不是"这只基金在押注什么"的信号。
# ⚠️ 取舍边界：国企改革/央企/一带一路/老字号 对多数基金是壳噪声，但对相应主题基金
# 恰是真实主题——本表按"通用穿透"取舍（宁滤勿噪），主题基金专用分析请自行调整本表。
_TAG_STOP = re.compile(
    r"概念|板块|融资融券|转融券|沪股通|深股通|MSCI|标普|标准普尔|富时|罗素|道琼斯"
    r"|上证|深证|沪深|中证|国证|创业板|科创板|北交所|HS\d|SH\d|SZ\d|央视|QFII|RQFII"
    r"|同花顺|东财|基金重仓|机构重仓|预盈预增|预亏预减|股权激励|回购|增持|壳资源"
    r"|次新股|高送转|含可转债|B转H|AB股|GDR|老字号|国企改革|央企|国资|PPP|一带一路")
_SUFFIX_ROMAN = re.compile(r"[ⅠⅡⅢⅣⅤ]$|I+$")


def _clean_tag(tag: str):
    """板块名 → 主题词；噪声返回 None。'白酒Ⅲ'→'白酒'。"""
    t = tag.strip()
    if not t or len(t) < 2 or _TAG_STOP.search(t):
        return None
    return _SUFFIX_ROMAN.sub("", t).strip() or None


def penetrate(query: str, topn: int = 10) -> dict:
    """重仓穿透主入口：基金 → 重仓股权重 → 股票域行情+板块 → 聚合白话地图。

    单股失败不中断整体：跳过该股的主题聚合，记入 failed_stocks 并在输出标注
    （批量组合场景 9 只成功不该被 1 只风控失败作废）。
    """
    hold = fd.fund_holdings(query)                     # 基金域（缓存后 0 请求）
    if not hold:
        raise RuntimeError(f"{query} 无股票持仓记录（债基/货基？）——"
                           f"股票穿透需要股票重仓；债券穿透请用 fund_snapshot().top_bonds")
    latest = max(h["period"] for h in hold)            # 不依赖上游表序，按日期取最新
    pool = [h for h in hold if h["period"] == latest][:topn]

    failed_stocks: list[dict] = []                     # per-stock 降级名单
    picks = []
    for h in pool:
        if h.get("nav_pct") is None:                   # 权重解析失败（None 防 TypeError）
            failed_stocks.append({"code": h["stock_code"], "name": h["stock_name"],
                                  "reason": "占净值比缺失"})
            continue
        picks.append(h)
    if not picks:
        raise RuntimeError(f"{query} 最新报告期 {latest} 全部重仓股权重缺失，无法穿透")
    weight_sum = sum(h["nav_pct"] for h in picks)

    quotes = tencent_quote([h["stock_code"] for h in picks])   # 股票域行情（1 请求）

    theme = defaultdict(float)                         # 主题词 → 累计净值权重
    unclassified_weight = 0.0
    for h in picks:
        try:
            tags = concept_blocks(h["stock_code"])
        except Exception as e:                         # 风控/解析失败：跳过该股主题，不中断
            failed_stocks.append({"code": h["stock_code"], "name": h["stock_name"],
                                  "reason": f"板块获取失败: {str(e)[:60]}"})
            unclassified_weight += h["nav_pct"]
            continue
        # 🔴 每股先去重再累计：同一股票的 白酒Ⅱ/白酒Ⅲ 清洗后都是"白酒"，
        #    不去重会把权重翻倍（实测白酒基金曾算出 259%）
        uniq = {k for k in (_clean_tag(t) for t in tags) if k}
        for key in uniq:
            theme[key] += h["nav_pct"]

    top_themes = sorted(theme.items(), key=lambda x: -x[1])[:6]
    pe_list = sorted(q["pe_ttm"] for q in quotes.values() if q["pe_ttm"] > 0)
    pe_med = None
    if pe_list:                                        # 标准中位数：偶数取中间两值均值
        n = len(pe_list)
        pe_med = pe_list[n // 2] if n % 2 else (pe_list[n // 2 - 1] + pe_list[n // 2]) / 2

    detail = [{**h, "quote": quotes.get(h["stock_code"], {})} for h in picks]
    return {
        "fund": fd.fund_snapshot(query)["name"],
        "period": latest, "topn_weight_pct": round(weight_sum, 2),
        "detail": detail,
        "themes": [(k, round(v, 2)) for k, v in top_themes],
        "pe_median_profitable": pe_med,                # 仅盈利股（PE>0）
        "failed_stocks": failed_stocks,
        "unclassified_weight_pct": round(unclassified_weight, 2),
    }


def plain_text_map(p: dict) -> str:
    """白话地图文本（R9 场景 04 的核心输出形态）。"""
    top = p["themes"][0] if p["themes"] and p["themes"][0][1] >= 5 else None
    lines = [
        f"《{p['fund']} 里装了什么》（{p['period']} 季报，前十大占净值 {p['topn_weight_pct']}%）",
        "",
        f"一句话：这只基金主要是{top[0]}（相关持仓权重约 {top[1]:.0f}%）。"
        if top else "一句话：持仓分散，无明显单一主题。",
        "",
        f"{'股票':<12}{'权重':>7}{'现价':>9}{'今日':>8}{'PE':>9}{'PB':>7}",
    ]
    for h in p["detail"]:
        q = h.get("quote") or {}
        pe = q.get("pe_ttm", 0)
        pe_txt = "亏损" if pe < 0 else f"{pe:.1f}"
        lines.append(f"{h['stock_name']:<12}{h['nav_pct']:>6.1f}%{q.get('price', 0):>9.2f}"
                     f"{q.get('change_pct', 0):>7.1f}%{pe_txt:>9}{q.get('pb', 0):>7.2f}")
    if len(p["themes"]) > 1:
        lines.append("\n主题词排行: " + "、".join(f"{k} {v:.0f}%" for k, v in p["themes"][:5]))
    if p.get("pe_median_profitable"):
        lines.append(f"盈利股 PE 中位数 {p['pe_median_profitable']:.1f} 倍"
                     f"（已剔除亏损股；{'高于' if p['pe_median_profitable'] > 30 else '不高于'}30 倍参考线）")
    if p.get("failed_stocks"):
        names = "、".join(f["name"] for f in p["failed_stocks"])
        lines.append(f"⚠ {len(p['failed_stocks'])} 只未完成分类（权重 "
                     f"{p.get('unclassified_weight_pct', 0):.1f}%）: {names}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "161725"
    t0 = time.time()
    p = penetrate(target)
    print(plain_text_map(p))
    print(f"\n[请求账本] fund-data: {fd.TT_REQUEST_COUNT[0]} 次 {fd.TT_REQUEST_COUNT[1]}"
          f" | 东财股票域: {EM_REQUEST_COUNT[0]} 次 | 耗时 {time.time() - t0:.1f}s")
