# -*- coding: utf-8 -*-
"""
fund-data V0.6.0 — 基金底层数据工具包（基金主源 + 蛋卷备胎 + 本地计算 + 跨域穿透 + 宏观利率 + 全市场数据层 + 经理画像与专项组装层）

请求预算契约（防封铁律，详见 SKILL.md 第一章）：
  1. 单基金深度诊断 ≤2 请求（1×pingzhongdata，0~1×lsjz）
  2. 全市场筛选走 rankhandler 全量单请求或 TopN 单页，禁止 for fund in all: snapshot(fund)
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

__version__ = "0.6.0"

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
_cbreak_state = {"tt": [0, 0.0], "em": [0, 0.0], "cninfo": [0, 0.0]}   # [连击数, 上次信号时刻]；>5min 衰减归一


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


def _line_label(line: str) -> str:
    """熔断文案的线上标签：cninfo 线显示「巨潮」，其余显示「东财」。"""
    return "巨潮" if line == "cninfo" else "东财"


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
                f"{_line_label(line)}[{line}] 熔断中（疑似触发风控已自动停机），冷却剩 {remain}s 至 "
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
            f"{_line_label(line)}[{line}] 连续 {_CBREAK_TRIP} 次风控信号（最近: {why}），已自动熔断 "
            f"{_CBREAK_COOLDOWN // 60} 分钟至 {datetime.fromtimestamp(until).strftime('%H:%M:%S')}。"
            f"熔断期内所有{_line_label(line)}请求直接拒绝（保护 IP）。")


def assert_not_risk_control(resp, line: str = "tt", context: str = ""):
    """F6 防线：调用方 raise_for_status **之前**先过这一道——403/429 转 RiskControlError。

    用法：r = ttfund_get(...); assert_not_risk_control(r, "tt", "E2"); r.raise_for_status()
    RiskControlError 不是 RequestException 子类 → fund_snapshot 的降级 except 不会误捕
    → 杜绝"403 被当网络故障 → 降级 → 37 页 lsjz 风暴"（审计 F6 的恶性链）。
    """
    if resp.status_code in (403, 429):
        _cbreak_signal(line, f"HTTP {resp.status_code} {context}")
        raise RiskControlError(
            f"HTTP {resp.status_code}（{context}）：疑似{_line_label(line)}风控信号，"
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
    """全量基金清单 ~2.8万只：[{code, pinyin, name, type}, ...]

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

    兼容旧列式（系列名"规模"）以防上游回切。下游评测 skill 的份额兜底路径由此修复为正路。
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
    scale_hist = _scale_history(v.get("Data_fluctuationScale"))
    position_hist = _xy_pairs_to_records(v.get("Data_fundSharesPositions"), "position_pct")
    peer_hist = _xy_pairs_to_records(v.get("Data_rateInSimilarPersent"), "percentile")
    bench_curves = _benchmark_curves(v.get("Data_grandTotal"))
    # P18 关联自洽：变量存在但形态解析失败 ≠「E2 没有」——P3 只查键缺失，
    # 这类"present,unparsed"必须显式进 missing，消费者才能区分两种空
    if v.get("Data_fluctuationScale") and not scale_hist:
        missing.append("Data_fluctuationScale(present,unparsed)")
    if v.get("Data_fundSharesPositions") and not position_hist:
        missing.append("Data_fundSharesPositions(present,unparsed)")
    if v.get("Data_rateInSimilarPersent") and not peer_hist:
        missing.append("Data_rateInSimilarPersent(present,unparsed)")
    if v.get("Data_grandTotal") and not bench_curves:
        missing.append("Data_grandTotal(present,unparsed)")
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
        "manager": {"name": mgr_raw.get("name"), "manager_id": mgr_raw.get("id"),
                    "tenure_start": mgr_raw.get("workTime"),
                    "fund_size_yi": mgr_raw.get("fundSize"),
                    "return_pct": mgr_raw.get("profit"), "star": mgr_raw.get("star")},
        "nav_history": nav,                            # 单位净值全史（含每日分红事件）
        "ac_nav_history": [{"date": datetime.fromtimestamp(int(p["x"]) / 1000, _BJ_TZ).strftime("%Y-%m-%d"),
                            "ac_nav": float(p["y"])}
                           for p in (v.get("Data_ACWorthTrend") or [])
                           if isinstance(p, dict) and "x" in p],
        "top_stocks": _codes_list(v.get("stockCodesNew") or v.get("stockCodes")),
        "top_bonds": _codes_list(v.get("zqCodesNew") or v.get("zqCodes")),
        "scale_history": scale_hist,
        "holder_structure": _normalize_table(v.get("Data_holderStructure")),
        "asset_allocation": _normalize_table(v.get("Data_assetAllocation")),
        "stock_position_history": position_hist,
        "industry_allocation": _normalize_table(v.get("Data_industryAllocation")),
        "peer_rank_history": peer_hist,
        "benchmark_curves": bench_curves,
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
    """历史净值逐页（E3 lsjz）。定位为补充端点——E2 已含净值全史，本端点独有价值是按日申赎状态史与 E2 失效时的净值兜底。

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
        # 列序防线（P-C 升级）：不再按下标盲取——从 thead 解析「列名→索引」映射动态取列，
        # 上游保留文案但调整列位（如插一列涨跌额）时旧防线会静默错列（F10 家族有改列前科）。
        thead = re.search(r"<thead[^>]*>(.*?)</thead>", t.group(0), re.S)
        head_cells = ([re.sub(r"<[^>]+>|\s+", "", c)
                       for c in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S)]
                      if thead else [])
        if not head_cells:
            raise RuntimeError(f"jjcc 表头缺失/无 th 列（{code}，报告期 {period}），拒绝盲取")

        def col_idx(keyword: str) -> int:
            for i, h in enumerate(head_cells):
                if keyword in h:
                    return i
            raise RuntimeError(f"jjcc 表头缺 {keyword!r} 列（{code}，报告期 {period}）："
                               f"{head_cells}——上游列序变更，拒绝盲取")

        i_seq = col_idx("序号")
        i_code, i_name = col_idx("股票代码"), col_idx("股票名称")   # 全称做关键词：
        # "代码"/"名称"过短，上游若插"行业代码"之类列会错列命中
        i_pct, i_shares, i_value = col_idx("占净值"), col_idx("持股数"), col_idx("市值")
        i_max = max(i_seq, i_code, i_name, i_pct, i_shares, i_value)
        for cells in _html_table_rows(t.group(0)):
            if len(cells) > i_max and cells[i_seq].isdigit():   # 序号也走映射——上游删序号列时
                out.append({"period": period, "seq": int(cells[i_seq]),   # 代码列 isdigit 会冒充序号
                            "stock_code": cells[i_code], "stock_name": cells[i_name],
                            "nav_pct": _to_float(cells[i_pct]),
                            "shares_wan": _to_float(cells[i_shares]),
                            "value_wan": _to_float(cells[i_value])})
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
    """蛋卷基金详情（D1 djapi/fund/{code}）：总份额/经理/托管/风险等级/申赎状态。

    定位：东财系全挂时的跨域备胎（不同域名不同风控面）。
    ⚠️ 蛋卷 growth 净值端点已死（坑P5），此处只保档案，无净值能力。
    """
    code = resolve_fund_code(query)
    r = ttfund_get(f"https://danjuanfunds.com/djapi/fund/{code}",
                   headers={"Referer": "https://danjuanfunds.com/"})
    assert_not_risk_control(r, "tt", "D1 蛋卷")
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
    """日期序列必须升序（E2 净值升序；E3 lsjz 原生降序——喂指标前必须反转，坑P7；
    peer_rank_history 等一切时序输入同样适用）。"""
    dates = [r["date"] for r in records]
    if dates != sorted(dates):
        raise ValueError(
            f"{ctx}：日期序列非升序（首 {dates[0]} → 末 {dates[-1]}）。"
            f"若数据来自 lsjz(E3) 请先反转——降序序列会让收益/回撤方向全反。")


def fund_metrics(snapshot: dict, start: str = "", end: str = "",
                 risk_free_annual: float = 0.015) -> dict:
    """区间收益/年化/最大回撤/年化波动/夏普/卡玛（复权口径，零网络）。

    年化收益按实际天数复合年化，包括不足一年的区间；短窗口外推需谨慎解读。
    对照 ground truth：snapshot.period_returns_pct 是官方同期收益（测试容差 0.6pct）。
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
    annual_ret = (1 + total_ret) ** (365 / max(days, 1)) - 1

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


XIRR_MAX_RATE = 10.0          # XIRR 求解域上限（年化 1000%）；note 文案引用此常量防漂移


def _xirr(cashflows: list, max_rate: float = XIRR_MAX_RATE) -> Optional[float]:
    """分期现金流年化 IRR（二分法，A1）。cashflows: [(date_str, amount)]，投入负/期末市值正。

    有效性：单符号变化现金流（先负后正，定投即此模式）在 (-1, +∞) 恰有唯一根；
    NPV 对 r 非单调（先降后升存在极小值）但两端异号下二分仍收敛到唯一根。
    返回 None 的情形：无符号变化（如清零）、根超出求解域 (-99.99%, max_rate]、
    数值溢出——绝不硬造。
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
    if not amount > 0:
        raise ValueError(f"amount 必须为正数（每期扣款额），收到 {amount!r}")
    _assert_ascending_dates(snapshot["nav_history"], "fund_dca_backtest")
    adj = adjusted_nav(snapshot["nav_history"])
    if start or end:
        adj = [a for a in adj if (not start or a["date"] >= start)
               and (not end or a["date"] <= end)]
    if len(adj) < 2:
        raise ValueError("区间内净值不足 2 个点，无法回测")
    seen, invest_dates = set(), []
    for a in adj:                                      # 每期首个交易日 = 该期第一条净值
        if freq == "monthly":
            k = a["date"][:7]
        else:
            iso = date.fromisoformat(a["date"]).isocalendar()
            k = (iso.year, iso.week)
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
    short_note = None
    if len(invest_dates) < 2 or years < 0.25:
        short_note = (f"仅 {len(invest_dates)} 期 / {years:.2f} 年窗口，年化数字为极端外推，"
                      f"读作区间收益更稳妥")
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
                      "XIRR 无解：期末市值≈0（全亏）、窗口过短，或真实年化超出求解域上限"
                      f"（>{XIRR_MAX_RATE * 100:.0f}%），分期真年化不适用/不可信"),
        "short_window_note": short_note,
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
             "贴上限运行（≥95%）" if latest >= 95 else "仓位正常区间")
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
    _assert_ascending_dates(hist, "peer_rank_history（latest 会取错点）")
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
        if c0 <= -100.0:
            raise ValueError(f"基准曲线 {benchmark!r} 在 {dates[i - 1]} 累计值 {c0}%（≤-100，"
                             f"数值异常），拒绝计算")
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
    bench_flat, fund_flat = sxx <= 1e-18, syy <= 1e-18
    win = [a for a in adj if dates[0] <= a["date"] <= dates[-1]]
    fund_ret_pct = (win[-1]["adj_nav"] / win[0]["adj_nav"] - 1.0) * 100.0
    bench_ret_pct = b_by_date[dates[-1]]
    beta_note = None
    if bench_flat and fund_flat:
        beta_note = "基准与基金曲线窗口内均近乎横盘（方差≈0），β/相关性不可辨识——相对收益仍有效"
    elif bench_flat:                                  # corr=None 的根因也是基准侧，勿误报基金横盘
        beta_note = "基准曲线窗口内近乎横盘（方差≈0），β 不可辨识——相对收益仍有效，β 请勿解读"
    elif fund_flat:
        beta_note = "基金净值窗口内近乎横盘（方差≈0），相关性不可辨识，β 数值意义同样有限"
    return {"benchmark": benchmark,
            "window": {"start": dates[0], "end": dates[-1], "days": n},
            "fund_return_pct": round(fund_ret_pct, 2),
            "benchmark_return_pct": round(bench_ret_pct, 2),
            "relative_return_pct": round(fund_ret_pct - bench_ret_pct, 2),
            "beta": round(beta, 3) if beta is not None else None,
            "correlation": round(corr, 3) if corr is not None else None,
            "beta_note": beta_note}


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

    请求预算（P-B 修正）：1(持仓 jjcc，当日缓存 F4) + 1(腾讯批量) + N(板块，当日缓存)
    → 冷启动 ≈13 请求；当日重跑 = 1 请求（仅腾讯，持仓与板块全缓存命中）。
    基金名从 fund_list 主键表直查（0 请求）——不再为拿名字拉 436KB 快照（P-A）。
    单股失败不中断整体：跳过该股的主题聚合，记入 failed_stocks（含 reason 与权重）。
    """
    code = resolve_fund_code(query)
    name = next((f["name"] for f in fund_list() if f["code"] == code), code)
    hold = fund_holdings(code)
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
        "fund": name,
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
    corp_pts = [p for p in corp["points"] if p["term"] == term]
    # P-E：先分清「期限不存在」与「日期对不上」——报错文案直接决定排查方向
    if not corp_pts:
        raise ValueError(f"企业债曲线({rating}) 无 {term!r} 期限数据点。可选期限: "
                         f"{sorted({p['term'] for p in corp['points']})[:12]}")
    if not g:
        raise ValueError(f"国债曲线无 {term!r} 期限数据点。可选期限: "
                         f"{sorted({p['term'] for p in gov['points']})[:12]}")
    out = [{"date": p["date"], "spread_pct": round(p["yield_pct"] - g[p["date"]], 4)}
           for p in corp_pts if p["date"] in g]
    if not out:
        raise ValueError(f"企业债({rating}) 与国债在 {term} 无共同日期（两条曲线日期错位），无法算利差")
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
    if cache_key:
        # 空结果也缓存（P-D）：success 链路已校验，空 = 上游真无数据；
        # 不缓存会让合法空报表每次调用重发请求
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


# ══════════════════════════════════════════════════════════════════
# V0.5 全市场数据层：rankhandler 排行/初筛 + 新发 + 公司 + cninfo 抱团度/行业配置
# ══════════════════════════════════════════════════════════════════
# 防封设计（第一约束，2026-11-10 实测钉死）：
#   · rankhandler / FundNewIssue / company 与 E1/E2 同域（fund.eastmoney.com）→ tt 线
#     全套继承（F1 锁 / F2 熔断 / 1.5s 串行 / 会话复用）。rankhandler 的风控形态不是
#     403 而是 200+"无访问权限"（坑P19）→ 内容级断言 assert_rankhandler 计入熔断。
#   · cninfo 独立第四线：.throttle_cninfo 磁盘锁 + F2 熔断 + 独立账本
#     CNINFO_REQUEST_COUNT；enckey=AES(秒级时间戳) 本地算（坑P20，subprocess node
#     标准库，同 P11 curl 先例零新依赖）；451 token 过期=钥匙时效问题非风控→刷新重试。
#   · 预算契约：全市场排行 = 1 请求（pn=25000 实测 24792 行）+ 20h 缓存，类型/排序
#     筛选全在缓存上本地做；新发全量 ≈7 页；抱团度/行业配置 = 1 请求/季度长效缓存
#     （已发布季报数据不可变）。fund_rank 不暴露翻页参数——逐页爬 496 次的封禁路径
#     从函数签名上不存在。

CNINFO_REQUEST_COUNT = [0]                     # 第四线账本（审计用）
CNINFO_MIN_INTERVAL = 1.5                      # cninfo 线最小间隔（秒）
_CNINFO_AES_KEY = "1234567887654321"           # 巨潮公开 cninfo.js 中的固定密钥（坑P20）
_NODE_ENCKEY_TMPL = (
    "const crypto=require('crypto');"
    "const c=crypto.createCipheriv('aes-128-cbc',"
    "Buffer.from('%s'),Buffer.from('%s'));"
    "console.log(Buffer.concat([c.update(String(%d),'utf8'),c.final()]).toString('base64'));"
)


def _cninfo_enckey(ts: Optional[int] = None) -> str:
    """cninfo Accept-Enckey：AES-128-CBC(秒级时间戳) 的 Base64（坑P20）。

    密钥/IV 是巨潮公开 cninfo.js 里的固定串；用系统 node 标准库计算（与中债
    subprocess curl 同为先例，零新 Python 依赖）。ts 可注入——确定性测试用。
    """
    import subprocess
    if ts is None:
        ts = int(time.time())
    out = subprocess.run(
        ["node", "-e", _NODE_ENCKEY_TMPL % (_CNINFO_AES_KEY, _CNINFO_AES_KEY, ts)],
        capture_output=True, text=True, timeout=10)
    key = out.stdout.strip()
    if out.returncode != 0 or not key.endswith("="):
        raise RuntimeError(f"enckey 计算失败（需系统 node）: {out.stderr[:80]}")
    return key


def assert_rankhandler(resp, context: str = "rankhandler"):
    """rankhandler 家族内容级风控断言（坑P19）：被拦时是 200+"无访问权限"而非 403——
    只看状态码会漏。命中后计入 tt 线熔断并抛 RiskControlError（不触发降级）。"""
    assert_not_risk_control(resp, "tt", context)
    text = resp.content.decode("utf-8", errors="replace")[:500]
    if "无访问权限" in text:
        _cbreak_signal("tt", f"rankhandler 拒绝访问 {context}")
        raise RiskControlError(
            f"rankhandler 返回「无访问权限」（{context}）：疑似东财风控信号，已计入熔断。"
            f"该接口鉴权=Referer（fundranking.html），先检查 Referer 头是否丢失。")


def _cninfo_post(sysapi: str, params: dict, context: str) -> list:
    """cninfo 专题统计统一 POST：第四线节流 + enckey + 451 刷新重试一次。

    451/"token null" = enckey 时效问题（本地时钟漂移或秒边界）非风控——刷新钥匙
    重试一次，仍拒才报错；403/429 仍走 assert_not_risk_control 计熔断。
    """
    for attempt in (1, 2):
        _cbreak_check("cninfo")
        _cross_proc_throttle("cninfo", CNINFO_MIN_INTERVAL)
        key = _cninfo_enckey()
        try:
            CNINFO_REQUEST_COUNT[0] += 1
            r = TT_SESSION.post(f"https://webapi.cninfo.com.cn/api/sysapi/{sysapi}",
                                params=params, timeout=20,
                                headers={"Accept-Enckey": key,
                                         "Referer": "https://webapi.cninfo.com.cn/",
                                         "X-Requested-With": "XMLHttpRequest"})
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.0)
            continue
        assert_not_risk_control(r, "cninfo", context)
        try:
            d = r.json()
        except ValueError as e:                     # 非反爬 JSON（HTML 拦截页等）→ 带上下文报错
            raise RuntimeError(f"cninfo {context} 返回非 JSON: {r.content[:80]!r}") from e
        msg = str(d.get("resultmsg") or "")
        if d.get("resultcode") == 451 or "token" in msg:
            if attempt == 1:                        # 钥匙时效：刷新重试（非风控不计熔断）
                continue
            raise RuntimeError(f"cninfo enckey 两次被拒（{context}）：检查本机时钟是否漂移")
        recs = d.get("records")
        if not recs:
            raise RuntimeError(f"cninfo {context} 返回空 records: {str(d)[:120]}")
        return recs
    raise RuntimeError(f"cninfo {context} 重试路径未达（不可达分支）")


def _norm_rdate(rdate: str = "") -> str:
    """报告期季末归一化："20260930"/"2026-09-30" → "20260930"；空=最近已发布季末。"""
    s = re.sub(r"-", "", str(rdate or "").strip())
    if not s:
        return _latest_quarter_end().replace("-", "")
    valid = {(m, d) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))}
    if not re.fullmatch(r"\d{8}", s) or (int(s[4:6]), int(s[6:8])) not in valid:
        raise ValueError(f"rdate 须为季末日（0331/0630/0930/1231 之一），收到 {rdate!r}")
    return s


def _latest_quarter_end(today: Optional[date] = None) -> str:
    """≤today 的最近季末日（跨年自动回退到上年 12-31）。"""
    d = today or date.today()
    cands = [date(d.year, m, dd) for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31))]
    cands.append(date(d.year - 1, 12, 31))
    return max(c for c in cands if c <= d).isoformat()


# ── ① 全市场排行 / 初筛底座 ──────────────────────────────────────

_RANK_SC = {"ytd": "1nzf", "1m": "1yzf", "3m": "3yzf", "6m": "6yzf",
            "1y": "1n", "2y": "2n", "3y": "3n", "since": "ln"}
_RANK_FT = {"all", "zs", "gp", "hh", "zq", "qdii", "fof"}
_RANK_SORT_KEY = {"ytd": "ret_ytd_pct", "1m": "ret_1m_pct", "3m": "ret_3m_pct",
                  "6m": "ret_6m_pct", "1y": "ret_1y_pct", "2y": "ret_2y_pct",
                  "3y": "ret_3y_pct", "since": "ret_since_pct"}
# rankhandler 行不带类型 → 本地过滤用 E1 中文类型映射到类别桶
# （REIT/货币归 zq 仅为七桶全覆盖；zq 初筛注意货基收益量级与债基完全不同）
_TYPE_BUCKETS = (("指数型", "zs"), ("QDII", "qdii"), ("FOF", "fof"), ("股票型", "gp"),
                 ("混合型", "hh"), ("债券型", "zq"), ("REIT", "zq"), ("货币", "zq"))


def _bucket_of(fund_type) -> Optional[str]:
    if not fund_type:
        return None
    for prefix, bucket in _TYPE_BUCKETS:
        if prefix in fund_type:
            return bucket
    return None


def _rank_row(raw: str, type_map: dict) -> dict:
    """rankhandler CSV 行 → dict。字段序经官方 syl 四点校准（161725：近1月/3月/6月/
    1年分毫不差，2026-11-10）；[17][18][24] 语义不明不映射（[19]=原费率）。"""
    f = raw.split(",")

    def num(i):
        try:
            v = f[i] if i < len(f) else ""
            return float(v) if v not in ("", "--") else None
        except ValueError:
            return None

    def s(i):
        return f[i] if i < len(f) else ""

    code = s(0)
    fee = None
    if len(f) > 19 and f[19].endswith("%"):
        try:
            fee = float(f[19][:-1])
        except ValueError:
            pass
    return {"code": code, "name": s(1), "pinyin": s(2), "nav_date": s(3) or None,
            "nav": num(4), "ac_nav": num(5), "daily_pct": num(6),
            "ret_1w_pct": num(7), "ret_1m_pct": num(8), "ret_3m_pct": num(9),
            "ret_6m_pct": num(10), "ret_1y_pct": num(11), "ret_2y_pct": num(12),
            "ret_3y_pct": num(13), "ret_ytd_pct": num(14), "ret_since_pct": num(15),
            "founded": s(16) or None, "fee_pct": fee,
            "fund_type": type_map.get(code)}


def _rank_fetch(ft: str, sc: str, pn: int) -> list:
    """rankhandler 单次拉取（tt 线 + Referer 鉴权 + allRecords 强校验防截断）。"""
    today_iso = date.today().isoformat()
    r = ttfund_get("https://fund.eastmoney.com/data/rankhandler.aspx",
                   params={"op": "ph", "dt": "kf", "ft": ft, "pi": 1, "pn": pn,
                           "sd": (date.today() - timedelta(days=7)).isoformat(),
                           "ed": today_iso, "sc": sc, "st": "desc"},
                   headers={"Referer": "https://fund.eastmoney.com/data/fundranking.html"})
    assert_rankhandler(r, f"rankhandler ft={ft} sc={sc}")
    text = r.content.decode("utf-8", errors="replace")
    rows = re.findall(r'"(\d{6},[^"]*)"', text)
    m = re.search(r"allRecords:(\d+)", text)
    total = int(m.group(1)) if m else 0
    if not rows or not total:
        raise RuntimeError(f"rankhandler 返回空（ft={ft}）: {text[:100]!r}")
    # 全量模式必须总数一致；TopN 的 allRecords 是类别总数，只校验本页应收条数。
    expected = total if pn == 25000 else min(pn, total)
    if len(rows) != expected:
        raise RuntimeError(f"rankhandler 校验失败 allRecords={total} 实收 {len(rows)}，"
                           f"应收 {expected}（静默截断拒绝，pn={pn}）")
    return rows


def fund_rank(top: Optional[int] = None, fund_type: str = "all", sort: str = "ytd",
              ascending: bool = False, force_refresh: bool = False) -> list[dict]:
    """全市场基金排行 / 初筛底座（坑P19；全量 = 1 请求）。

    两种形态（无翻页参数——逐页爬 496 次的封禁路径从签名上不存在）：
      top=None（默认）：全市场一次拉（pn=25000 实测 24792 行），20h 缓存；
        fund_type/sort/ascending 全部在缓存上【本地】执行——组合筛选 0 请求
      top=N（1~100）：服务端单页（ft+sc 下推，取该类前 N 名），当日缓存；
        ascending=True 表示【前 N 名窗口内】升序展示（服务端仍按该字段取前 N）
    fund_type: all/zs/gp/hh/zq/qdii/fof（本地过滤经 E1 类型映射）；
    sort: ytd/1m/3m/6m/1y/2y/3y/since；None 值恒排末尾。
    """
    if fund_type not in _RANK_FT:
        raise ValueError(f"fund_type 须为 {sorted(_RANK_FT)}，收到 {fund_type!r}")
    if sort not in _RANK_SC:
        raise ValueError(f"sort 须为 {sorted(_RANK_SC)}，收到 {sort!r}")
    key_field = _RANK_SORT_KEY[sort]
    if top is None:
        rel = "rank/full_market.json"
        cached = None if force_refresh else _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            rows = cached
        else:
            type_map = {f["code"]: f["type"] for f in fund_list()}
            rows = [_rank_row(raw, type_map) for raw in _rank_fetch("all", "1nzf", 25000)]
            _cache_write(rel, rows)
        if fund_type != "all":
            rows = [r for r in rows if _bucket_of(r.get("fund_type")) == fund_type]
        # None 恒排末尾：降序把 None 映射 -inf、升序映射 +inf，再统一 reverse
        nil = float("-inf") if not ascending else float("inf")
        rows.sort(key=lambda r: r.get(key_field) if r.get(key_field) is not None else nil,
                  reverse=not ascending)
        return rows
    top = int(top)
    if not 1 <= top <= 100:
        raise ValueError("top 只支持 1~100（更大需求走全量模式 top=None 本地排序）")
    # 缓存键含 top+sort：服务端窗口（按 sc 字段取前 N 名）随两者变化，键必须同变；
    # 仅 ascending 不入键——升/降序在缓存读取后本地重排兑现
    rel = f"rank/top_{fund_type}_{sort}_{top}.json"
    rows = None if force_refresh else _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
    if rows is None:
        type_map = {f["code"]: f["type"] for f in fund_list()}
        # _RANK_FT 是合法 ft 值集合（fund_type 已校验在其中）；服务端恒 desc，
        # ascending 语义由下方本地重排兑现
        rows = [_rank_row(raw, type_map)
                for raw in _rank_fetch(fund_type, _RANK_SC[sort], top)]
        _cache_write(rel, rows)
    nil = float("-inf") if not ascending else float("inf")
    rows = sorted(rows, key=lambda r: r.get(key_field) if r.get(key_field) is not None else nil,
                  reverse=not ascending)
    return rows


# ── ② 新发基金 ────────────────────────────────────────────────────

def fund_new_issues(force_refresh: bool = False) -> list[dict]:
    """在发/新成立基金全量（FundNewIssue.aspx，tt 线 ≈7 页×1 请求，20h 缓存）。

    返回 [{code,name,company,company_id,fund_type,issue_period,fee_pct,manager}]，
    record 总数强校验（坑P2 同款防线，实测 record=332/pages=7）。
    顺带产出「公司名→公司ID」映射（fund_company 消歧用）。
    """
    rel = "new_issues.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached
    out, total = [], 0
    for page in range(1, 11):                        # 上限 10 页（500 条）防失控
        r = ttfund_get("https://fund.eastmoney.com/data/FundNewIssue.aspx",
                       params={"dt": 14, "page": f"{page},50", "order": "issuedate"},
                       headers={"Referer": "https://fund.eastmoney.com/data/xinfound.html"})
        assert_rankhandler(r, f"新发 page{page}")
        ntext = r.content.decode("utf-8", errors="replace")
        m = re.search(r"datas:\s*(\[.*?\])\s*,\s*record", ntext, re.S)
        if not m:
            raise RuntimeError(f"新发响应无 datas（page{page}）: {r.text[:80]!r}")
        try:
            arrs = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"新发 datas 解析失败（page{page}）: {e}") from e
        for a in arrs:
            def g(i):
                return a[i] if len(a) > i else ""
            out.append({"code": g(0), "name": g(1), "company": g(2), "company_id": g(3),
                        "fund_type": g(4), "issue_period": g(5),
                        "fee_pct": _to_float((g(6) or "").replace("%", "")) or None,
                        "manager": g(7) or None})
        mt = re.search(r"record:(\d+)", ntext)
        if not mt:                              # record 缺席=强校验失效，拒绝静默跳过
            raise RuntimeError(f"新发响应无 record 总数（page{page}），截断校验不可用")
        total = int(mt.group(1))
        if len(out) >= total:
            break
        if not arrs:
            break
    if len(out) != total:
        raise RuntimeError(f"新发分页校验失败 record={total} 实收 {len(out)}（截断拒绝）")
    if not out:
        raise RuntimeError("新发列表为空，拒绝静默返回")
    _cache_write(rel, out)
    return out


# ── ③ 基金公司画像 ────────────────────────────────────────────────

def _company_id_from_fund(fund_query: str) -> tuple:
    """基金 → (公司ID, 公司名)：F10 概况页（jbgk）服务端直出的 company/{id} 直链。"""
    code = resolve_fund_code(fund_query)
    r = ttfund_get(f"https://fundf10.eastmoney.com/jbgk_{code}.html",
                   headers={"Referer": "https://fundf10.eastmoney.com/"})
    assert_rankhandler(r, f"jbgk {code}")
    # ⚠️ 链接锚文本是通用的"基金公司"字样而非公司名——只取 ID，名字走详情页 title
    m = re.search(r'company/(\d{8})\.html',
                  r.content.decode("utf-8", errors="replace"))
    if not m:
        raise RuntimeError(f"jbgk 页未找到公司直链（{code}）——上游结构变更")
    return m.group(1), None


def fund_company(query: str, force_refresh: bool = False) -> dict:
    """基金公司画像（company/{id}.html 服务端直出，24h 缓存，1 请求）。

    query 三种形态：8 位数字公司ID（如 80065113）/ 基金代码或名称（6 位码是基金，
    经 jbgk 直链转公司，1 额外请求）/ 公司名（best-effort：先查新发缓存免请求命中；
    未命中则按基金名路径尝试——公司名≠基金名，多半显式报错，绝不静默错配）。
    返回 {company_id,name,scope_yi 管理规模(亿),fund_count 旗下基金数,
    manager_count 经理人数,funds:[{code,name}]}（旗下基金服务端表格去重）。
    """
    q = str(query).strip()
    jbgk_name = None
    if re.fullmatch(r"\d{8}", q):                   # 公司ID 恒 8 位（6 位是基金代码）
        cid = q
    else:
        cid = None
        # best-effort：新发缓存里的公司名→ID（免请求）；风控信号绝不吞——
        # 已计入熔断的信号若被 pass 掉，后续仍会连发 jbgk+company 两请求打枪口
        try:
            for it in fund_new_issues():
                if it.get("company") == q:
                    cid = it["company_id"]
                    break
        except RiskControlError:
            raise
        except Exception:
            pass
        if cid is None:                              # 走基金→jbgk→公司直链
            cid, jbgk_name = _company_id_from_fund(q)
    rel = f"company/{cid}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24)
        if cached is not None:
            return cached
    r = ttfund_get(f"https://fund.eastmoney.com/company/{cid}.html",
                   headers={"Referer": "https://fund.eastmoney.com/company/default.html"})
    assert_rankhandler(r, f"公司 {cid}")
    h = r.content.decode("utf-8", errors="replace")
    if len(h) < 10000:                               # 跳转页/空壳防线（E2 同款）
        raise RuntimeError(f"公司页异常（{cid} 仅 {len(h)}B）")
    def _num_near(label: str, unit: str) -> Optional[float]:
        """标签段内去 HTML 标签/空白/千分位逗号后取「label+数字+unit」。数字常被
        <span> 包裹；同名 label 会先出现在导航链接里（无数字），须逐个出现位置
        扫描直到命中。"""
        for i in (mm_.start() for mm_ in re.finditer(re.escape(label), h)):
            seg = re.sub(r"<[^>]+>|\s+|,", "", h[i:i + 300])
            m = re.search(re.escape(label) + r"[:：]?([0-9.]+)" + unit, seg)
            if m:
                return float(m.group(1))
        return None

    mt = re.search(r"<title>([^<\-]{2,25})", h)
    name = ((mt.group(1).strip() if mt else "").split("_")[0].split("-")[0]
            .strip().rstrip("主页").strip()) or None
    ms_v = _num_near("管理规模", "亿元")
    mf_v = _num_near("基金数量", "只")
    mm_v = _num_near("经理人数", "人")
    seen, funds = set(), []
    for code2, name2 in re.findall(
            r'href="https?://fund\.eastmoney\.com/(\d{6})\.html"[^>]*>([^<]{2,30})</a>', h):
        n2 = name2.strip()
        if code2 in seen or n2.isdigit() or n2 in ("基金品种", "净值", "持仓", "档案"):
            continue
        seen.add(code2)
        funds.append({"code": code2, "name": n2})
    out = {"company_id": cid, "name": name or jbgk_name,
           "scope_yi": ms_v,
           "fund_count": int(mf_v) if mf_v is not None else None,
           "manager_count": int(mm_v) if mm_v is not None else None,
           "funds": funds}
    if out["scope_yi"] is None and out["fund_count"] is None:
        raise RuntimeError(f"公司页字段解析失败（{cid}）——上游结构变更，拒绝半空返回")
    _cache_write(rel, out)
    return out


# ── ④ 抱团度 / 行业配置（cninfo 第四线）──────────────────────────

def fund_crowding(rdate: str = "", force_refresh: bool = False) -> list[dict]:
    """季度基金重仓股聚合（cninfo 官方专题统计 p_sysapi1112，坑P20）→ 抱团度原料。

    rdate 报告期季末（"20260930"/"2026-09-30" 皆可，空=最近已发布季末）。
    返回 [{code,name,fund_count 被多少只基金持有,shares_wan 持股总数(万),
    market_value_wan 持股市值(万元),date}] 按 fund_count 降序——TOP50 排名与
    TOP10 平均家数（市场抱团温度计）直接从这张表算。1 请求/季度，90 天缓存
    （已发布季报数据不可变）。实测 2026Q3：3088 只重仓股，Top1 中际旭创 2424 家。
    ⚠️ 披露窗口：季末后约 15 个工作日内各公司季报尚未披露完，最新一期聚合
    会随披露进度逐渐变多——首次拉取即被缓存 90 天，刚过季末慎用（可显式传
    上一季度 rdate 拿完整快照）。
    """
    rd8 = _norm_rdate(rdate)
    rel = f"crowding/{rd8}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24 * 90)
        if cached is not None:
            return cached
    recs = _cninfo_post("p_sysapi1112",
                        {"rdate": f"{rd8[:4]}-{rd8[4:6]}-{rd8[6:]}"}, f"基金重仓股 {rd8}")
    out = []
    for x in recs:
        try:
            out.append({"code": x.get("SECCODE"), "name": x.get("SECNAME"),
                        "fund_count": int(float(x.get("F001N") or 0)),
                        "shares_wan": float(x.get("F002N") or 0),
                        "market_value_wan": float(x.get("F003N") or 0),
                        "date": (x.get("ENDDATE") or "")[:10]})
        except (TypeError, ValueError):
            continue
    if len(out) < 100:
        raise RuntimeError(f"基金重仓股聚合仅解析出 {len(out)} 行（{rd8}），疑似结构变更")
    out.sort(key=lambda r: -r["fund_count"])
    _cache_write(rel, out)
    return out


def fund_industry_allocation(rdate: str = "", force_refresh: bool = False) -> list[dict]:
    """季度全市场基金行业配置聚合（cninfo p_sysapi1113，证监会行业门类口径）。

    返回 [{industry_code 门类码,industry 门类名,fund_count 持有基金家数,
    market_value_yi 持股市值(亿),weight_pct 占比%,date}] 按市值降序。
    实测 2026Q3：19 个门类（制造业 4.6 万亿/11.94% 居首）。同 fund_crowding 节流缓存，
    披露窗口内最新季聚合同样可能不完整（见 fund_crowding ⚠️）。
    """
    rd8 = _norm_rdate(rdate)
    rel = f"industry/{rd8}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24 * 90)
        if cached is not None:
            return cached
    recs = _cninfo_post("p_sysapi1113",
                        {"rdate": f"{rd8[:4]}-{rd8[4:6]}-{rd8[6:]}"}, f"行业配置 {rd8}")
    out = []
    for x in recs:
        try:
            out.append({"industry_code": x.get("F001V"), "industry": x.get("F002V"),
                        "fund_count": int(float(x.get("F003N") or 0)),
                        "market_value_yi": float(x.get("F004N") or 0),
                        "weight_pct": float(x.get("F005N") or 0),
                        "date": (x.get("ENDDATE") or "")[:10]})
        except (TypeError, ValueError):
            continue
    if len(out) < 10:
        raise RuntimeError(f"行业配置仅解析出 {len(out)} 行（{rd8}），疑似结构变更")
    out.sort(key=lambda r: -r["market_value_yi"])
    _cache_write(rel, out)
    return out


# ══════════════════════════════════════════════════════════════════
# V0.6 经理画像与专项组装层：持续性/规模/固收+/溢价/重仓探测/经理三件套
# ══════════════════════════════════════════════════════════════════
# 探针结论（2026-11-10 实测钉死）：
#   · rankhandler 行【无规模列】——[17][18]=自定义区间收益、[19][20]=申购费原价/折扣价、
#     [22]=优惠费率（161725/110022 双锚校准）；单基金规模走移动端 ENDNAV（209.47亿精确锚）
#   · 经理数据两源全通：主页 manager/{id}.html 服务端直出（mgrlists 在管清单+任职+规模+回报）、
#     dt=14 排行（4384 位，pn≤50 且服务端仅姓名序）；姓名→ID 无公开索引（坑P23：
#     dt=15 目录页忽略 pi/pn 恒返回拼音前10，FundSearchAPI 不索引经理名——二次实测 2026-11-11）
#   · FOF 底层基金明细五源实测无结构化接口（坑P24）——只有季报 PDF 有，诚实边界


def _tencent_kline(stock_code: str, bars: int = 90,
                   force_refresh: bool = False) -> list[dict]:
    """腾讯日K前复权收盘序列 [{date, close}]（nav_divergence 重仓股区间收益用）。

    腾讯线（不封 IP）+ P12 直连 opener 同款 + TENCENT_REQUEST_COUNT 审计入账；
    仅支持 6 位 A 股码（港股 5 位码由调用方过滤）；当日缓存。"""
    pre = _tencent_prefix(stock_code)
    rel = f"kline/{pre}_{int(bars)}.json"   # bars 入键：窗口根数随基金/日期漂移，防跨窗口串缓存
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={pre},day,,,{int(bars)},qfq")
    req = urllib.request.Request(url, headers={"Referer": "https://gu.qq.com/"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    raw = None
    for attempt in (1, 2):                        # 瞬态重试1次（对齐 tencent_quote 同通道）
        try:
            TENCENT_REQUEST_COUNT[0] += 1
            raw = opener.open(req, timeout=10).read().decode("utf-8")
            break
        except OSError as e:
            if attempt == 2:
                raise RuntimeError(f"腾讯日K请求失败({pre}): {e}") from e
            time.sleep(0.5)
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise RuntimeError(f"腾讯日K非JSON({pre}): {raw[:80]!r}") from e
    node = (d.get("data") or {}).get(pre) or {}
    rows = node.get("qfqday") or node.get("day") or []
    out = [{"date": x[0], "close": float(x[2])} for x in rows if len(x) >= 3]
    if len(out) < 2:
        raise RuntimeError(f"腾讯日K空返回({pre}): {str(d)[:100]}")
    _cache_write(rel, out)
    return out


def rank_persistence(top: int = 20, fund_type: str = "all",
                     force_refresh: bool = False) -> list[dict]:
    """业绩持续性矩阵（零请求）：排行缓存上算各周期同类分位与评分。

    周期 = 1m/3m/6m/ytd/1y/2y/3y；分位在【同类别桶内】算（vs 全市场更公平）；
    纳入门槛：≥4 个周期有值且必须含 ytd 与 1y（过短的新基不参与）。
    score_pct = 各周期分位均值；consistent = 全部已覆盖周期 ≥75 分位。
    bucket_n = 所属类别桶有值基金数——桶过小（<5）时恒高/低分位参考意义有限，自行降权。
    top=None 返回全部（按 score 降序）。组合初筛的「长牛」候选直接取前 N。"""
    if top is not None and top < 1:
        raise ValueError(f"top 须 ≥1 或 None（全部），收到 {top!r}")
    rows = fund_rank(fund_type=fund_type, force_refresh=force_refresh)
    horizons = {"1m": "ret_1m_pct", "3m": "ret_3m_pct", "6m": "ret_6m_pct",
                "ytd": "ret_ytd_pct", "1y": "ret_1y_pct", "2y": "ret_2y_pct",
                "3y": "ret_3y_pct"}
    # 按类别桶分组算分位（本地，零请求）
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_bucket_of(r.get("fund_type")) or "_", []).append(r)
    import bisect
    pct_by_code: dict[str, dict] = {}
    size_by_code: dict[str, int] = {}
    for group in buckets.values():
        for h, key in horizons.items():
            vals = sorted(x.get(key) for x in group if x.get(key) is not None)
            if not vals:
                continue
            for x in group:
                v = x.get(key)
                if v is None:
                    continue
                # 不高于该值的占比（含并列）：桶内最高=100、最低≈1/n（排名分位口径）
                pct = bisect.bisect_right(vals, v) / len(vals) * 100.0
                pct_by_code.setdefault(x["code"], {})[h] = round(pct, 1)
                size_by_code[x["code"]] = len(group)
    out = []
    for r in rows:
        p = pct_by_code.get(r["code"]) or {}
        if "ytd" not in p or "1y" not in p or len(p) < 4:
            continue                       # 纳入门槛：含 ytd+1y 且 ≥4 周期
        score = sum(p.values()) / len(p)
        out.append({"code": r["code"], "name": r["name"], "fund_type": r.get("fund_type"),
                    "score_pct": round(score, 1), "horizon_pct": p,
                    "covered": len(p), "bucket_n": size_by_code.get(r["code"]),
                    "consistent": all(v >= 75.0 for v in p.values())})
    out.sort(key=lambda x: -x["score_pct"])
    return out if top is None else out[:int(top)]


_MOB_COMMON = {"deviceid": "Wap", "plat": "Wap", "product": "EFund", "version": "6.2.8"}


def fund_scale(query: str, force_refresh: bool = False) -> dict:
    """单基金最新规模（1 请求）：移动端 ENDNAV 精确值，20h 缓存。

    rankhandler/E1 都没有规模列（坑P22）——这是全市场唯一免费单基金规模源；
    ENDNAV 为元精确值（161725 锚 20946896763.35 = 209.47 亿分毫不差）。
    全市场【规模榜】无聚合源 → 榜单场景由调用方对候选集逐只富集（预算自控），
    规模重叠预警同理（两只基金 = 2 请求）。"""
    code = resolve_fund_code(query)
    rel = f"scale/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    r = ttfund_get("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNBasicInformation",
                   params={**_MOB_COMMON, "FCODE": code}, timeout=15)
    assert_not_risk_control(r, "tt", f"fund_scale {code}")
    try:
        d = (r.json() or {}).get("Datas") or {}
    except ValueError as e:
        raise RuntimeError(f"fund_scale {code} 返回非JSON: {r.content[:80]!r}") from e
    endnav = d.get("ENDNAV")
    try:
        endnav_f = float(endnav)
    except (TypeError, ValueError):
        endnav_f = None
    if not endnav_f or endnav_f <= 0:
        raise RuntimeError(f"fund_scale {code} 无有效 ENDNAV（未披露或结构变更）：{endnav!r}")
    name = next((f["name"] for f in fund_list() if f["code"] == code), None)
    out = {"code": code, "name": name, "scale_yi": round(endnav_f / 1e8, 2),
           "company": d.get("JJGS"), "source": "eastmoney/fundmobapi"}
    _cache_write(rel, out)
    return out


def bond_plus_profile(query: str, force_refresh: bool = False) -> dict:
    """固收+画像（零新请求）：权益仓位带 + 回撤控制带 + 利率敏感度标签。

    口径：股票占净比（资产配置最新披露期）→ 偏债(<30) / 平衡(30~60) / 偏股(≥60)；
    回撤带按 fund_metrics 最大回撤绝对值：≤5 优 / ≤10 良 / ≤20 中 / >20 弱；
    债券占净比 ≥50% 附加利率敏感标注（联动 cb_yield_curve / credit_spread）。"""
    snap = fund_snapshot(query, force_refresh=force_refresh)
    aa = snap.get("asset_allocation") or []
    if not aa:
        raise RuntimeError(f"固收+画像需要资产配置（{snap.get('name')}）——Data_assetAllocation 缺失")
    latest = aa[-1]
    stock_pct = latest.get("股票占净比")
    bond_pct = latest.get("债券占净比")
    if stock_pct is None:
        raise RuntimeError(f"资产配置缺「股票占净比」（{snap.get('name')}）——上游结构变更")
    m = fund_metrics(snap)
    mdd = abs(m.get("max_drawdown_pct") or 0.0)
    grade = "偏股" if stock_pct >= 60 else ("平衡" if stock_pct >= 30 else "偏债")
    dd_band = ("优" if mdd <= 5 else "良" if mdd <= 10 else "中" if mdd <= 20 else "弱")
    rate_note = None
    if (bond_pct or 0) >= 50:
        rate_note = f"债券占净比 {bond_pct}%——利率敏感，联动 cb_yield_curve / credit_spread 评估债底"
    pos = snap.get("stock_position_history") or []
    return {"code": snap["_meta"]["code"], "name": snap.get("name"),
            "as_of": latest.get("_date"), "stock_pct": stock_pct, "bond_pct": bond_pct,
            "grade": grade, "max_drawdown_pct": m.get("max_drawdown_pct"),
            "volatility_ann_pct": m.get("annualized_volatility_pct"),
            "drawdown_band": dd_band, "rate_note": rate_note,
            "position_latest_pct": pos[-1]["position_pct"] if pos else None}


def qdii_premium(codes: list) -> list[dict]:
    """QDII/ETF 场内溢价（N 只=2 请求）：腾讯市价 ÷ 最新净值 − 1。

    ⚠️ 口径：E6 净值为 T-1/T-2（坑P16 日历差），行情剧烈时段溢价会被高估——
    结果必须连同 nav_date 一起解读。仅对场内可交易码有意义（5/16 开头）。
    返回按溢价降序；净值缺失的只标注 error 不中断。"""
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        raise ValueError("codes 不能为空")
    if len(codes) > 50:
        raise ValueError(f"单次 ≤50 只（批量净值上限），收到 {len(codes)}")
    q = tencent_quote(codes)
    e6 = {x["code"]: x for x in fund_realtime_nav(codes)}
    out = []
    for c in codes:
        tq = q.get(c) or {}
        navrow = e6.get(c) or {}
        price, nav = tq.get("price"), navrow.get("nav")
        row = {"code": c, "name": tq.get("name") or navrow.get("name"),
               "price": price, "nav": nav, "nav_date": navrow.get("nav_date")}
        if price and nav:
            row["premium_pct"] = round((float(price) / float(nav) - 1) * 100, 2)
            row["note"] = (f"净值 {navrow.get('nav_date')}（T-1/T-2 口径），"
                           "剧烈行情时段溢价可能被高估")
        else:
            row["premium_pct"] = None
            row["note"] = "市价或净值缺失（非场内码/未披露）"
        out.append(row)
    out.sort(key=lambda x: -(x["premium_pct"] if x["premium_pct"] is not None else -9e9))
    return out


def nav_divergence(query: str, force_refresh: bool = False) -> dict:
    """重仓探测：净值收益 vs 上季重仓加权收益的背离度（疑似调仓信号）。

    窗口 = 最新披露报告期末 → 最新净值日；重仓股走腾讯日K前复权（当日缓存）；
    权重 = 占净值比不归一（coverage_pct 如实报告前十覆盖度）。
    预算：冷启 ≤12 请求（快照1+持仓1+K线≤10）；当日重跑 0~2。
    |背离|≤5pp 持仓一致 / ≤10pp 轻微背离（关注）/ >10pp 疑似调仓或风格漂移。
    仅支持 6 位 A 股重仓码（港股 5 位码跳过并在 skipped 标注）。
    ⚠️ 披露滞后：季报持仓天然滞后，背离≠一定调仓（也可能是持仓内涨跌分化）。"""
    snap = fund_snapshot(query)
    adj = adjusted_nav(snap["nav_history"])
    hold = fund_holdings(query)
    if not hold:
        raise RuntimeError(f"无股票持仓记录（{snap.get('name')}）——货币基金或上游空表，重仓探测不适用")
    period = hold[0]["period"]
    rows = [h for h in hold if h["period"] == period]
    base = next((a for a in adj if a["date"] > period), None)
    if base is None:
        raise RuntimeError(f"报告期 {period} 之后无净值（{snap.get('name')}），无法算窗口收益")
    base_idx = adj.index(base)
    if base_idx == 0:
        raise RuntimeError(f"报告期 {period} 前无净值基点（{snap.get('name')}）")
    fund_ret = (adj[-1]["adj_nav"] / adj[base_idx - 1]["adj_nav"] - 1) * 100
    days_span = (date.fromisoformat(adj[-1]["date"]) - date.fromisoformat(period)).days
    bars = min(days_span + 20, 250)
    weighted, coverage, skipped, used = 0.0, 0.0, [], 0
    for h in rows:
        w = (h.get("nav_pct") or 0) / 100.0
        code6 = str(h.get("stock_code") or "")
        if not re.fullmatch(r"\d{6}", code6):
            skipped.append({"code": code6, "name": h.get("stock_name"), "reason": "非6位A股码"})
            continue
        k = _tencent_kline(code6, bars=bars, force_refresh=force_refresh)
        seg = [x for x in k if period < x["date"] <= adj[-1]["date"]]   # 终点对齐净值日，防股票腿超前
        if len(seg) < 2:
            skipped.append({"code": code6, "name": h.get("stock_name"), "reason": "K线窗口不足"})
            continue
        r_i = (seg[-1]["close"] / seg[0]["close"] - 1) * 100
        weighted += w * r_i
        coverage += w
        used += 1
    if used == 0:
        raise RuntimeError(f"重仓股无一可取K线（{snap.get('name')}）——拒绝静默返回")
    div = fund_ret - weighted
    verdict = ("持仓一致" if abs(div) <= 5
               else "轻微背离（关注）" if abs(div) <= 10 else "疑似调仓/风格漂移")
    return {"code": snap["_meta"]["code"], "name": snap.get("name"),
            "period": period, "window": {"start": period, "end": adj[-1]["date"],
                                         "days": days_span},
            "fund_return_pct": round(fund_ret, 2),
            "holdings_weighted_return_pct": round(weighted, 2),
            "divergence_pp": round(div, 2), "verdict": verdict,
            "coverage_pct": round(coverage * 100, 1), "stocks_used": used,
            "skipped": skipped,
            "note": "季报披露天然滞后，背离≠一定调仓（持仓内涨跌分化同表象）"}


def _manager_page(mid: str) -> dict:
    """经理主页解析（manager/{id}.html 服务端直出，坑P23 形态）：1 请求。"""
    r = ttfund_get(f"https://fund.eastmoney.com/manager/{mid}.html",
                   headers={"Referer": "https://fund.eastmoney.com/"})
    assert_rankhandler(r, f"经理主页 {mid}")
    h = r.content.decode("utf-8", errors="replace")
    if len(h) < 10000:
        raise RuntimeError(f"经理主页异常（{mid} 仅 {len(h)}B）——ID 无效或结构变更")
    flat = re.sub(r"<[^>]+>|\s+", "", h)
    mt = re.search(r"<title>([^<_\-]{2,20})", h)
    name = (mt.group(1).strip() if mt else "").strip()
    tenure = re.search(r"累计任职时间[:：]?([0-9年又天]+)", flat)
    scale = re.search(r"现任基金[^0-9]{0,6}([0-9.]+)亿", flat)
    ret = re.search(r"任期回报[:：]?([\-0-9.]+)%", flat)
    mg = re.search(r"var\s+mgrlists\s*=\s*(\[.*?\]);", h, re.S)
    funds = []
    if mg:
        funds = [{"code": c, "name": n, "type": t} for c, n, t in
                 re.findall(r"\{valueField:'(\d{6})',name:'([^']+)',type:'(\d+)'\}", mg.group(1))]
    if not name or not funds:
        raise RuntimeError(f"经理主页解析失败（{mid}）：name={name!r} 在管清单={len(funds)}——上游结构变更")
    return {"manager_id": mid, "name": name,
            "tenure": tenure.group(1) if tenure else None,
            "scale_yi": float(scale.group(1)) if scale else None,
            "return_since_pct": float(ret.group(1)) if ret else None,
            "funds": funds, "fund_count": len(funds),
            "source": "eastmoney/manager-page"}


def fund_manager(query: str, force_refresh: bool = False) -> dict:
    """基金经理画像（1 请求，24h 缓存）：ID 直查或基金码转接现任经理。形态：
    经理ID（8 位数字，非 80 开头）/ 基金代码或名称（快照现任经理转接——快照已
    缓存则 0 请求；经理ID 由 pingzhongdata Data_currentFundManager 直出）。
    返回 {manager_id,name,tenure 累计任职时间,scale_yi 现任基金总规模,
    return_since_pct 任期回报,funds 在管清单,fund_count}。
    经理姓名→ID 无公开结构化索引（旧名单页 404、建议接口退化为固定样本、
    排行接口仅姓名序且全量 88 页超预算；dt=15 目录页实测恒返回拼音前 10 条
    且忽略 pi/pn——不可查询，坑P23）——请用基金码转接或经理ID。
    80 开头的 8 位 ID 是公司，走 fund_company。"""
    q = str(query).strip()
    if re.fullmatch(r"\d{8}", q):
        if q.startswith("80"):
            raise ValueError(f"{q} 是 80 开头的公司ID——公司画像请用 fund_company")
        mid = q
    else:
        snap = fund_snapshot(q)
        mgr = snap.get("manager") or {}
        mid = mgr.get("manager_id")
        if not mid:
            raise RuntimeError(f"未找到「{q}」的现任经理（Data_currentFundManager 缺失）"
                               "——基金可能清算或经理空缺")
    rel = f"manager/{mid}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24)
        if cached is not None:
            return cached
    out = _manager_page(mid)
    _cache_write(rel, out)
    return out


def manager_rank(pages: int = 1, force_refresh: bool = False) -> list[dict]:
    """经理排行（坑P23：服务端仅姓名序，50 条/页，record 强校验）。

    pages×50 位（record≈4384 校验防截断）。行含在管清单/任职天数/任期回报/
    在管规模——规模与回报列是【已取页内】字段，本地排序只对已取子集有意义，
    不等于全市场按规模排行（那需要 88 页，预算不允许；诚实边界）。
    缓存 20h。"""
    pages = int(pages)
    if not 1 <= pages <= 88:
        raise ValueError(f"pages 须 1~88（record≈4384 ÷ 50），收到 {pages}")
    rel = f"manager/rank_p{pages}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    out, record = [], None
    for pi in range(1, pages + 1):
        r = ttfund_get("https://fund.eastmoney.com/Data/FundDataPortfolio_Interface.aspx",
                       params={"dt": 14, "mc": "returnjson", "pt": 2, "sc": "abbname",
                               "st": "desc", "pi": pi, "pn": 50},
                       headers={"Referer": "https://fund.eastmoney.com/"})
        assert_rankhandler(r, f"经理排行 p{pi}")
        text = r.content.decode("utf-8", errors="replace")
        if record is None:
            mr = re.search(r"record:(\d+)", text)
            if not mr:
                raise RuntimeError("经理排行响应无 record 总数——截断校验不可用")
            record = int(mr.group(1))
        # 行形态 ["a","b",...]：组内引号成对出现，需精确匹配引号组序列（坑P23）
        raw_rows = re.findall(r'\[("[^"]*"(?:,"[^"]*")*)\]', text)
        for rr in raw_rows:
            f = [x for x in rr.split('","')]
            f[0] = f[0].lstrip('"')
            if f and f[-1].endswith('"'):
                f[-1] = f[-1].rstrip('"')
            if len(f) < 12:
                continue
            scale_yi = None
            ms = re.search(r"([0-9.]+)亿", f[10])
            if ms:
                scale_yi = float(ms.group(1))
            out.append({"manager_id": f[0], "name": f[1], "company_id": f[2],
                        "company": f[3],
                        "fund_codes": f[4].split(",") if f[4] else [],
                        "fund_names": f[5].split(",") if f[5] else [],
                        "tenure_days": int(f[6]) if f[6].isdigit() else None,
                        "return_pct": _to_float(f[7].rstrip("%")),
                        "best_fund_code": f[8], "best_fund_name": f[9],
                        "scale_yi": scale_yi})
    expected = min(50 * pages, record)
    if len(out) != expected:
        raise RuntimeError(f"经理排行校验失败 record={record} 实收 {len(out)}，应收 {expected}")
    _cache_write(rel, out)
    return out


# ══════════════════════════════════════════════════════════════════
# V0.6.1 基准指数层：BENCH_SECID 名称查表 + kline_daily 日线原语
# ══════════════════════════════════════════════════════════════════
# 收编自 quant-excess 端点层（实战两轮审查的代码）：指数=EM push2his 主源（tt 线
# + F6 分流）+ 腾讯 fqkline 备胎；股票=复用 _tencent_kline（腾讯不封 IP）。
# 缓存键含 lmt——窗口拉大时旧缓存不得静默截断。RiskControlError 绝不降级。

# 跟踪指数名 → EM secid（市场前缀：1=沪 0=深 2=中证指数公司；名称源自 F10 tsdata 官方字段）
BENCH_SECID = {
    "沪深300": "1.000300", "沪深300指数": "1.000300",
    "中证500": "1.000905", "中证500指数": "1.000905",
    "中证800": "1.000906", "中证1000": "1.000852", "中证1000指数": "1.000852",
    "中证红利": "1.000922", "上证红利": "1.000015",
    "创业板综合": "0.399102", "创业板综": "0.399102",
    "上证50": "1.000016", "科创50": "1.000688",
    "中证A500": "1.000510",                       # 2024-09 新发宽基（实测 K线可达）
    "创业板指": "0.399006", "创业板指数": "0.399006",
    "深证100": "0.399330", "中证全指": "1.000985", "国证2000": "0.399303",
    "中证2000": "2.932000",
}
_SECID_RE = re.compile(r"[012]\.\d{6}")
_TENCENT_KLINE_CAP = 800                       # 腾讯 fqkline 实测：≤800 全量返回；1000~2000
                                               # 静默截断~641；≥2100 param error（sh600519 扫描，P25）


def _kline_resolve(query: str) -> tuple:
    """kline_daily 入参归一 → ("index", secid) | ("stock", 代码或带前缀符号)。纯函数离线可测。

    前缀形态保留市场信息（审查实测：sh000016 若剥前缀会被按深市重推导成深康佳A——
    沪指 000xxx 与深股 000xxx 大面积碰撞，静默错数据）；sh/sz 前缀 + 码值命中
    BENCH_SECID 值域时路由回 index 走 EM 主源；399xxx 深指号段无股票冲突直路由指数。"""
    q = str(query).strip()
    if q in BENCH_SECID:
        return "index", BENCH_SECID[q]
    if _SECID_RE.fullmatch(q):
        return "index", q
    if re.fullmatch(r"399\d{3}", q):
        return "index", "0." + q
    if re.fullmatch(r"\d{6}", q):
        return "stock", q
    m = re.fullmatch(r"(sh|sz|bj)(\d{6})", q.lower())
    if m:
        pref, code = m.group(1), m.group(2)
        secid = ("1." if pref == "sh" else "0.") + code
        if secid in set(BENCH_SECID.values()):
            return "index", secid
        return "stock", f"{pref}{code}"
    supported = "、".join(sorted(set(k for k in BENCH_SECID if "指数" not in k)))
    raise ValueError(
        f"kline_daily 不识别 {query!r}——支持：指数名（{supported}…）、secid（如 1.000300）、"
        f"6位股票代码或 sh/sz/bj 前缀形式")


def _secid_tencent_sym(secid: str) -> Optional[str]:
    """EM secid → 腾讯指数符号；市场 2（中证指数公司发布）无腾讯代码 → None 不降级。"""
    mkt, code = secid.split(".")
    if mkt == "1":
        return f"sh{code}"
    if mkt == "0":
        return f"sz{code}"
    return None


def kline_daily(query: str, lmt: int = 2600, use_tencent_fallback: bool = True,
                force_refresh: bool = False) -> list[dict]:
    """指数/股票日线收盘序列 [{date, close}] 升序（基准指数层原语，坑P25）。

    query 三形态：指数名（BENCH_SECID，如 "沪深300"/"中证红利"/"国证2000"）/
    EM secid（如 "1.000300"）/ 6 位股票代码（复用 _tencent_kline 腾讯通道）。
    指数 = EM push2his 主源（tt 线 + F6 分流）+ 腾讯备胎（市场2无腾讯代码不降级；
    腾讯 fqkline 实测上限 800 根，超过会静默截断/param error，备胎窗口自动钳制，P25）；
    股票 = 腾讯 fqkline（不封 IP，lmt>800 显式报错引导指数形态）。20h 缓存、键含 lmt；RiskControlError 不降级。
    基金诊断用途：基准对照/风格温度/压力测试；个股深度分析仍归 a-stock-data。
    """
    lmt = int(lmt)
    if lmt < 10:
        raise ValueError(f"lmt 至少 10 根，收到 {lmt}")
    kind, ident = _kline_resolve(query)
    if kind == "stock":
        if lmt > _TENCENT_KLINE_CAP:
            raise ValueError(
                f"股票日K腾讯通道实测上限 {_TENCENT_KLINE_CAP} 根（更大会静默截断或 param error，"
                f"P25）——更长窗口请用指数名或 secid 形态（EM 主源支持更大 lmt），收到 {lmt}")
        try:
            rows = _tencent_kline(ident, bars=lmt, force_refresh=force_refresh)
        except RuntimeError as e:
            hints = [c for c in ("0." + ident[-6:], "1." + ident[-6:])
                     if c in BENCH_SECID.values()]
            msg = str(e) + (f"——若 {ident[-6:]} 是指数，请用指数名或 secid 形态（如 {hints[0]}）"
                            if hints else "")
            raise RuntimeError(msg) from e
        if len(rows) < min(lmt, 60):
            raise RuntimeError(f"kline_daily 股票日K不足（{ident} 需≥{min(lmt, 60)} 实收 {len(rows)}）")
        return rows
    # 指数路径
    rel = f"kline/idx_{ident.replace('.', '_')}_lmt{lmt}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    need = min(lmt, 100)
    rows: list = []
    em_rows = 0
    fb_note = "未启用"
    try:
        r = ttfund_get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                       params={"secid": ident, "klt": 101, "fqt": 1, "lmt": lmt,
                               "end": "20500101", "fields1": "f1,f2,f3",
                               "fields2": "f51,f53"},
                       headers={"Referer": "https://quote.eastmoney.com/"})
        assert_not_risk_control(r, "tt", f"kline {ident}")
        r.raise_for_status()
        ks = ((r.json() or {}).get("data") or {}).get("klines") or []
        for line in ks:
            p = line.split(",")
            if len(p) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", p[0]):
                try:
                    rows.append({"date": p[0], "close": float(p[1])})
                except ValueError:
                    continue
    except RiskControlError:
        raise                                          # 风控≠网络故障，绝不降级（F6 契约）
    except requests.RequestException:
        rows = []                                      # 网络类故障才走备胎
    em_rows = len(rows)
    if em_rows < need and use_tencent_fallback:
        sym = _secid_tencent_sym(ident)
        if sym is None:
            fb_note = "备胎不可用（市场2中证指数无腾讯代码）"
        else:
            fb_note = "备胎失败"
            eff = min(lmt, _TENCENT_KLINE_CAP)         # 腾讯 fqkline 上限（P25）：超限恒返空
            kept = rows
            rows = []                                  # 重置防两源日期/复权基线混拼
            try:
                url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
                       f"param={sym},day,,,{eff},qfq")
                req = urllib.request.Request(url, headers={"Referer": "https://gu.qq.com/"})
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                TENCENT_REQUEST_COUNT[0] += 1
                d = json.loads(opener.open(req, timeout=10).read().decode("utf-8"))
                node = (d.get("data") or {}).get(sym) or {}
                for arr in node.get("qfqday") or node.get("day") or []:
                    if len(arr) >= 3:
                        rows.append({"date": arr[0], "close": float(arr[2])})
            except Exception as e:
                fb_note = f"备胎失败（{str(e)[:40]}）"
            if len(rows) < need:                       # 备胎也不够 → 回用 EM 有效行（诚实留痕）
                rows = kept
    rows.sort(key=lambda x: x["date"])
    seen, dedup = set(), []
    for r in rows:                                     # 按日期去重（下游收益率序列重复即拒算）
        if r["date"] not in seen:
            seen.add(r["date"])
            dedup.append(r)
    if len(dedup) < need:                              # 去重后过线（审查建议：防重复行虚过）
        raise RuntimeError(
            f"指数日线不足（{ident} 需≥{need}）：EM 实收 {em_rows} 行，{fb_note}，"
            f"去重后 {len(dedup)} 行")
    _cache_write(rel, dedup)
    return dedup


# ══════════════════════════════════════════════════════════════════
# V0.6.2 规模全历史：F10 规模变动 gmbd 通道（53 期全史 + 期间申赎流量）
# ══════════════════════════════════════════════════════════════════
# pingzhongdata 的规模史只有 ~5 期（图表数据）；gmbd 是 F10「规模变动」页同一
# 数据源的全量版（募集期→最新季报，实测 000312 53 期），多出期间申购/赎回流量
# 列——容量衰减判定与"申购驱动 vs 净值驱动"分析的原料。列序动态映射（P-C 同款
# 防线），新→旧源序反转为旧→新输出（与 snapshot.scale_history 口径一致）。


def _js_string_after(s: str, anchor: str) -> Optional[str]:
    """anchor 后第一个 JS 双引号字符串的值（逐字符扫描，尊重 \\" 转义）。

    gmbd 响应的 content 后还跟着基金元数据 JSON（含大量引号），贪婪正则会越界——
    fund_holdings 的 `content:"(.*)"` 模式在 gmbd 上不可复用。"""
    i = s.find(anchor)
    if i < 0:
        return None
    i = s.find('"', i + len(anchor))
    if i < 0:
        return None
    out, j = [], i + 1
    while j < len(s):
        ch = s[j]
        if ch == "\\" and j + 1 < len(s):              # 尊重转义（\" 等成对取用）
            out.append(s[j:j + 2])
            j += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        j += 1
    return "".join(out)


def fund_scale_history(query: str, force_refresh: bool = False) -> list[dict]:
    """基金规模全历史（F10 规模变动 gmbd 通道，1 请求，当日缓存）。

    返回 [{date, subscribe_yi 期间申购(亿份), redeem_yi 期间赎回(亿份),
    shares_yi 期末总份额(亿), nav_yi 期末净资产(亿), nav_change_pct 净资产变动率}]
    按【日期升序】（源序新→旧已反转，与 snapshot.scale_history 口径一致）；
    募集期行无申赎流量（---→None）。1 请求 ≈ N 季全史，容量衰减判定从
    "近 5 期对比"升级为全历史对比；期间申赎流量还可支撑"申购驱动 vs 净值驱动"
    归因分析。当日缓存 20h。"""
    code = resolve_fund_code(query)
    rel = f"scale_history/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    r = ttfund_get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                   params={"type": "gmbd", "mode": 0, "code": code},
                   headers={"Referer": f"https://fundf10.eastmoney.com/tsdata_{code}.html"})
    assert_not_risk_control(r, "tt", f"gmbd {code}")
    r.raise_for_status()
    html = _js_string_after(r.text, "content:")
    if not html:
        raise RuntimeError(f"gmbd 响应无 content（{code}）：{r.text[:120]!r}")
    table = re.search(r"<table[^>]*>.*?</table>", html, re.S)
    if not table:
        raise RuntimeError(f"gmbd 规模表为空（{code}）——上游结构变更或该基金无规模披露")
    thead = re.search(r"<thead[^>]*>(.*?)</thead>", table.group(0), re.S)
    heads = [re.sub(r"<[^>]+>|\s+|&nbsp;", "", c)
             for c in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1) if thead else "",
                                 re.S)]
    required = ("期末总份额", "期末净资产")
    if not heads or any(not any(k in h for h in heads) for k in required):
        raise RuntimeError(f"gmbd 表头缺必需列（{code}）：{heads}——上游结构变更，拒绝盲取")

    def col(keyword: str) -> Optional[int]:
        for i, h in enumerate(heads):
            if keyword in h:
                return i
        return None

    idx = {k: col(k) for k in
           ("期间申购", "期间赎回", "期末总份额", "期末净资产", "净资产变动率")}
    if idx["期末总份额"] is None or idx["期末净资产"] is None:
        raise RuntimeError(f"gmbd 必需列定位失败（{code}）：{heads}")

    def num(text: str) -> Optional[float]:
        t = re.sub(r"<[^>]+>|\s+|&nbsp;", "", text).strip()
        if t in ("", "---", "--", "—"):
            return None
        try:
            return float(t.rstrip("%"))
        except ValueError:
            return None

    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S):
        cells = [re.sub(r"<[^>]+>|\s+|&nbsp;", "", c)
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if not cells or not re.match(r"\d{4}-\d{2}-\d{2}", cells[0].strip()):
            continue                                   # 表头行/空行跳过
        def cell_at(k):
            i = idx[k]
            return num(cells[i]) if i is not None and i < len(cells) else None
        rate = cell_at("净资产变动率")
        out.append({"date": cells[0].strip(),
                    "subscribe_yi": cell_at("期间申购"),
                    "redeem_yi": cell_at("期间赎回"),
                    "shares_yi": cell_at("期末总份额"),
                    "nav_yi": cell_at("期末净资产"),
                    "nav_change_pct": rate})
    if len(out) < 2:
        raise RuntimeError(f"gmbd 规模记录不足（{code} 仅 {len(out)} 期）——新基金或结构变更")
    out.sort(key=lambda x: x["date"])                  # 源序新→旧 → 反转为升序
    _cache_write(rel, out)
    return out
