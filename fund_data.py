# -*- coding: utf-8 -*-
"""
fund-data V0.3.0 — 基金底层数据工具包（基金主源 + 蛋卷备胎 + 本地计算 + 跨域穿透 + 宏观利率）

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
  P8  中债 searchYc POST 是参数黑洞（实测恒返回 []），downYearBzqx GET 直出全年
      xlsx——只用下载端点，不碰查询端点；中债请求必须带 Referer
  P9  中债 xlsx 的单元格是 inlineStr（无 sharedStrings），zipfile+regex 可解，
      无需 pandas/openpyxl——行结构为 (日期, 期限码, 收益率) 三元组
  P10 东财 datacenter 报表名必须从页面 bundle 逆向（臆测名全 9501）：
      国债日频=RPTA_WEB_TREASURYYIELD / SHIBOR=RPT_IMP_INTRESTRATEN(filter CNY)
      / 基准利率调整=RPT_ECONOMY_DEPOSIT_RATE；LPR 独立报表不存在(9501)
  P11 中债 TLS 指纹墙：requests/urllib 稳定 SSL EOF，curl 稳定 200（A/B 实测
      2026-11-06）——中债通道用 subprocess curl（_cb_request），仍计入限流与审计
"""
import html as _html
import io
import json
import math
import os
import re
import time
import random
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

try:  # pandas 可选：仅 fund_metrics 的 DataFrame 输出用到，缺了降级为 list[dict]
    import pandas as pd
except ImportError:
    pd = None

__version__ = "0.4.0"

# ══════════════════════════════════════════════════════════════════
# L1 前置层：限流入口 / 缓存目录 / 请求计数
# ══════════════════════════════════════════════════════════════════

FUND_CACHE_DIR = Path.home() / ".fund_data_cache"
SNAPSHOT_TTL_HOURS = 20          # 距上次抓取 20h 失效（滚动窗口，非自然日切分）
FUND_LIST_TTL_DAYS = 30
_BJ_TZ = timezone(timedelta(hours=8))   # E2 时间戳按北京时区解析，非东八区机器不漂移

TT_MIN_INTERVAL = 1.5            # 东财/天天系最小请求间隔（秒）；批量场景建议调 2~3
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 坑P12：本 skill 全部端点是国内金融数据站——macOS 系统代理（VPN/Clash，requests 的
# trust_env 会读 scproxy）转发这些域名只会引入抖动（实测 SSL EOF 间歇失败，curl 直连则稳）。
# 故两条 Session 一律直连（不读系统代理/环境代理）。
TT_SESSION = requests.Session()
TT_SESSION.trust_env = False                          # 直连：绕过系统代理（坑P12）
TT_SESSION.headers.update({"User-Agent": UA})
TT_REQUEST_COUNT = [0, {}]       # [总请求数, {host: 次数}] — 预算审计用，ttfund_get 自增

# ── F1：跨进程节流锁（fcntl + 磁盘时间戳）─────────────────────────────
# 原实现的时间戳在进程内存里——licai-map 服务、AI 会话、手跑脚本各持一份限流器，
# 合并速率 = 进程数 × 0.67req/s，双进程批量即可越 300req/5min 封禁线（审计 F1）。
# 现改为磁盘时间戳 + 文件锁：同机所有进程强制串行，锁等待自动吸收间隔。
try:
    import fcntl  # macOS/Linux；Windows 缺此模块时降级为进程内节流
except ImportError:
    fcntl = None

_CBREAK_COOLDOWN = 600           # F2：熔断冷却 10 分钟
_CBREAK_TRIP = 3                 # 连续 3 次风控信号触发熔断
_cbreak_state = {"tt": [0, 0.0], "em": [0, 0.0]}   # [连击数, 上次信号时刻]；>5min 衰减归一


class RiskControlError(RuntimeError):
    """403/429/风控页信号——与网络故障严格区分：绝不触发降级风暴（审计 F6），
    直接上抛并计入熔断。它不是 requests.RequestException 子类，
    fund_snapshot 的 except RequestException 不会误捕它。"""


def _throttle_file(line: str) -> Path:
    return FUND_CACHE_DIR / f".throttle_{line}"


def _cbreak_file(line: str) -> Path:
    return FUND_CACHE_DIR / f".cbreak_{line}"


def _cross_proc_throttle(line: str, min_interval: float):
    """F1 跨进程节流：flock 抢锁 → 读磁盘时间戳 → 睡足间隔 → 写回 → 放锁。

    时间戳语义=放行时刻（非请求完成时刻）：慢响应下相邻请求**发起**间隔恒 ≥interval
    （V0.3.0 是间隔+响应耗时，本版略松但仍在设计线内，见 ttfund_get docstring）。
    """
    if fcntl is None:                                 # Windows 降级：进程内保守节流
        time.sleep(min_interval * random.uniform(0.8, 1.2))
        return
    lock_path = _throttle_file(line)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        open(lock_path, "a").close()                   # O_CREAT 原子首建，绝不截断已有内容
        with open(lock_path, "r+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                last = float(f.read().strip() or 0)
            except ValueError:
                last = 0.0
            now = time.time()                          # 拿锁后再取样（排队时长不计入间隔）
            wait = min_interval - (now - last)
            if wait > 0:
                time.sleep(wait + random.uniform(0.05, 0.3))
            f.seek(0)
            f.truncate()
            f.write(f"{time.time():.6f}")
    except OSError:
        # 锁文件异常 → 降级进程内保守节流（保持可用性优先）
        time.sleep(min_interval * random.uniform(0.8, 1.2))


def _cbreak_check(line: str):
    """F2：熔断检查。冷却期内所有请求直接拒绝（不敲上游），报错给明确解除时间。"""
    f = _cbreak_file(line)
    if f.exists():
        try:
            until = float(f.read_text().strip() or 0)
        except ValueError:
            until = 0.0
        if until > time.time():
            remain = int(until - time.time())
            raise RuntimeError(
                f"东财[{line}] 熔断中（疑似触发风控已自动停机），冷却剩 {remain}s 至 "
                f"{datetime.fromtimestamp(until).strftime('%H:%M:%S')}。"
                f"这是保护机制——继续请求只会延长封禁；稍后再试或换网络。")
        f.unlink(missing_ok=True)                     # 冷却期满自动复位
        _cbreak_state[line] = [0, 0.0]


def _cbreak_signal(line: str, why: str):
    """F2：记录一次风控信号。连续 _CBREAK_TRIP 次（5 分钟窗口内）→ 落盘熔断并抛错。"""
    cnt, last_ts = _cbreak_state.get(line, [0, 0.0])
    cnt = 1 if (time.time() - last_ts) > 300 else cnt + 1   # 跨 5min 的孤立信号重新计
    _cbreak_state[line] = [cnt, time.time()]
    if cnt >= _CBREAK_TRIP:
        until = time.time() + _CBREAK_COOLDOWN
        try:
            _cbreak_file(line).parent.mkdir(parents=True, exist_ok=True)
            _cbreak_file(line).write_text(f"{until:.0f}")
        except OSError:
            pass
        _cbreak_state[line] = [0, 0.0]
        raise RiskControlError(
            f"东财[{line}] 连续 {_CBREAK_TRIP} 次风控信号（最近: {why}），已自动熔断 "
            f"{_CBREAK_COOLDOWN // 60} 分钟至 {datetime.fromtimestamp(until).strftime('%H:%M:%S')}。"
            f"熔断期内所有东财请求直接拒绝（保护 IP）。")


def assert_not_risk_control(resp, line: str = "tt", context: str = ""):
    """F6 防线：调用方 raise_for_status **之前**先过这一道——403/429 转 RiskControlError。

    用法：r = ttfund_get(...); assert_not_risk_control(r, "tt", "E2"); r.raise_for_status()
    RiskControlError 不是 RequestException 子类 → fund_snapshot 的降级 except 不会误捕
    → 杜绝"403 被当网络故障 → 降级 → 37 页 lsjz 风暴"（审计 F6 的恶性链）。
    """
    if resp.status_code in (403, 429):
        _cbreak_signal(line, f"HTTP {resp.status_code} {context}")
        raise RiskControlError(
            f"HTTP {resp.status_code}（{context}）：疑似东财风控信号，"
            f"已计入熔断计数。本错误不触发降级（防请求风暴），请降频稍后再试。")
    _cbreak_state[line] = [0, 0.0]   # 成功响应清零连击（孤立 403 不跨时间累积误熔断）


def ttfund_get(url: str, params: Optional[dict] = None,
               headers: Optional[dict] = None, timeout: int = 20, **kwargs):
    """天天/东财/蛋卷统一请求入口：跨进程串行限流(F1) + 熔断(F2) + 强制 HTTPS（坑P1）
    + 会话复用 + 瞬态重试1次（仅网络异常；HTTP 状态码在调用方经 assert_not_risk_control
    分类——403/429 是风控信号，不重试、不降级、计熔断，见 F6）。
    """
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    for attempt in (1, 2):
        _cbreak_check("tt")                             # F2：冷却期内直接拒绝
        _cross_proc_throttle("tt", TT_MIN_INTERVAL)     # F1：跨进程节流（锁内睡间隔）
        try:
            TT_REQUEST_COUNT[0] += 1
            host = re.sub(r"^https?://", "", url).split("/")[0]
            TT_REQUEST_COUNT[1][host] = TT_REQUEST_COUNT[1].get(host, 0) + 1
            return TT_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2.0)


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


def _cache_write(rel: str, payload: "dict | list") -> None:
    """原子写（tmp+rename）：进程中断不留半截文件（G2；损坏兜底仍在 _cache_read）。"""
    p = FUND_CACHE_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


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
    assert_not_risk_control(r, "tt", "E1 清单")
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
    """E2 echarts 表格 → [dict,...]。两种系列形态分流（坑P18，2026-11-09 实测）：

    形态一（列式，持有人结构等）：{'categories':[...], 'series':[{name, data}...]}
      → [{_date, <系列名>: 值}, ...]
    形态二（行式，fluctuationScale 规模等）：{'categories':[...], 'series':[{y, mom}, ...]}
      series[i] 与 categories[i] 逐期对应、无 name/data 列键——旧实现只认形态一，
      行式值全丢（规模史只剩 _date 光杆行）。
    """
    if not isinstance(obj, dict):
        return []
    cats = obj.get("categories") or []
    series = [s for s in (obj.get("series") or []) if isinstance(s, dict)]
    if not cats or not series:
        return []
    if all(s.get("data") is None for s in series):     # 形态二：行式 {y, mom}
        recs = []
        for i, c in enumerate(cats):
            if i >= len(series):
                break
            row = {"_date": str(c)}
            row.update({k: v for k, v in series[i].items()})
            recs.append(row)
        return recs
    cols = {}                                          # 形态一：列式 {name, data}
    for s in series:
        if s.get("data") is not None:
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


def _ms_to_date(ms) -> str:
    """E2 毫秒时间戳 → YYYY-MM-DD（北京时区，与 snapshot 净值口径一致）。"""
    return datetime.fromtimestamp(int(ms) / 1000, _BJ_TZ).strftime("%Y-%m-%d")


def _xy_pairs_to_records(raw, value_key: str = "value") -> list[dict]:
    """E2 时序对 [[ts_ms, v], ...] → [{"date", value_key}]（坑P18 关联修复）。

    fundSharesPositions（仓位）/ rateInSimilarPersent（同类百分位）/ grandTotal.data
    都是这个形态——旧路径走 _pair_rows_to_dicts 会把首对当表头行，产出
    {"时间戳串": 时间戳} 乱码（2026-11-09 实测修复）。
    任一行不是二元可数值化对 → 整体判非此形态返回 []（宁缺毋错，交回表格分流）。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return []
        try:
            out.append({"date": _ms_to_date(p[0]), value_key: float(p[1])})
        except (TypeError, ValueError, OverflowError):
            return []
    return out


def _scale_history(raw) -> list[dict]:
    """Data_fluctuationScale（行式 {y: 规模亿, mom: 环比}）→ [{_date, scale_yi, mom_pct}]。

    兼容旧列式（系列名"规模"）以防上游回切。评测skill C7 的兜底路径由此修复为正路。
    """
    recs = _normalize_table(raw)
    out = []
    for r in recs:
        v = r.get("scale_yi", r.get("y", r.get("规模")))
        out.append({"_date": r.get("_date"),
                    "scale_yi": float(v) if isinstance(v, (int, float)) else None,
                    "mom_pct": r.get("mom")})
    return out


def _benchmark_curves(raw) -> list[dict]:
    """Data_grandTotal → [{"name", "points": [{"date", "return_pct"}]}]（V0.4.0 B1）。

    系列实测为 [基金简称, "同类平均", "沪深300"] 三条；窗口约近半年（126 交易日）
    非全史；值 = 窗口起点以来累计收益%（起点 0）。旧版只把它挂在 _PZ_VARS_KNOWN
    防缺失清单里从未输出。
    """
    out = []
    if not isinstance(raw, list):
        return out
    for s in raw:
        if not isinstance(s, dict):
            continue
        pts = _xy_pairs_to_records(s.get("data"), "return_pct")
        if pts:
            out.append({"name": str(s.get("name", "")), "points": pts})
    return out


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
        assert_not_risk_control(r, "tt", "E2 快照")
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
        "is_money_fund": bool(v.get("ishb")),          # 仅类型标注：无货基专项口径（坑P15）
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
        "scale_history": _scale_history(v.get("Data_fluctuationScale")),
        "holder_structure": _normalize_table(v.get("Data_holderStructure")),
        "asset_allocation": _normalize_table(v.get("Data_assetAllocation")),
        "stock_position_history": _xy_pairs_to_records(
            v.get("Data_fundSharesPositions"), "position_pct"),
        "industry_allocation": _normalize_table(v.get("Data_industryAllocation")),
        "peer_rank_history": _xy_pairs_to_records(
            v.get("Data_rateInSimilarPersent"), "percentile"),
        "benchmark_curves": _benchmark_curves(v.get("Data_grandTotal")),
        "buy_redemption_status": v.get("Data_buySedemption"),
        "missing_datasets": missing,                   # 坑P3：显式列出，调用方知情
    }
    _cache_write(rel, snap)
    return snap


def _snapshot_degraded(code: str, reason: str = "", years: float = 1.0) -> dict:
    """E2 失效时的分拆降级：E3 净值(近N年) + E4a 持仓 + 蛋卷 D1 档案。

    ⚠️ 降级快照不写缓存（下次调用会重试 E2 主端点）；数据集缺口显式列入 missing；
    净值窗口默认近 1 年（F3：原 3 年=37 页连发，批量场景会形成请求风暴；
    需要更长历史可显式传 years，单基金场景代价可接受）。
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
        "peer_rank_history": [], "benchmark_curves": [],
        "buy_redemption_status": None,
        "missing_datasets": missing,
        "note": f"降级快照：净值窗口近{years}年（非全史）；原因={reason}",
        "window_years": years,
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
        assert_not_risk_control(r, "tt", "E3 lsjz")
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
    rel = f"holdings/{code}_{year or 'latest'}.json"      # F4：季报数据当日缓存（原每次现查）
    cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
    if cached is not None:
        return cached
    params = {"type": "jjcc", "code": code, "topline": "10",
              "year": str(year) if year else "", "month": ""}
    r = ttfund_get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx", params=params,
                   headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"})
    assert_not_risk_control(r, "tt", "E4a jjcc")
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
    _cache_write(rel, out)
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
        assert_not_risk_control(r, "tt", "E6 批量净值")
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
    fee = None                                         # 管理费率（费后净值已含费用，仅标注）
    try:
        fee = float(snapshot.get("fees", {}).get("current_rate"))
    except (TypeError, ValueError):
        pass
    return {
        "window": {"start": adj[0]["date"], "end": adj[-1]["date"], "days": days, "points": len(adj)},
        "total_return_pct": round(total_ret * 100, 4),
        "annualized_return_pct": round(annual_ret * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "max_drawdown_window": {"peak": dd_peak_date, "trough": dd_end},
        "annualized_volatility_pct": round(vol_annual * 100, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
        "mgmt_fee_pct": fee,                           # None=档案缺失；sharpe 分母未扣费
    }


def _xirr(cashflows: list, max_rate: float = 10.0) -> Optional[float]:
    """分期现金流年化 IRR（二分法，A1）。cashflows: [(date_str, amount)]，投入负/期末市值正。

    无符号变化（如亏损到市值≈0）或多解无根时返回 None 绝不硬造；
    NPV(r)=Σ a_i/(1+r)^{t_i 年}，对 r 单调递减，二分稳定收敛。
    """
    if len(cashflows) < 2:
        return None
    t0 = datetime.strptime(cashflows[0][0], "%Y-%m-%d")
    ts = [(datetime.strptime(d, "%Y-%m-%d") - t0).days / 365.0 for d, _ in cashflows]

    def npv(r: float) -> float:
        return sum(a / (1.0 + r) ** t for (_, a), t in zip(cashflows, ts))

    lo, hi = -0.9999, max_rate
    try:
        f_lo, f_hi = npv(lo), npv(hi)
    except (OverflowError, ZeroDivisionError):
        return None
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)) or f_lo * f_hi > 0:
        return None
    mid = (lo + hi) / 2.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6 or hi - lo < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid


def fund_dca_backtest(snapshot: dict, amount: float = 1000.0, freq: str = "monthly",
                      start: str = "", end: str = "") -> dict:
    """定投回测（真实复权净值口径，零网络）。

    约定：扣款日=每月（周）首个有净值的交易日，按当日复权价买入；
    期末市值=Σ(amount/adj_t)×adj_end。单位：元（复权序列以首日单位净值起算）。
    freq: monthly | weekly。
    年化双口径（V0.4.0 A1）：naive_annualized_pct（总倍数全窗口年化，忽略分期，
    方向性偏差取决于路径，仅供参考）+ irr_xirr_pct（XIRR 分期投入真年化）。
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
    cfs = [(a["date"], -float(amount)) for a in invest_dates]
    cfs.append((adj[-1]["date"], float(final_value)))
    xirr = _xirr(cfs)
    return {
        "freq": freq, "amount_per_period": amount, "periods": len(invest_dates),
        "first_invest": invest_dates[0]["date"], "last_invest": invest_dates[-1]["date"],
        "total_invested": round(invested, 2), "final_value": round(final_value, 2),
        "profit": round(profit, 2),
        "return_pct": round(profit / invested * 100, 4),
        # ⚠️ 朴素年化=总倍数按全窗口年化，忽略资金分期投入；偏差方向取决于路径（先涨后跌
        # 偏高、稳步上行偏低），仅供参考——分期投入真年化看 irr_xirr_pct
        "naive_annualized_pct": round(((final_value / invested) ** (1 / years) - 1) * 100, 4),
        "irr_xirr_pct": round(xirr * 100, 4) if xirr is not None else None,
        "xirr_note": (None if xirr is not None else
                      "XIRR 无解（期末市值≈0 全亏或窗口过短），分期真年化不适用"),
    }


def position_trend(snapshot: dict, recent: int = 4) -> dict:
    """股票仓位史规则解读（零网络，V0.4.0 B2）。输入 snapshot.stock_position_history。

    recent: 头尾各取 recent 期均值对比，捕捉加减仓（>5pp 判定）。
    """
    hist = [h for h in (snapshot.get("stock_position_history") or [])
            if isinstance(h.get("position_pct"), (int, float))]
    if len(hist) < 2:
        raise ValueError("仓位史不足 2 期（E2 Data_fundSharesPositions 缺失或基金过新），拒绝解读")
    vals = [h["position_pct"] for h in hist]
    latest = vals[-1]
    k = max(1, min(recent, len(vals) // 2))
    head = sum(vals[:k]) / k
    tail = sum(vals[-k:]) / k
    label = ("仓位过低（股基/指增常态 80~95%，需核对基金类型或在建仓期）" if latest < 60 else
             "贴上限运行（≥95%）" if latest > 95 else "仓位正常区间")
    if tail - head > 5:
        label += "；近几期加仓"
    elif head - tail > 5:
        label += "；近几期减仓"
    return {"latest_pct": round(latest, 2), "min_pct": round(min(vals), 2),
            "max_pct": round(max(vals), 2), "mean_pct": round(sum(vals) / len(vals), 2),
            "periods": len(vals), "first_date": hist[0]["date"], "last_date": hist[-1]["date"],
            "label": label}


def peer_rank(snapshot: dict, lookback_days: int = 365) -> dict:
    """同类百分位解读（零网络，V0.4.0 B2）。输入 snapshot.peer_rank_history（日频）。

    值 = 超越同类百分比（高=排名靠前）。输出最新值 + lookback 天前对照与变化。
    """
    hist = [h for h in (snapshot.get("peer_rank_history") or [])
            if isinstance(h.get("percentile"), (int, float))]
    if not hist:
        raise ValueError("同类排名史缺失（E2 Data_rateInSimilarPersent），拒绝解读")
    latest = hist[-1]
    cutoff = (datetime.fromisoformat(latest["date"]) - timedelta(days=lookback_days)).isoformat()
    past = [h for h in hist if h["date"] <= cutoff]
    year_ago = past[-1] if past else None
    vals = [h["percentile"] for h in hist]
    label = ("同类前列（≥80 分位）" if latest["percentile"] >= 80 else
             "同类落后（≤30 分位）" if latest["percentile"] <= 30 else "同类中游")
    return {"latest_date": latest["date"], "latest_percentile": latest["percentile"],
            "year_ago_percentile": year_ago["percentile"] if year_ago else None,
            "delta_pp": (round(latest["percentile"] - year_ago["percentile"], 2)
                         if year_ago else None),
            "min_percentile": min(vals), "max_percentile": max(vals),
            "points": len(vals), "label": label}


def relative_performance(snapshot: dict, benchmark: str = "沪深300",
                         min_overlap: int = 60) -> dict:
    """基金 vs E2 内置基准曲线的区间相对表现（零网络，V0.4.0 B3）。

    基准来自 snapshot.benchmark_curves（Data_grandTotal，窗口约近半年）：值为窗口
    起点以来累计收益%。基金侧用复权净值限制到基准窗口对齐后：
    区间收益（基金/基准/相对差）+ 日频 β（基金日收益对基准日收益 OLS）+ 相关系数。
    benchmark 可传任意现存曲线名（"沪深300"/"同类平均"/基金简称）。
    """
    curves = {c["name"]: c for c in (snapshot.get("benchmark_curves") or [])}
    if benchmark not in curves:
        raise ValueError(f"基准曲线 {benchmark!r} 不存在；可用: {sorted(curves) or '（snapshot 无 benchmark_curves——E2 缺失或缓存为旧版本，force_refresh 重拉）'}")
    pts = curves[benchmark]["points"]
    if len(pts) < 2:
        raise ValueError(f"基准曲线 {benchmark!r} 点数不足")
    b_by_date = {p["date"]: p["return_pct"] for p in pts}
    dates = sorted(b_by_date)
    b_ret = {}
    for i in range(1, len(dates)):
        c0, c1 = b_by_date[dates[i - 1]], b_by_date[dates[i]]
        b_ret[dates[i]] = (100.0 + c1) / (100.0 + c0) - 1.0
    _assert_ascending_dates(snapshot["nav_history"], "relative_performance")
    adj = adjusted_nav(snapshot["nav_history"])
    f_ret = {adj[i]["date"]: adj[i]["adj_nav"] / adj[i - 1]["adj_nav"] - 1.0
             for i in range(1, len(adj))}
    common = sorted(set(b_ret) & set(f_ret))
    if len(common) < min_overlap:
        raise ValueError(f"与基准曲线重叠仅 {len(common)} 个交易日（要求≥{min_overlap}；"
                         f"基准窗口仅约半年，新基金/区间错位可能不足）")
    xs = [b_ret[d] for d in common]
    ys = [f_ret[d] for d in common]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    beta = sxy / sxx if sxx > 1e-18 else None
    corr = sxy / math.sqrt(sxx * syy) if sxx > 1e-18 and syy > 1e-18 else None
    win = [a for a in adj if dates[0] <= a["date"] <= dates[-1]]
    fund_ret_pct = (win[-1]["adj_nav"] / win[0]["adj_nav"] - 1.0) * 100.0
    bench_ret_pct = b_by_date[dates[-1]]
    return {"benchmark": benchmark,
            "window": {"start": dates[0], "end": dates[-1], "days": n},
            "fund_return_pct": round(fund_ret_pct, 2),
            "benchmark_return_pct": round(bench_ret_pct, 2),
            "relative_return_pct": round(fund_ret_pct - bench_ret_pct, 2),
            "beta": round(beta, 3) if beta is not None else None,
            "correlation": round(corr, 3) if corr is not None else None}


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
        "note": "零网络桥接视图；一键穿透（含行情+板块+聚合）直接用本层 penetrate()",
    }


# ══════════════════════════════════════════════════════════════════
# L6 跨域穿透层：重仓股 → 股票域行情/板块（内嵌 a-stock-data 通道，V0.2.0）
# ══════════════════════════════════════════════════════════════════
# 通道来源与防封纪律均沿用 a-stock-data：腾讯批量行情（不封IP，1请求）+
# 东财 push2 slist 板块归属（1请求/股，当日磁盘缓存）。push2 走独立限流线
# EM_MIN_INTERVAL（与 ttfund_get 分管不同子域，峰值合计 ≈1.7 req/s，远低于风控阈值）。

EM_MIN_INTERVAL = 1.0
EM_SESSION = requests.Session()
EM_SESSION.trust_env = False                          # 直连：绕过系统代理（坑P12）
EM_SESSION.headers.update({"User-Agent": UA})
EM_REQUEST_COUNT = [0]                              # 股票域东财请求计数（审计用）
TENCENT_REQUEST_COUNT = [0]                         # 腾讯行情请求计数（审计用；腾讯不封不设限流）


def em_get(url: str, params: Optional[dict] = None,
           headers: Optional[dict] = None, timeout: int = 15, **kwargs):
    """股票域东财统一入口（push2 系）：跨进程串行限流(F1) + 熔断(F2) + 会话复用。

    与 ttfund_get 分管不同子域（各自独立的节流线/熔断线），但同属东财风控面——
    em 线触发熔断时建议同时降低 tt 线频率（风控按 IP 不按子域精确区分）。
    """
    for attempt in (1, 2):                            # 瞬态抖动重试一次（与 ttfund_get 对齐）
        _cbreak_check("em")
        _cross_proc_throttle("em", EM_MIN_INTERVAL)
        try:
            EM_REQUEST_COUNT[0] += 1
            return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2.0)


_SH_INDEX_Q = {"000300", "000905", "000016", "000688", "000852", "000010"}


def _tencent_prefix(code: str) -> str:
    """6 位代码 → 腾讯行情前缀（sh/sz/bj）。

    ⚠️ 92 北交所号段必须先于 9x 判断（920982→bj 而非 sh），错路由=腾讯静默返空。
    ⚠️ 204xxx 沪市质押式回购（GC001~GC182，repo_rate_gc 依赖）必须走 sh，误判深市=返空。
    """
    c = str(code).lower().strip()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith("92") or c[:1] in ("4", "8"):
        return f"bj{c}"
    if c.startswith("204"):                            # 沪市回购 GC 族（204007=GC007）
        return f"sh{c}"
    if c in _SH_INDEX_Q or c[:1] in ("5", "6", "9"):
        return f"sh{c}"
    return f"sz{c}"


def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯批量实时行情（通道内嵌自 a-stock-data §1.2）：价/涨跌/换手/PE/PB/市值。

    腾讯通道实测不封 IP，N 只 = 1 请求，是穿透层的行情主力。
    返回 {纯6位代码: {...}}；非交易时段返回最后成交快照。
    """
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(_tencent_prefix(c) for c in codes)
    data = None
    for attempt in (1, 2):                            # 瞬态 SSL EOF/连接重试一次（实测网络抖动）
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        TENCENT_REQUEST_COUNT[0] += 1                # 审计账本：腾讯线与东财线并列可查
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            data = opener.open(req, timeout=10).read().decode("gbk")   # 直连（坑P12）
            break
        except OSError:                            # 含 URLError（其子类）
            if attempt == 2:
                raise
            time.sleep(1.0)
    result = {}
    for line in data.strip().split(";"):
        if "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        v = line.split('"')[1].split("~")
        if len(v) < 53:
            continue

        def f(i):
            return float(v[i]) if v[i] else 0.0

        result[key[2:]] = {
            "name": v[1], "price": f(3), "last_close": f(4), "change_pct": f(32),
            "turnover_pct": f(38), "pe_ttm": f(39),
            "float_mcap_yi": f(44), "mcap_yi": f(45), "pb": f(46),
        }
    return result


def em_market_code(code: str) -> int:
    """东财 secid 市场号：沪=1，深/北=0（规则照抄 a-stock-data，勿用 startswith('6')）。

    ⚠️ 92 北交所现行号段先于 9x 判断（V3.7.0 实测 920982 → m=0，与深市共用 0）。
    """
    c = str(code).lower()
    if c.startswith("92") or c[:1] in ("4", "8"):     # 北交所：920 现行 + 4x/8x 老段
        return 0
    if c.startswith("sh") or c[:1] in ("5", "6", "9"):
        return 1
    return 0


def concept_blocks(stock_code: str, use_cache: bool = True) -> list[str]:
    """个股板块标签（通道内嵌自 a-stock-data §3.3 东财 slist，一次请求拿全）。

    板块混合 行业/概念/地域（'白酒Ⅲ'/'酿酒概念'/'贵州板块'），名称自解释；
    当日磁盘缓存 penetration/{code}.json（存原始标签，清洗规则演进不使缓存失效）。
    """
    if not re.fullmatch(r"\d{6}", str(stock_code)):
        raise ValueError(f"股票代码格式错误: {stock_code!r}（需 6 位数字，防异常上游数据拼入缓存路径）")
    rel = f"penetration/{stock_code}.json"
    if use_cache:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    params = {"fltt": "2", "invt": "2",
              "secid": f"{em_market_code(stock_code)}.{stock_code}",
              "spt": "3", "pi": "0", "pz": "200", "po": "1",
              "fields": "f12,f14,f3,f128"}
    r = em_get("https://push2.eastmoney.com/api/qt/slist/get", params=params,
               headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
    assert_not_risk_control(r, "em", "push2 slist")
    d = r.json()
    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    tags = [it.get("f14", "") for it in items if it.get("f14")]
    if not tags:
        raise RuntimeError(f"{stock_code} 板块归属为空（secid 路由或风控），拒绝静默空")
    _cache_write(rel, tags)
    return tags


# 噪声停用词：指数成分/交易机制/地域板块——不是"这只基金在押注什么"的信号。
# ⚠️ 取舍边界：国企改革/央企/一带一路 对多数基金是壳噪声，但对相应主题基金
# 恰是真实主题——本表按"通用穿透"取舍（宁滤勿噪），主题基金专用分析请自行调整。
_TAG_STOP = re.compile(
    r"概念|板块|融资融券|转融券|沪股通|深股通|MSCI|标普|标准普尔|富时|罗素|道琼斯"
    r"|上证|深证|沪深|中证|国证|创业板|科创板|北交所|HS\d|SH\d|SZ\d|央视|QFII|RQFII"
    r"|同花顺|东财|基金重仓|机构重仓|预盈预增|预亏预减|股权激励|回购|增持|壳资源"
    r"|次新股|高送转|含可转债|B转H|AB股|GDR|老字号|国企改革|央企|国资|PPP|一带一路")
_SUFFIX_ROMAN = re.compile(r"[ⅠⅡⅢⅣⅤ]$|I+$")


def _clean_tag(tag: str):
    """板块名 → 主题词；噪声返回 None。'白酒Ⅲ'→'白酒'，'计算机设备II'→'计算机设备'。

    后缀剥离后长度 <2 的产物（如 'AI' 被 I+$ 剥成 'A'）也返回 None——单字符不构成主题词。
    """
    t = tag.strip()
    if not t or len(t) < 2 or _TAG_STOP.search(t):
        return None
    cleaned = _SUFFIX_ROMAN.sub("", t).strip()
    return cleaned if len(cleaned) >= 2 else None


def penetrate(query: str, topn: int = 10) -> dict:
    """重仓穿透主入口（R9 场景 04）：基金 → 重仓股权重 → 股票域行情+板块 → 白话地图。

    请求预算：1(持仓 jjcc，每次现查——fund_holdings 无缓存) + 1(腾讯批量) + N(板块，当日缓存)
    → 冷启动最多 ≈14 请求（含 snapshot/fund_list 缓存 miss）；当日重跑 = 2 请求（jjcc+腾讯）。
    单股失败不中断整体：跳过该股的主题聚合，记入 failed_stocks（含 reason 与权重）。
    """
    hold = fund_holdings(query)
    if not hold:
        raise RuntimeError(f"{query} 无股票持仓记录（债基/货基？）——"
                           f"股票穿透需要股票重仓；债券穿透请用 fund_snapshot().top_bonds")
    latest = max((h["period"] or "" for h in hold))    # 防个别行 period=None 崩 max；按日期取最新
    pool = [h for h in hold if h["period"] == latest][:topn]

    failed_stocks: list[dict] = []                     # per-stock 降级名单（含权重）
    picks = []
    for h in pool:
        if h.get("nav_pct") is None:                   # 权重解析失败（None 防 TypeError）
            failed_stocks.append({"code": h["stock_code"], "name": h["stock_name"],
                                  "weight": None, "reason": "占净值比缺失"})
            continue
        picks.append(h)
    if not picks:
        raise RuntimeError(f"{query} 最新报告期 {latest} 全部重仓股权重缺失，无法穿透")
    weight_sum = sum(h["nav_pct"] for h in picks)

    quotes = tencent_quote([h["stock_code"] for h in picks])   # 股票域行情（1 请求）

    theme = defaultdict(float)                         # 主题词 → 累计净值权重
    for h in picks:
        try:
            tags = concept_blocks(h["stock_code"])
        except Exception as e:                         # 风控/解析失败：跳过该股主题，不中断
            failed_stocks.append({"code": h["stock_code"], "name": h["stock_name"],
                                  "weight": h["nav_pct"],
                                  "reason": f"板块获取失败: {str(e)[:60]}"})
            continue
        # 🔴 每股先去重再累计：同一股票的 白酒Ⅱ/白酒Ⅲ 清洗后都是"白酒"，
        #    不去重会把权重翻倍（实测白酒基金曾算出 259%）
        uniq = {k for k in (_clean_tag(t) for t in tags) if k}
        for key in uniq:
            theme[key] += h["nav_pct"]

    # 平局裁决必须确定：同权重时短词优先（"白酒"2字 胜 "食品饮料"4字，行业分级里
    # 短词通常是更聚焦的主题），再按字典序——否则 set 遍历序受哈希随机化影响，
    # 同一基金每次穿透 Top1 主题会在 白酒/食品饮料 间漂移（实测踩过）
    top_themes = sorted(theme.items(), key=lambda x: (-x[1], len(x[0]), x[0]))[:6]
    pe_list = sorted(q["pe_ttm"] for q in quotes.values() if q["pe_ttm"] > 0)
    pe_med = None
    if pe_list:                                        # 标准中位数：偶数取中间两值均值
        n = len(pe_list)
        pe_med = pe_list[n // 2] if n % 2 else (pe_list[n // 2 - 1] + pe_list[n // 2]) / 2

    # 未参与主题聚合的权重 = 板块失败股权重 + 权重缺失股按 0 计（无从取数）
    unclassified = round(sum(f["weight"] for f in failed_stocks if f["weight"]), 2)
    detail = [{**h, "quote": quotes.get(h["stock_code"], {})} for h in picks]
    return {
        "fund": fund_snapshot(query)["name"],
        "period": latest, "topn_weight_pct": round(weight_sum, 2),
        "detail": detail,
        "themes": [(k, round(v, 2)) for k, v in top_themes],
        "pe_median_profitable": pe_med,                # 仅盈利股（PE>0）
        "failed_stocks": failed_stocks,
        "unclassified_weight_pct": unclassified,
    }


def plain_text_map(p: dict) -> str:
    """穿透结果 → 白话地图文本（"这只基金主要是白酒约86%"）。"""
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
        lines.append(f"⚠ {len(p['failed_stocks'])} 只未参与主题聚合"
                     f"（合计权重 {p.get('unclassified_weight_pct', 0):.1f}%）: {names}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
# L7 宏观利率层（V0.3.0）：中债曲线族 + 东财利率报表 + 腾讯回购代理
# ══════════════════════════════════════════════════════════════════
# 服务基金域分析：债基利率敏感度解释 / 理财波动地图 / 期限匹配 / 信用利差监控。
# 三条通道：中债官网（曲线族主源，1请求=1条曲线1年日频）· 东财 datacenter
# （SHIBOR/国债日频/基准利率，报表名见坑P10）· 腾讯（GC007 回购代理）。
# 中债/东财均走 ttfund_get 统一限流；push2 实时走既有 em_get 股票域线。

CB_BASE = "https://yield.chinabond.com.cn/cbweb-mn"
CB_REFERER = "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN"


def _cb_request(path: str, params: Optional[dict] = None, method: str = "GET",
                timeout: int = 30) -> bytes:
    """中债专用通道：subprocess curl（坑P11：中债 TLS 指纹墙——requests/urllib 稳定
    SSL EOF，curl 稳定 200，A/B 实测 2026-11-06）。仍计入 ttfund 限流与审计账本。

    参数拼接后整串进 curl（list 参数无 shell 注入面；ycDefId 入 URL 前过白名单校验）。
    """
    from urllib.parse import urlencode
    url = f"{CB_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    import subprocess
    for attempt in (1, 2):                              # curl 也有瞬态，重试一次
        _cross_proc_throttle("cb", TT_MIN_INTERVAL)    # F1：中债独立节流线（非东财域，不熔断）
        TT_REQUEST_COUNT[0] += 1
        TT_REQUEST_COUNT[1]["yield.chinabond.com.cn"] = \
            TT_REQUEST_COUNT[1].get("yield.chinabond.com.cn", 0) + 1
        cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", method,
               "-H", "User-Agent: Mozilla/5.0", "-H", f"Referer: {CB_REFERER}",
               "-H", "Accept: */*", url]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        except FileNotFoundError:
            raise RuntimeError("中债通道需要系统 curl（TLS 指纹墙，requests 不可用，"
                               "见坑P11）——当前环境未安装 curl。")
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise RuntimeError(f"中债 curl 超时（>{timeout}s）: {url[:90]}")
            time.sleep(1.5)
            continue
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        if attempt == 2:
            raise RuntimeError(
                f"中债 curl 通道失败（rc={proc.returncode}，{len(proc.stdout)}B）: "
                f"{proc.stderr.decode('utf-8', 'ignore')[:120] or url[:100]}")
        time.sleep(1.5)
    raise RuntimeError("unreachable")

# 关键曲线种子 ID（2026-11-06 实测从 queryTree 提取；cb_curve_tree() 可拉全族校准）
_CB_CURVES = {
    "treasury":      ("中债国债收益率曲线", "2c9081e50a2f9606010a3068cae70001"),
    "enterprise_aaa": ("中债企业债收益率曲线(AAA)", "2c9081e50a2f9606010a309f4af50111"),
    "enterprise_aa": ("中债企业债收益率曲线(AA)", "2c90818812b319130112c279222836c3"),
    "ncd_aaa":       ("中债商业银行同业存单收益率曲线(AAA)", "8308218D1D030E0DE0540010E03EE6DA"),
    "ncd_aa_plus":   ("中债商业银行同业存单收益率曲线(AA＋)", "8308218DB7D90E0DE0540010E03EE6DA"),
    "ncd_aa":        ("中债商业银行同业存单收益率曲线(AA)", "8308218E52AF0E0DE0540010E03EE6DA"),
    "ncd_a_plus":    ("中债商业银行同业存单收益率曲线(A＋)", "8308218F885B0E0DE0540010E03EE6DA"),
    "ncd_a":         ("中债商业银行同业存单收益率曲线(A)", "8308219023310E0DE0540010E03EE6DA"),
}


def _term_years(term: str) -> Optional[float]:
    """中债标准期限码 → 年：'0d'→0.0，'3m'→0.25，'2y'→2.0；无法解析返回 None。"""
    t = str(term).strip().lower()
    try:
        if t.endswith("d"):
            return round(float(t[:-1]) / 365, 6)
        if t.endswith("m"):
            return round(float(t[:-1]) / 12, 6)
        if t.endswith("y"):
            return float(t[:-1])
    except ValueError:
        pass
    return None


def _parse_cb_xlsx(content: bytes) -> list[dict]:
    """中债 downYearBzqx 的 OOXML → [{date, term, term_years, yield_pct}]。

    坑P9：单元格是 inlineStr（无 sharedStrings），zipfile+regex 直接解；
    行结构 (日期, 期限码, 收益率) 三元组，表头行自然被日期正则排除。
    """
    z = zipfile.ZipFile(io.BytesIO(content))
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "ignore")
    pts = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = [_html.unescape(c).strip()
                 for c in re.findall(r"<t[^>]*>([^<]*)</t>", row)]
        if len(cells) >= 3 and re.fullmatch(r"\d{4}/\d{2}/\d{2}", cells[0]):
            try:
                pts.append({"date": cells[0].replace("/", "-"), "term": cells[1],
                            "term_years": _term_years(cells[1]),
                            "yield_pct": float(cells[2])})
            except ValueError:
                continue                                    # 非数据行（空值等），跳过
    return pts


def _cb_curve_id(curve: str) -> tuple[str, str]:
    """曲线键 → (名称, ycDefId)。种子表优先，miss 则拉曲线树按名称模糊匹配。"""
    if curve in _CB_CURVES:
        return _CB_CURVES[curve]
    tree = cb_curve_tree()                                 # name → id（含全族 40+ 条）
    hits = {n: i for n, i in tree.items() if curve in n}
    if len(hits) != 1:
        raise ValueError(f"未知曲线 {curve!r}：请用 _CB_CURVES 键（{sorted(_CB_CURVES)}）"
                         f"或曲线树中的完整名称；模糊命中 {len(hits)} 条不猜。")
    return next(iter(hits.items()))


def _cb_validated_id(curve: str) -> tuple[str, str]:
    """_cb_curve_id + 32 位十六进制校验（防树缓存污染拼入 URL/缓存路径，TypeError 一并拦）。"""
    name, cid = _cb_curve_id(curve)
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", cid):
        raise ValueError(f"曲线 ID 非法: {cid!r}（需 32 位十六进制，来源 {name!r}）")
    return name, cid


def cb_curve_tree(force_refresh: bool = False) -> dict[str, str]:
    """中债曲线树全族 name→ycDefId（40+ 条：国债/国开/企业债AAA~AA-/城投/存单/ABS…）。

    端点 POST /yc/queryTree；磁盘缓存 30 天（曲线族极少变化）。
    """
    if not force_refresh:
        cached = _cache_read("cb_curve_tree.json", max_age_hours=30 * 24)
        if cached is not None:
            return cached
    raw = _cb_request("/yc/queryTree", {"locale": "zh_CN"}, method="POST")
    root = json.loads(raw)
    if not isinstance(root, list):
        root = root.get("data") or []
    out: dict[str, str] = {}

    def walk(nodes):
        for n in nodes:
            if isinstance(n, dict):
                if n.get("children"):
                    walk(n["children"])
                elif n.get("id"):
                    out[str(n.get("name", ""))] = n["id"]

    walk(root)
    if len(out) < 10:                                      # 正常 40+，骤降=结构变更
        raise RuntimeError(f"中债曲线树仅 {len(out)} 条叶子，疑似结构变更，拒绝缓存")
    _cache_write("cb_curve_tree.json", out)
    return out


def cb_yield_curve(curve: str = "treasury", year: Optional[int] = None,
                   force_refresh: bool = False) -> dict:
    """中债收益率曲线·年文件（坑P8：只走 downYearBzqx GET，不碰 searchYc）。

    1 请求 = 1 条曲线 1 年的日频全期限（0d~50y）；当日磁盘缓存命中 0 请求。
    返回 {curve, year, fetched_at, source, points: [{date, term, term_years, yield_pct}]}。
    """
    name, cid = _cb_validated_id(curve)             # 32-hex 校验先于缓存路径构造
    year = int(year or date.today().year)
    rel = f"cb_curves/{cid}_{year}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached
    params = {"locale": "zh_CN", "year": str(year), "ycDefId": cid,
              "qxll": "0,", "zblx": "txy", "dxbj": "0",
              "workTime": date.today().isoformat(),
              "wrjibile": "true", "wrjxCBFlag": ""}
    content = _cb_request("/yc/downYearBzqx", params)
    pts = _parse_cb_xlsx(content)
    if not pts:
        raise RuntimeError(f"中债 {name} {year} 年文件解析出 0 个数据点"
                           f"（{len(content)} 字节）——参数或上游结构变更，拒绝静默空")
    snap = {"curve": name, "year": year, "points": pts,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "source": "chinabond/downYearBzqx"}
    _cache_write(rel, snap)
    return snap


def cb_curve_series(curve: str, term: str = "10y", year: Optional[int] = None) -> list[dict]:
    """单期限时序：[{date, yield_pct}]（日期升序）。term 用中债期限码：'1y'/'3y'/'10y'…"""
    snap = cb_yield_curve(curve, year)
    rows = [p for p in snap["points"] if p["term"] == term]
    if not rows:
        raise ValueError(f"{snap['curve']} {snap['year']} 无期限 {term!r}，"
                         f"可用: {sorted({p['term'] for p in snap['points']})[:12]}")
    return [{"date": p["date"], "yield_pct": p["yield_pct"]} for p in rows]


def credit_spread(rating: str = "AA", term: str = "3y",
                  year: Optional[int] = None) -> list[dict]:
    """信用利差 = 中债企业债(rating) − 国债，同日期同期限对齐后本地算差（2 请求/年/日缓存）。

    rating 支持 AAA/AAA-/AA+/AA/AA-（全角＋转半角后匹配曲线树中文名）。
    """
    cn = f"中债企业债收益率曲线({rating.replace('+', '＋')})"
    corp = cb_yield_curve(cn, year)
    gov = cb_yield_curve("treasury", year)
    g = {p["date"]: p["yield_pct"] for p in gov["points"] if p["term"] == term}
    out = [{"date": p["date"], "spread_pct": round(p["yield_pct"] - g[p["date"]], 4)}
           for p in corp["points"] if p["term"] == term and p["date"] in g]
    if not out:
        raise ValueError(f"企业债({rating}) 与国债在 {term} 无共同日期，无法算利差")
    return out


def _dc_query(report: str, sort_col: str, page_size: int = 60,
              filter_str: str = "", pages: int = 1,
              cache_key: Optional[str] = None) -> list[dict]:
    """东财 datacenter 宏观报表统一查询（走 ttfund_get 限流；坑P10：报表名需实测）。

    F7：cache_key 非空时当日磁盘缓存（宏观日更数据，重复查询零请求）。
    """
    if cache_key:
        cached = _cache_read(f"macro/{cache_key}.json", max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached
    rows: list[dict] = []
    for pg in range(1, pages + 1):
        params = {"reportName": report, "columns": "ALL", "pageSize": str(page_size),
                  "pageNumber": str(pg), "sortColumns": sort_col, "sortTypes": "-1",
                  "source": "WEB", "client": "WEB"}
        if filter_str:
            params["filter"] = filter_str
        r = ttfund_get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                       params=params,
                       headers={"Referer": "https://data.eastmoney.com/cjsj/"})
        assert_not_risk_control(r, "tt", "L7 宏观")
        r.raise_for_status()
        d = r.json()
        if not d.get("success", False):
            raise RuntimeError(f"东财 {report} 查询失败: {d.get('message')}")
        batch = (d.get("result") or {}).get("data") or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    if cache_key and rows:
        _cache_write(f"macro/{cache_key}.json", rows)
    return rows


_EM_TSY_FIELDS = {"EMM00588704": "cn_2y", "EMM00166462": "cn_5y",
                  "EMM00166466": "cn_10y", "EMM00166469": "cn_30y",
                  "EMG00001306": "us_2y", "EMG00001308": "us_5y",
                  "EMG00001310": "us_10y", "EMG00001312": "us_30y"}


def treasury_yield_daily(days: int = 30) -> list[dict]:
    """国债收益率·日频（东财 RPTA_WEB_TREASURYYIELD，中债口径，**新→旧序**）：中美 2/5/10/30Y+利差。

    days>500 时按 500 截断（单页上限）；中债↔东财同口径（三源互证实测分毫不差）。
    """
    rows = _dc_query("RPTA_WEB_TREASURYYIELD", "SOLAR_DATE",
                   page_size=min(days, 500), cache_key=f"tsy_{min(days, 500)}")
    out = []
    for r in rows[:days]:
        rec = {"date": str(r.get("SOLAR_DATE", ""))[:10]}
        rec.update({v: r.get(k) for k, v in _EM_TSY_FIELDS.items()})
        if all(rec.get(c) for c in ("cn_2y", "cn_10y")):
            rec["cn_10y_2y"] = round(rec["cn_10y"] - rec["cn_2y"], 4)
        out.append(rec)
    if not out:
        raise RuntimeError("东财国债日频返回空（RPTA_WEB_TREASURYYIELD），拒绝静默空")
    return out


def shibor_history(period: Optional[str] = None, days: int = 30) -> list[dict]:
    """SHIBOR 历史（东财 RPT_IMP_INTRESTRATEN，恒定过滤「上海银行同业拆借市场」）。

    🔴 坑（实测）：不加市场过滤时 Chibor（中国银行同业拆借市场）会混入同币种同期限
    序列且数值跳变 ~24bp——本函数恒定锁定上海市场（SHIBOR 本尊）。
    period=None 返回全期限混合；传 '隔夜(O/N)' / '1周(1W)' / '3月(3M)' / '1年(1Y)' 等得单序列。
    days 语义：单序列≈天数；混合模式每自然日 ~8 行（8期限），仅按 days×8 估页取前若干行。
    """
    if period and ('"' in period or "\\" in period):   # 括号是合法期名组成(1Y)，只拦引号断串
        raise ValueError(f"period 含非法字符: {period!r}")
    flt = '(CURRENCY_CODE="CNY")(MARKET="上海银行同业拆借市场")'
    if period:
        flt += f'(REPORT_PERIOD="{period}")'
    rows = _dc_query("RPT_IMP_INTRESTRATEN", "REPORT_DATE",
                     page_size=min(days * (1 if period else 8), 500), filter_str=flt,
                     cache_key=f"shibor_{period or 'all'}_{min(days, 500)}")
    out = [{"date": str(r.get("REPORT_DATE", ""))[:10], "period": r.get("REPORT_PERIOD"),
            "market": r.get("MARKET"), "rate_pct": r.get("IR_RATE"),
            "change_pct": r.get("CHANGE_RATE")} for r in rows]
    if not out:
        raise RuntimeError(f"SHIBOR 查询返回空（period={period!r}），拒绝静默空")
    if period:                                             # 双保险：按日期去重
        seen, dedup = set(), []
        for r in sorted(out, key=lambda x: x["date"], reverse=True):
            if r["date"] not in seen:
                seen.add(r["date"])
                dedup.append(r)
        return dedup
    return out


def benchmark_rate_history() -> list[dict]:
    """一年期存/贷款基准利率历次调整（东财 RPT_ECONOMY_DEPOSIT_RATE，2004 至今）。

    字段语义（实测锚定 2015-10-24/2012-07-06/2008-11-27 三次央行调息）：
      DEPOSIT_RATE_BB/SB = **调整前**一年定存/一年贷款；BA/SA = **调整后**；
      B/S = 变动**百分点**（非 bp，-0.25 即降 25bp）。⚠️ 本表无活期/短贷字段，勿被字段名 B/BB/S/SB 字面误导。
      ⚠️ 这是基准利率史非 LPR 报价（东财无 LPR 报表，实测 9501）。
    附每次调整次日沪深涨跌（next_sh/sz_pct）。
    """
    rows = _dc_query("RPT_ECONOMY_DEPOSIT_RATE", "REPORT_DATE", page_size=100,
                     cache_key="deposit_rate_all")
    out = [{"report_date": str(r.get("REPORT_DATE", ""))[:10],
            "deposit_1y_prev": r.get("DEPOSIT_RATE_BB"),
            "deposit_1y": r.get("DEPOSIT_RATE_BA"),
            "deposit_change_pp": r.get("DEPOSIT_RATE_B"),
            "loan_1y_prev": r.get("LOAN_RATE_SB"), "loan_1y": r.get("LOAN_RATE_SA"),
            "loan_change_pp": r.get("LOAN_RATE_S"),
            "next_sh_pct": r.get("NEXT_SH_RATE"), "next_sz_pct": r.get("NEXT_SZ_RATE")}
           for r in rows]
    if len(out) < 5:
        raise RuntimeError(f"基准利率史仅 {len(out)} 条（正常 30+），疑似上游结构变更")
    return out


def repo_rate_gc() -> dict:
    """GC007 交易所质押式回购利率（腾讯通道，1 请求）——DR007 的代理指标。

    ⚠️ 口径：交易所 vs 银行间（DR007）。走势相关性高，绝对水平有差；
    需要 DR007 本尊须等中国货币网（本网络环境被墙）或付费源。
    """
    q = tencent_quote(["204007"]).get("204007")
    if not q or not q.get("price"):
        raise RuntimeError("GC007(204007) 行情获取失败，拒绝静默空")
    return {"date": datetime.now(_BJ_TZ).strftime("%Y-%m-%d"), "rate_pct": q["price"],
            "change_pct": q.get("change_pct"), "prev_close": q.get("last_close"),
            "proxy_for": "DR007", "source": "tencent/GC007-交易所口径"}


def cn_treasury_realtime() -> dict:
    """国债收益率实时盘中（东财 push2 全球债券行情：CN2Y/5Y/10Y/30Y，走 em_get 股票域线）。"""
    out = {}
    for term, sec in [("2y", "CN2Y"), ("5y", "CN5Y"), ("10y", "CN10Y"), ("30y", "CN30Y")]:
        r = em_get("https://push2.eastmoney.com/api/qt/stock/get",
                   params={"secid": f"171.{sec}", "fields": "f43,f58,f169,f170"},
                   headers={"User-Agent": UA, "Referer": "https://quote.eastmoney.com/"})
        assert_not_risk_control(r, "em", "push2 stock/get 国债实时")
        r.raise_for_status()
        d = r.json().get("data") or {}
        if not d or not d.get("f43"):
            raise RuntimeError(f"{sec} 实时行情为空（f43 缺失），拒绝静默空")
        out[term] = {"yield_pct": d["f43"] / 10000,
                     "change": (d.get("f169") or 0) / 10000,
                     "change_pct": (d.get("f170") or 0) / 100}
    out["source"] = "eastmoney/push2-globalbond"
    out["time"] = datetime.now(_BJ_TZ).isoformat(timespec="seconds")
    return out
