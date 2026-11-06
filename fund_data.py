# -*- coding: utf-8 -*-
"""
fund-data V0.1.2 — 基金底层数据工具包（天天基金主源 + 蛋卷备胎 + 本地计算）

请求预算契约（防封铁律，详见 SKILL.md 第一章）：
  1. 单基金深度诊断 ≤2 请求（1×pingzhongdata，0~1×lsjz）
  2. 全市场筛选只允许 rankhandler 翻页（1请求=50只），禁止 for fund in all: snapshot(fund)
  3. 同一基金当日快照磁盘缓存命中 = 0 请求
  4. 所有东财/天天请求走 ttfund_get()：串行 + 1.5s 最小间隔 + 会话复用 + 强制 HTTPS

坑记录（2026-11-05 实测）：
  P1  pingzhongdata 必须 HTTPS（UA 无关）——HTTP 静默返回 0 字节，HTTP 200 照常
  P2  lsjz 三连坑：①pageSize 服务端钳制 20（请求 49~200 都只回 20 条，500 静默 0 条）
      ②TotalCount 在响应顶层不在 Data 内 ③必须显式日期区间。
      → 固定 pageSize=20 + 顶层 TotalCount + 实收条数强校验
  P3  pingzhongdata 变量按基金类型选择性存在（如白酒基金无 Data_industryAllocation）
      → 解析器容忍缺失，返回 missing 列表，绝不静默空
  P3b pingzhongdata 值后挂 `;/*注释*/var`——按相邻 var 位置切片+剥注释，
      否则字符串值吞掉后文（实测 syl_1y 坏过）；stockCodesNew 是 ["1.600519",...] secid 数组
  P4  基金代码与股票代码同段碰撞（000001=华夏成长混合 vs 平安银行）
      → 代码不在基金清单时明确报错并引导 a-stock-data，不静默空
  P5  fundgz.1234567.com.cn 盘中估值已下线（404），蛋卷 growth 净值接口亦不可用
      → 净值无跨域备胎，东财系全挂时诚实报缺；盘中估算走 V0.3 本地推演
  P6  unitMoney 事件标记存在但**金额数字被上游剥离**（实测"分红：每10份派现金元"），
      且"拆分"事件混在同一字段——解析分红金额不可靠
      → 复权引擎优先链接官方 equityReturn（已处理分红/拆分），unitMoney 解析仅作回退
  P7  E3 lsjz 返回**新→旧**序，E2 netWorthTrend 是**旧→新**序——降级快照必须反转，
      否则喂给指标计算收益方向全反（静默错，比静默空更危险）
"""
import json
import re
import time
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

try:  # pandas 可选：仅 fund_metrics 的 DataFrame 输出用到，缺了降级为 list[dict]
    import pandas as pd
except ImportError:
    pd = None

__version__ = "0.1.2"

# ══════════════════════════════════════════════════════════════════
# L1 前置层：限流入口 / 缓存目录 / 请求计数
# ══════════════════════════════════════════════════════════════════

FUND_CACHE_DIR = Path.home() / ".fund_data_cache"
SNAPSHOT_TTL_HOURS = 20          # 距上次抓取 20h 失效（滚动窗口，非自然日切分）
FUND_LIST_TTL_DAYS = 30
_BJ_TZ = timezone(timedelta(hours=8))   # E2 时间戳按北京时区解析，非东八区机器不漂移

TT_MIN_INTERVAL = 1.5            # 东财/天天系最小请求间隔（秒）
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TT_SESSION = requests.Session()
TT_SESSION.headers.update({"User-Agent": UA})
_tt_last_call = [0.0]
TT_REQUEST_COUNT = [0, {}]       # [总请求数, {host: 次数}] — 预算审计用，ttfund_get 自增


def ttfund_get(url: str, params: Optional[dict] = None,
               headers: Optional[dict] = None, timeout: int = 20, **kwargs):
    """天天/东财/蛋卷统一请求入口：强制 HTTPS（坑P1）+ 串行限流 + 会话复用 + 瞬态重试1次。

    所有 HTTP 端点必须经此函数，禁止裸 requests.get —— 既防封又给预算审计留计数。
    """
    # 坑P1：pingzhongdata 的 HTTP 静默返 0 字节。统一升级 HTTPS（其余东财端点 https 均可达）。
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    for attempt in (1, 2):                            # 瞬态网络抖动（读超时/连接重置）重试一次
        wait = TT_MIN_INTERVAL - (time.time() - _tt_last_call[0])
        if wait > 0:
            time.sleep(wait + random.uniform(0.05, 0.3))
        try:
            TT_REQUEST_COUNT[0] += 1
            host = re.sub(r"^https?://", "", url).split("/")[0]
            TT_REQUEST_COUNT[1][host] = TT_REQUEST_COUNT[1].get(host, 0) + 1
            return TT_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2.0)                           # 抖一下再试最后一次
        finally:
            _tt_last_call[0] = time.time()


def _cache_read(rel: str, max_age_hours: Optional[float]) -> Optional[dict]:
    """读磁盘缓存；rel 相对 FUND_CACHE_DIR。过期/损坏返回 None（损坏即重下，坑见 L5 演练）。"""
    p = FUND_CACHE_DIR / rel
    if not p.exists():
        return None
    if max_age_hours is not None:
        age_h = (time.time() - p.stat().st_mtime) / 3600
        if age_h > max_age_hours:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None                                    # 缓存损坏 → 视为 miss 重下


def _cache_write(rel: str, payload: dict) -> None:
    p = FUND_CACHE_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════
# L1 前置层：全量基金清单（主键表，缓存 30 天）
# ══════════════════════════════════════════════════════════════════

_FUND_LIST_MEM: list[dict] = []     # 进程级缓存（同一会话多次调用零 IO）


def fund_list(force_refresh: bool = False) -> list[dict]:
    """全量基金清单 ~2万只：[{code, pinyin, name, type}, ...]

    E1: fund.eastmoney.com/js/fundcode_search.js（首载 1 请求，之后磁盘 30 天 / 进程内 0 开销）
    """
    if _FUND_LIST_MEM and not force_refresh:
        return _FUND_LIST_MEM
    cached = None if force_refresh else _cache_read(
        "fund_list.json", max_age_hours=FUND_LIST_TTL_DAYS * 24)
    if cached:
        _FUND_LIST_MEM[:] = cached
        return _FUND_LIST_MEM
    url = "https://fund.eastmoney.com/js/fundcode_search.js"
    r = ttfund_get(url, headers={"Referer": "https://fund.eastmoney.com/"})
    r.raise_for_status()
    text = r.text
    brace = text.find("[")
    if brace < 0:
        raise RuntimeError(f"基金清单响应异常（无 JSON 数组）: {text[:120]!r}")
    rows, _ = json.JSONDecoder().raw_decode(text[brace:])
    out = [{"code": x[0], "pinyin": x[1], "name": x[2], "type": x[3]} for x in rows]
    if len(out) < 5000:                               # 正常 ~2万条，骤降=上游结构变了
        raise RuntimeError(f"基金清单仅 {len(out)} 条，疑似上游结构变更，拒绝缓存脏数据")
    _cache_write("fund_list.json", out)
    _FUND_LIST_MEM[:] = out
    return out


def norm_fund_code(query: str) -> dict:
    """基金代码/名称/拼音 → 归一化结果。

    返回 {query, matches: [{code,name,type,pinyin}], resolved, note}
      - 6 位代码且在基金清单 → resolved=code
      - 6 位代码但不在清单 → ValueError（坑P4：可能是股票代码，引导 a-stock-data）
      - 名称/拼音 → 精确→包含匹配；多候选（如 A/C 份额）resolved=None 列出全部，绝不猜
    """
    q = str(query).strip()
    if not q:
        raise ValueError("查询为空：需要 6 位基金代码 / 基金名称 / 拼音缩写")
    digits = q.replace(".OF", "").replace(".of", "")
    if re.fullmatch(r"\d{6}", digits):
        for f in fund_list():
            if f["code"] == digits:
                return {"query": q, "matches": [f], "resolved": digits,
                        "note": f["name"]}
        raise ValueError(
            f"{digits} 不在基金清单（{len(fund_list())} 只）中——它可能是股票代码。"
            f"查询股票请使用 a-stock-data skill；基金代码请核对天天基金页面。")
    # 名称 / 拼音匹配
    fl = fund_list()
    exact = [f for f in fl if f["name"] == q or f["pinyin"].lower() == q.lower()]
    if len(exact) == 1:
        return {"query": q, "matches": exact, "resolved": exact[0]["code"],
                "note": exact[0]["name"]}
    if len(exact) > 1:                                # 典型：同基金多份额（A/C/E/后端）
        return {"query": q, "matches": exact, "resolved": None,
                "note": f"命中 {len(exact)} 只同份额族基金，需指定 6 位代码消歧"}
    contains = [f for f in fl if q in f["name"] or q.lower() in f["pinyin"].lower()]
    if not contains:
        raise ValueError(f"{q!r} 未匹配到任何基金（清单 {len(fl)} 只）。"
                         f"建议用 6 位代码或完整基金名称。")
    if len(contains) == 1:
        return {"query": q, "matches": contains, "resolved": contains[0]["code"],
                "note": contains[0]["name"]}
    contains.sort(key=lambda f: len(f["name"]))       # 短名优先，取前 8 个供选择
    return {"query": q, "matches": contains[:8], "resolved": None,
            "note": f"模糊命中 {len(contains)} 只，请从候选中选择"}


def resolve_fund_code(query: str) -> str:
    """norm_fund_code 的便捷包装：唯一命中返回代码，多候选/未命中直接抛错。"""
    r = norm_fund_code(query)
    if r["resolved"]:
        return r["resolved"]
    cands = " / ".join(f"{f['code']}{f['name']}" for f in r["matches"][:5])
    raise ValueError(f"{query!r} 有多个候选：{cands} ...")


# ══════════════════════════════════════════════════════════════════
# L4 解析层 + V0.1 端点：fund_snapshot（E2 主端点，1 请求 = 90% 静态数据）
# ══════════════════════════════════════════════════════════════════

_PZ_VARS_KNOWN = [
    "fS_name", "fS_code", "fund_sourceRate", "fund_Rate", "fund_minsg", "ishb",
    "stockCodes", "stockCodesNew", "zqCodes", "zqCodesNew",
    "syl_1n", "syl_1y", "syl_3y", "syl_6y",
    "Data_netWorthTrend", "Data_ACWorthTrend", "Data_grandTotal",
    "Data_rateInSimilarType", "Data_rateInSimilarPersent",
    "Data_fluctuationScale", "Data_holderStructure", "Data_assetAllocation",
    "Data_performanceEvaluation", "Data_fundSharesPositions",
    "Data_currentFundManager", "Data_buySedemption", "Data_industryAllocation",
]


def _parse_js_vars(text: str) -> dict:
    """pingzhongdata 的 `var X = <value>;` 全量解析。

    value 形态：JSON 数组/对象、双引号字符串、true/false、裸数字。
    🔴 值后面常挂 `;/*注释*/` 再接下一个 var——必须按"相邻两个 var 声明的位置"切片
    并剥离块注释，否则字符串值会吞掉后面一整段（实测 syl_1y 就这样坏掉过）。
    坑P3：按基金类型选择性存在 → 未出现的变量不在结果里，由调用方记入 missing。
    """
    ms = list(re.finditer(r"var\s+([A-Za-z_0-9]+)\s*=", text))
    out = {}
    for i, m in enumerate(ms):
        name = m.group(1)
        if name in out:
            continue
        seg = text[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(text)]
        seg = re.sub(r"/\*.*?\*/", "", seg, flags=re.S).strip().rstrip(";").strip()
        if not seg:
            continue
        if seg[0] in "{[":
            try:
                out[name], _ = json.JSONDecoder().raw_decode(seg)
                continue
            except json.JSONDecodeError:
                pass
        sm = re.match(r'^"((?:[^"\\]|\\.)*)"', seg)
        if sm:
            out[name] = sm.group(1)
            continue
        if seg in ("true", "false"):
            out[name] = (seg == "true")
            continue
        try:
            out[name] = float(seg) if ("." in seg or "e" in seg.lower()) else int(seg)
        except ValueError:
            out[name] = seg                            # 未知形态原样保留，绝不静默丢
    return out


def _div_from_unit_money(unit_money) -> float:
    """unitMoney → 每份现金分红（元）。

    ⚠️ 坑P6（实测）：unitMoney 事件标记存在但金额常被上游剥离（"分红：每10份派现金元"），
    且"拆分：每份基金份额折算份"混在同一字段——拆分不是现金分红，恒返回 0。
    本函数仅作 adjusted_nav 的回退路径；主路径是链接官方 equityReturn。
    支持"每10份派现金0.5元"（÷10）与"每份派现金0.0100元"（÷1）两种形态。
    """
    s = str(unit_money or "")
    if not s or "折算" in s or "拆分" in s:
        return 0.0                                    # 空 或 拆分/折算事件：无现金分红
    if "分红" not in s and "派" not in s:
        return 0.0                                    # 无现金分红标记
    m = re.search(r"每(\d+)份[^0-9]*([\d.]+)\s*元", s)
    if m:
        return float(m.group(2)) / float(m.group(1))
    m = re.search(r"([\d.]+)\s*元", s)                # 无"每N份"限定的兜底形态（E3口径）
    return float(m.group(1)) if m else 0.0


def _pair_rows_to_dicts(raw) -> list[dict]:
    """E2 通用表格形态 [[label...],[v1...],[v2...]] → [dict,...]（首行当表头）。"""
    if not raw or not isinstance(raw, list):
        return []
    rows = [r for r in raw if isinstance(r, list) and r]
    if len(rows) < 2:
        return []
    head = [str(x) for x in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:]]


def _echarts_to_records(obj) -> list[dict]:
    """{'categories':[...], 'series':[{name,data}...]} → [{_date, name:value}, ...]"""
    if not isinstance(obj, dict):
        return []
    cats = obj.get("categories") or []
    cols = {}
    for s in obj.get("series") or []:
        if isinstance(s, dict) and s.get("data") is not None:
            cols[str(s.get("name", "value"))] = s["data"]
    recs = []
    for i, c in enumerate(cats):
        row = {"_date": str(c)}
        for k, arr in cols.items():
            row[k] = arr[i] if i < len(arr) else None
        recs.append(row)
    return recs


def _normalize_table(raw) -> list[dict]:
    """E2 表格变量双格式兼容：echarts dict 或 [[label行],[值行]...] 嵌套数组 → [dict,...]。

    不同变量/不同版本两种形态都出现过，按实际类型分流，都不匹配返回 []（进 missing 告知）。
    """
    if isinstance(raw, dict):
        return _echarts_to_records(raw)
    if isinstance(raw, list):
        return _pair_rows_to_dicts(raw)
    return []


def _codes_list(x) -> list[str]:
    """stockCodes/zqCodes → 纯 6 位代码列表。

    双格式兼容：JSON 数组（实测 stockCodesNew 是 ["1.600519",...] secid 形态）或
    逗号分隔字符串。secid 前缀（0./1. 市场号）剥掉——喂 a-stock-data 用纯 6 位最通用。
    """
    if not x:
        return []
    raw = x if isinstance(x, list) else str(x).split(",")
    out = []
    for c in raw:
        c = str(c).strip()
        c = re.sub(r"^[01]\.", "", c) if re.fullmatch(r"[01]\.\d{6}", c) else c
        if c:
            out.append(c)
    return out


def fund_snapshot(query: str, force_refresh: bool = False) -> dict:
    """单基金一站式快照（E2 pingzhongdata，436KB → 20+ 数据集）。

    ⭐ 主端点：常态诊断只花这 1 个请求（当日磁盘缓存命中则 0 请求）。
    E2 网络失败/响应异常时自动走分拆降级（E3+E4a+蛋卷，见 _snapshot_degraded），
    返回体 source 标注 "degraded"，绝不静默空。
    返回结构见 keys；缺失数据集列入 missing（坑P3），绝不静默为空。
    """
    code = resolve_fund_code(query)
    rel = f"snapshots/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached and cached.get("_meta", {}).get("version") == __version__:
            return cached

    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    try:
        r = ttfund_get(url, headers={"Referer": "https://fund.eastmoney.com/"})
        r.raise_for_status()
    except requests.RequestException as e:
        return _snapshot_degraded(code, reason=f"E2 网络失败: {e}")
    text = r.content.decode("utf-8", errors="replace")
    # 坑P1 兜底：HTTPS 下仍拿到的空体/极小体 = 上游异常（新基金无数据或被风控）
    if len(r.content) < 1024:
        return _snapshot_degraded(
            code, reason=f"E2 响应仅 {len(r.content)} 字节（正常 400KB+），疑似上游异常")
    v = _parse_js_vars(text)
    missing = [k for k in _PZ_VARS_KNOWN if k not in v]
    # P0 防线：HTTP 200 + ≥1KB 的风控页/跳转页会解析不出关键变量——
    # 不校验就会产出 name=None 的全空快照还写入缓存污染 20 小时。识别为上游异常 → 走降级。
    if not v.get("fS_name") or ("Data_netWorthTrend" not in v and not v.get("ishb")):
        return _snapshot_degraded(
            code, reason="E2 响应缺少关键变量（fS_name/netWorthTrend），疑似风控页或非数据响应")

    nav = [{"date": datetime.fromtimestamp(int(p["x"]) / 1000, _BJ_TZ).strftime("%Y-%m-%d"),
            "nav": float(p["y"]),
            "daily_return": None if p.get("equityReturn") in (None, "") else float(p["equityReturn"]),
            "dividend": _div_from_unit_money(p.get("unitMoney"))}
           for p in (v.get("Data_netWorthTrend") or []) if isinstance(p, dict) and "x" in p]

    mgr_raw = (v.get("Data_currentFundManager") or [None])[0] or {}
    snap = {
        "_meta": {"code": code, "version": __version__,
                  "fetched_at": datetime.now().isoformat(timespec="seconds"),
                  "source": "eastmoney/pingzhongdata"},
        "name": v.get("fS_name"),
        "is_money_fund": bool(v.get("ishb")),          # 类型路由标志（货基走 V0.2 专项）
        "fees": {"origin_rate": v.get("fund_sourceRate"),
                 "current_rate": v.get("fund_Rate"),
                 "min_buy": v.get("fund_minsg")},
        "period_returns_pct": {                        # 官方口径收益率（也是测试 ground truth）
            "1month": _to_float(v.get("syl_1y")), "3month": _to_float(v.get("syl_3y")),
            "6month": _to_float(v.get("syl_6y")), "1year": _to_float(v.get("syl_1n"))},
        "manager": {"name": mgr_raw.get("name"), "tenure_start": mgr_raw.get("workTime"),
                    "fund_size_yi": mgr_raw.get("fundSize"),
                    "return_pct": mgr_raw.get("profit"), "star": mgr_raw.get("star")},
        "nav_history": nav,                            # 单位净值全史（含每日分红事件）
        "ac_nav_history": [{"date": datetime.fromtimestamp(int(p["x"]) / 1000, _BJ_TZ).strftime("%Y-%m-%d"),
                            "ac_nav": float(p["y"])}
                           for p in (v.get("Data_ACWorthTrend") or [])
                           if isinstance(p, dict) and "x" in p],
        "top_stocks": _codes_list(v.get("stockCodesNew") or v.get("stockCodes")),
        "top_bonds": _codes_list(v.get("zqCodesNew") or v.get("zqCodes")),
        "scale_history": _normalize_table(v.get("Data_fluctuationScale")),
        "holder_structure": _normalize_table(v.get("Data_holderStructure")),
        "asset_allocation": _normalize_table(v.get("Data_assetAllocation")),
        "stock_position_history": _normalize_table(v.get("Data_fundSharesPositions")),
        "industry_allocation": _normalize_table(v.get("Data_industryAllocation")),
        "rank_in_similar": v.get("Data_rateInSimilarPersent"),
        "buy_redemption_status": v.get("Data_buySedemption"),
        "missing_datasets": missing,                   # 坑P3：显式列出，调用方知情
    }
    _cache_write(rel, snap)
    return snap


def _snapshot_degraded(code: str, reason: str = "", years: float = 3.0) -> dict:
    """E2 失效时的分拆降级：E3 净值(近N年) + E4a 持仓 + 蛋卷 D1 档案。

    ⚠️ 降级快照不写缓存（下次调用会重试 E2 主端点）；数据集缺口显式列入 missing；
    净值窗口默认近 3 年（E2 的全史拿不到，这是降级的代价，调用方知情）。
    """
    missing: list[str] = ["Data_* 系列（E2 整体不可用）"]
    start = (date.today() - timedelta(days=int(years * 365))).isoformat()
    hist = fund_nav_history(code, start=start)        # E3 兜底净值（可能抛错→由调用方感知）
    if not hist:
        raise RuntimeError(f"降级失败：{code} 在 {start} 起无净值记录（E2/E3 双不可用或代码有误）")
    nav_hist = [{"date": h["date"], "nav": h["nav"],        # 🔴 E3 原生新→旧（坑P7），
                 "daily_return": h["daily_return_pct"],     # 必须反转为旧→新与 E2 口径一致，
                 "dividend": _div_from_unit_money(h.get("dividend"))}   # 否则指标方向全反
                for h in hist[::-1]]
    top_stocks: list[str] = []
    try:
        hold = fund_holdings(code)                    # E4a 持仓
        if hold:
            latest = hold[0]["period"]
            top_stocks = [h["stock_code"] for h in hold if h["period"] == latest]
    except Exception:
        missing.append("stockCodes/E4a")
    dj: dict = {}
    try:
        dj = fund_detail_danjuan(code)                # 蛋卷档案（跨域）
    except Exception:
        missing.append("danjuan/D1")
    return {
        "_meta": {"code": code, "version": __version__,
                  "fetched_at": datetime.now().isoformat(timespec="seconds"),
                  "source": "degraded(E3+E4a+D1)", "reason": reason},
        "name": dj.get("name"), "is_money_fund": None,
        "manager": {"name": dj.get("manager")},
        "nav_history": nav_hist,
        "ac_nav_history": [], "top_stocks": top_stocks, "top_bonds": [],
        "fees": {}, "period_returns_pct": {},
        "scale_history": [], "holder_structure": [], "asset_allocation": [],
        "stock_position_history": [], "industry_allocation": [],
        "rank_in_similar": None, "buy_redemption_status": None,
        "missing_datasets": missing,
        "note": f"降级快照：净值窗口近{years}年（非全史）；原因={reason}",
    }


# ══════════════════════════════════════════════════════════════════
# V0.1 端点：fund_nav_history（E3 补充端点：区间 + 每日申赎状态）
# ══════════════════════════════════════════════════════════════════

LSJZ_PAGE_SIZE = 20                                   # 坑P2：服务端实际钳制 pageSize=20（实测）


def fund_nav_history(query: str, start: str = "", end: str = "",
                     max_pages: int = 80) -> list[dict]:
    """历史净值逐页（E3 lsjz）。定位为补充端点——E2 已含净值全史，
    本端点独有价值 = ① 按日申赎状态史（SGZT/SHZT）② E2 失效时的净值兜底。

    坑P2 安全用法（2026-11-05 实测钉死）：
      - pageSize 服务端钳制为 20：请求 49/50/100/200 都只回 20 条，500 直接静默 0 条
      - TotalCount 在响应【顶层】不在 Data 里，读错位置=total 恒 0=分页循环第一页就断
      - 每页校验：TotalCount 与实收条数不一致即抛错（防上游静默截断）
    返回 [{date, nav, ac_nav, daily_return_pct, buy_status, redeem_status, dividend}]（新→旧）。
    """
    code = resolve_fund_code(query)
    if not start:                                     # 默认近一年，避免无区间的不稳定行为
        start = (date.today() - timedelta(days=365)).isoformat()
    if not end:
        end = date.today().isoformat()
    if start > end:
        raise ValueError(f"start({start}) 不得晚于 end({end})")
    rows: list[dict] = []
    total = 0
    for page in range(1, max_pages + 1):
        params = {"fundCode": code, "pageIndex": page, "pageSize": LSJZ_PAGE_SIZE,
                  "startDate": start, "endDate": end}
        r = ttfund_get("https://api.fund.eastmoney.com/f10/lsjz", params=params,
                       headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"})
        r.raise_for_status()
        d = r.json()
        if d.get("ErrCode") not in (None, 0):
            raise RuntimeError(f"lsjz 接口返回失败: {d.get('ErrMsg')}")
        lst = (d.get("Data") or {}).get("LSJZList") or []
        total = int(d.get("TotalCount") or 0)          # 🔴 顶层字段，不在 Data 内
        rows.extend({
            "date": x["FSRQ"], "nav": float(x["DWJZ"]), "ac_nav": float(x["LJJZ"]),
            "daily_return_pct": None if x.get("JZZZL") in (None, "") else float(x["JZZZL"]),
            "buy_status": x.get("SGZT"), "redeem_status": x.get("SHZT"),
            "dividend": x.get("FHFCZ") or None,
        } for x in lst if x.get("FSRQ"))
        if not lst or (total > 0 and len(rows) >= total):
            break
    if total and len(rows) != total:
        raise RuntimeError(                            # 坑P2 核心防线
            f"lsjz 分页校验失败：TotalCount={total}，实收 {len(rows)} 条"
            f"（区间 {start}~{end}）。若因 max_pages={max_pages} 用尽，可调大 max_pages "
            f"或缩小区间；否则为上游静默截断，拒绝返回不完整数据。")
    if not total and rows:
        raise RuntimeError(f"lsjz 响应缺 TotalCount 顶层字段但返回了 {len(rows)} 条数据，"
                           f"响应结构异常，拒绝返回未校验的数据。")
    if not rows:
        raise RuntimeError(f"{code} 在 {start}~{end} 无净值记录"
                           f"（基金未成立/已清盘/区间早于成立日/代码有误），拒绝静默返回空。")
    return rows


# ══════════════════════════════════════════════════════════════════
# V0.1 端点：fund_holdings（E4a 季度前十大，可按年份拉历史）
# ══════════════════════════════════════════════════════════════════

def _html_table_rows(html: str) -> list[list[str]]:
    """F10 content 里的 <table> → 行列表（仅依赖标准库，不依赖 lxml）。"""
    body = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S)
    if not body:
        return []
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        cells = [re.sub(r"<[^>]+>|\s+", "", c)
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if cells:
            rows.append(cells)
    return rows


def fund_holdings(query: str, year: Optional[int] = None) -> list[dict]:
    """季度股票持仓前十大（E4a FundArchivesDatas type=jjcc）。

    与 E2 的 stockCodes 互为校验；year 指定时返回该年各季报持仓（历史穿透）。
    ⚠️ 口径：季度报告期前十大重仓（全部持仓明细在年报 PDF，属公告层，不在本 skill 范围）。
    列序（实测 2026-11）：0序号 1代码 2名称 3最新价 4涨跌幅 5资讯 6占净值% 7持股数(万股) 8市值(万元)
    """
    code = resolve_fund_code(query)
    params = {"type": "jjcc", "code": code, "topline": "10",
              "year": str(year) if year else "", "month": ""}
    r = ttfund_get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx", params=params,
                   headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"})
    r.raise_for_status()
    m = re.search(r'content:"(.*)"\s*,\s*arryear', r.text, re.S) or \
        re.search(r'content:"(.*)"\s*}', r.text, re.S)
    if not m:
        raise RuntimeError(f"jjcc 响应无 content（{code}）：{r.text[:120]!r}")
    html = m.group(1)
    # 每个季度一张表；报告期日期在表外 h4（"截止至：<font>2026-09-30</font>"），需按表定位
    out = []
    prev_end = 0
    tables = list(re.finditer(r"<table[^>]*>.*?</table>", html, re.S))
    if not tables:
        raise RuntimeError(f"jjcc 持仓表为空（{code}）——债基/货基可能无股票持仓，"
                           f"请检查 fund_snapshot().top_stocks 或 zqCodes。")
    for t in tables:
        header_zone = html[prev_end:t.start()]
        prev_end = t.end()
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", header_zone)
        period = dates[-1] if dates else None
        # 列序防线：上游调整列序时下标取值会静默错列，先按表头文案定位校验
        thead = re.search(r"<thead[^>]*>(.*?)</thead>", t.group(0), re.S)
        head_txt = re.sub(r"<[^>]+>|\s+", "", thead.group(1)) if thead else ""
        if "占净值" not in head_txt or "持股数" not in head_txt:
            raise RuntimeError(f"jjcc 表头与预期不符（{code}，报告期 {period}）："
                               f"{head_txt[:60]!r}——上游疑似调整列序，拒绝按下标盲取。")
        for cells in _html_table_rows(t.group(0)):
            if len(cells) >= 9 and cells[0].isdigit():
                out.append({"period": period, "seq": int(cells[0]),
                            "stock_code": cells[1], "stock_name": cells[2],
                            "nav_pct": _to_float(cells[6]),
                            "shares_wan": _to_float(cells[7]),
                            "value_wan": _to_float(cells[8])})
    return out


def _to_float(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


# ══════════════════════════════════════════════════════════════════
# V0.1 端点：蛋卷备胎（D1 档案 / 唯一跨域活路）
# ══════════════════════════════════════════════════════════════════

def fund_detail_danjuan(query: str) -> dict:
    """蛋卷基金详情（D1 djapi/fund/{code}）：规模/经理/托管/风险等级/申赎状态。

    定位：东财系全挂时的跨域备胎（不同域名不同风控面）。
    ⚠️ 蛋卷 growth 净值端点已死（坑P5），此处只保档案，无净值能力。
    """
    code = resolve_fund_code(query)
    r = ttfund_get(f"https://danjuanfunds.com/djapi/fund/{code}",
                   headers={"Referer": "https://danjuanfunds.com/"})
    r.raise_for_status()
    d = (r.json() or {}).get("data") or {}
    if not d:
        raise RuntimeError(f"蛋卷详情返回空（{code}）")
    return {
        "code": d.get("fd_code"), "name": d.get("fd_name"), "full_name": d.get("fd_full_name"),
        "type": d.get("fd_type"), "found_date": d.get("found_date"),
        "total_share": d.get("totshare"), "manager": d.get("manager_name"),
        "company": d.get("keeper_name"), "custodian": d.get("trup_name"),
        "risk_level": d.get("risk_level"),
        "subscribe_status": d.get("subscribe_status"), "redeem_status": d.get("withdraw_status"),
        "source": "danjuan",
    }


# ══════════════════════════════════════════════════════════════════
# V0.1 端点：fund_realtime_nav（E6 批量实时净值，N 只 = 1 请求）
# ══════════════════════════════════════════════════════════════════

def fund_realtime_nav(codes: list[str]) -> list[dict]:
    """批量最新净值（E6 fundmobapi FCodes 逗号分隔）。轻量探测（1KB vs E2 的 436KB），
    批量循环场景的省流量通道。⚠️ GSZ 盘中估值字段已恒为 null（官方下线，坑P5 关联）。
    超过 50 只自动分块（接口安全上限）。"""
    cs = [resolve_fund_code(c) for c in codes]
    out = []
    for i in range(0, len(cs), 50):
        chunk = cs[i:i + 50]
        params = {"pageIndex": 1, "pageSize": len(chunk), "plat": "Wap", "product": "EFund",
                  "Version": "6.2.8", "deviceid": "Wap", "FCodes": ",".join(chunk)}
        r = ttfund_get("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFinfo", params=params)
        r.raise_for_status()
        datas = (r.json() or {}).get("Datas") or []
        got = [x.get("FCODE") for x in datas if x.get("FCODE")]
        if len(got) != len(chunk):                     # 个别缺失必须显式报，不许静默漏
            missing = [c for c in chunk if c not in got]
            raise RuntimeError(f"E6 批量净值缺 {missing}（请求 {len(chunk)} 只实得 {len(got)} 只），"
                               f"拒绝静默漏返回。")
        out.extend({"code": x.get("FCODE"), "name": x.get("SHORTNAME"),
                    "nav_date": x.get("PDATE"), "nav": _to_float(x.get("NAV")),
                    "ac_nav": _to_float(x.get("ACCNAV")),
                    "daily_return_pct": _to_float(x.get("NAVCHGRT"))}
                   for x in datas if x.get("FCODE"))
    return out


# ══════════════════════════════════════════════════════════════════
# L5 本地计算层（零网络）
# ══════════════════════════════════════════════════════════════════

def adjusted_nav(nav_records: list[dict]) -> list[dict]:
    """净值序列 → 复权净值（分红再投口径，零网络）。

    主路径（坑P6）：逐日链接官方 equityReturn（daily_return 字段，上游已处理分红/拆分）
      TR_t = TR_{t-1} × (1 + equityReturn_t / 100)
    回退路径（记录无 daily_return 时）：TR_t = TR_{t-1} × (NAV_t + DIV_t) / NAV_{t-1}。
    ⚠️ 直接用单位净值比算收益会漏分红；用 unitMoney 解析分红金额不可靠（金额常被剥离），
    因此凡有官方日收益率就链接它——回退路径仅用于手工构造/极端缺字段场景。
    输入即 fund_snapshot().nav_history；输出首点 = 首日单位净值（元口径，只影响平移不影响比率）。
    """
    if not nav_records:
        raise ValueError("nav_records 为空，无法计算复权净值")
    tr, prev_nav = [], None
    for rec in nav_records:
        nav = float(rec["nav"])
        dr = rec.get("daily_return")
        if prev_nav is None:
            tr.append({"date": rec["date"], "adj_nav": nav})
        elif dr not in (None, ""):                    # 主路径：官方日收益率
            tr.append({"date": rec["date"],
                       "adj_nav": round(tr[-1]["adj_nav"] * (1 + float(dr) / 100), 6)})
        else:                                         # 回退路径：单位净值+分红
            div = float(rec.get("dividend") or 0.0)
            if prev_nav <= 0:
                raise ValueError(f"{rec['date']} 前一日净值为 {prev_nav}，除零")
            tr.append({"date": rec["date"],
                       "adj_nav": round(tr[-1]["adj_nav"] * (nav + div) / prev_nav, 6)})
        prev_nav = nav
    return tr


def _assert_ascending_dates(records: list[dict], ctx: str) -> None:
    """净值序列必须日期升序（E2 是升序；E3 原生降序——喂指标前必须反转，坑P7）。"""
    dates = [r["date"] for r in records]
    if dates != sorted(dates):
        raise ValueError(
            f"{ctx}：净值序列日期非升序（首 {dates[0]} → 末 {dates[-1]}）。"
            f"若数据来自 lsjz(E3) 请先反转——降序序列会让收益/回撤方向全反。")


def fund_metrics(snapshot: dict, start: str = "", end: str = "",
                 risk_free_annual: float = 0.015) -> dict:
    """区间收益/年化/最大回撤/年化波动/夏普/卡玛（复权口径，零网络）。

    对照 ground truth：snapshot.period_returns_pct 是官方同期收益（测试容差 0.1pct）。
    """
    _assert_ascending_dates(snapshot["nav_history"], "fund_metrics")
    adj = adjusted_nav(snapshot["nav_history"])
    if start or end:
        adj = [a for a in adj if (not start or a["date"] >= start)
               and (not end or a["date"] <= end)]
    if len(adj) < 2:
        raise ValueError(f"区间内净值不足 2 个点（{len(adj)}），无法计算指标")
    navs = [a["adj_nav"] for a in adj]
    total_ret = navs[-1] / navs[0] - 1
    days = (datetime.strptime(adj[-1]["date"], "%Y-%m-%d")
            - datetime.strptime(adj[0]["date"], "%Y-%m-%d")).days
    annual_ret = (1 + total_ret) ** (365 / max(days, 1)) - 1 if days > 365 else total_ret

    peak, max_dd, dd_end, dd_peak_date = navs[0], 0.0, None, None
    cur_peak_date = adj[0]["date"]
    for a, n in zip(adj, navs):
        if n > peak:
            peak, cur_peak_date = n, a["date"]
        dd = n / peak - 1
        if dd < max_dd:
            max_dd, dd_end, dd_peak_date = dd, a["date"], cur_peak_date

    daily = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    var = sum((x - sum(daily) / len(daily)) ** 2 for x in daily) / max(len(daily) - 1, 1)
    vol_annual = (var * 252) ** 0.5
    rf_daily = (1 + risk_free_annual) ** (1 / 252) - 1
    sharpe = ((sum(daily) / len(daily) - rf_daily) / (var ** 0.5)) * (252 ** 0.5) if var > 0 else None
    calmar = annual_ret / abs(max_dd) if max_dd < 0 else None
    return {
        "window": {"start": adj[0]["date"], "end": adj[-1]["date"], "days": days, "points": len(adj)},
        "total_return_pct": round(total_ret * 100, 4),
        "annualized_return_pct": round(annual_ret * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "max_drawdown_window": {"peak": dd_peak_date, "trough": dd_end},
        "annualized_volatility_pct": round(vol_annual * 100, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
    }


def fund_dca_backtest(snapshot: dict, amount: float = 1000.0, freq: str = "monthly",
                      start: str = "", end: str = "") -> dict:
    """定投回测（真实复权净值口径，零网络）。

    约定：扣款日=每月（周）首个有净值的交易日，按当日复权价买入；
    期末市值=Σ(amount/adj_t)×adj_end。单位：元（复权序列以首日单位净值起算）。
    freq: monthly | weekly。
    """
    if freq not in ("monthly", "weekly"):
        raise ValueError(f"freq 只支持 monthly/weekly，收到 {freq!r}")
    _assert_ascending_dates(snapshot["nav_history"], "fund_dca_backtest")
    adj = adjusted_nav(snapshot["nav_history"])
    if start or end:
        adj = [a for a in adj if (not start or a["date"] >= start)
               and (not end or a["date"] <= end)]
    if len(adj) < 2:
        raise ValueError("区间内净值不足 2 个点，无法回测")
    key_len = 7 if freq == "monthly" else 10
    seen, invest_dates = set(), []
    for a in adj:                                      # 每期首个交易日 = 该期第一条净值
        k = a["date"][:key_len]
        if k not in seen:
            seen.add(k)
            invest_dates.append(a)
    units = sum(amount / a["adj_nav"] for a in invest_dates)
    invested = amount * len(invest_dates)
    final_value = units * adj[-1]["adj_nav"]
    profit = final_value - invested
    years = max((datetime.strptime(adj[-1]["date"], "%Y-%m-%d")
                 - datetime.strptime(adj[0]["date"], "%Y-%m-%d")).days / 365, 1e-9)
    return {
        "freq": freq, "amount_per_period": amount, "periods": len(invest_dates),
        "first_invest": invest_dates[0]["date"], "last_invest": invest_dates[-1]["date"],
        "total_invested": round(invested, 2), "final_value": round(final_value, 2),
        "profit": round(profit, 2),
        "return_pct": round(profit / invested * 100, 4),
        # ⚠️ 朴素年化=总倍数按全窗口年化，忽略资金分期投入，长周期显著高估，仅供参考
        "naive_annualized_pct": round(((final_value / invested) ** (1 / years) - 1) * 100, 4),
        "irr_xirr": None,                             # V0.2 补精确 XIRR（分期投入真口径）
    }


def holding_map(snapshot: dict, topn: int = 10) -> dict:
    """持仓穿透桥（零网络）：把重仓股代码整理成可直接喂 a-stock-data 的形态。

    不直接调用 a-stock-data（避免硬依赖），返回调用说明 + 代码清单。
    """
    codes = [c for c in snapshot.get("top_stocks", []) if c][:topn]
    if not codes:
        raise ValueError(f"{snapshot.get('name')} 无股票持仓代码"
                         f"（债基/货基或 missing_datasets 含 stockCodes），无从穿透")
    return {
        "fund": snapshot.get("name"), "stock_codes": codes,
        "usage": ("以下 a-stock-data 端点可直接消费这些代码：\n"
                  "  tencent_quote(codes)               # 实时价/PE/PB/市值（腾讯通道不封IP）\n"
                  "  eastmoney_concept_blocks(code)     # 个股概念板块归属\n"
                  "  baostock_valuation_history(code,s,e) # 估值历史分位\n"
                  "  §6.7 sw_industry_as_of(...)        # 申万行业归属（历史口径）"),
        "note": "股票代码属股票域，穿透分析请加载 a-stock-data skill 后调用其端点",
    }
