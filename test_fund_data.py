# -*- coding: utf-8 -*-
"""
fund-data V0.7.8 分层测试运行器（按测试计划 V1.0）
  L1 单元（零网络）· L2 端点 smoke（结构断言）· L3 交叉验证（三源互证）
  L4 请求预算审计 · L5 降级演练与坑回归 · L6 穿透层（确定性/降级/路由/清洗）
用法: python3 test_fund_data.py [--offline-only | --online]（默认离线）
"""
import json
import os
import sys
import tempfile
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fund_data as fd

# 输出编码加固：本套件打印 emoji/中文，传统 Windows 控制台默认 cp1252/gbk
# 会 UnicodeEncodeError 崩溃（CI windows runner 实测）。errors="replace" 让
# 不可编码字符降级为 ? 而非中断——断言结果永不因控制台编码而丢失。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass  # 非 TextIO 或不可重配的流：维持原状

# 全部离线测试（含后续 mock 的缓存写入）都隔离在临时目录，绝不碰真实用户缓存。
_TEST_CACHE_TMP = tempfile.TemporaryDirectory(prefix="fund-data-offline-cache-")
fd.FUND_CACHE_DIR = Path(_TEST_CACHE_TMP.name)
# 模块 import 末尾按真实缓存目录默认激活了协调器；重定向后按沙箱目录重建，
# 测试期的配额/熔断状态绝不写进 ~/.fund_data_cache/coordinator。间隔归零：
# 滑窗/间隔语义由 fund_data_core 单测钉死，集成回归不为节流真睡 1.5s。
_ZERO_POLICY = {line: {"min_interval": 0} for line in
                ("eastmoney", "cninfo", "danjuan", "tencent", "sina", "chinabond", "csindex", "sws")}


def _fresh_coordination():
    """清空沙箱协调器落盘状态并重建（风控演练后复位闸门，等价 legacy 时代
    unlink 标记文件——闸门 OPEN 是跨进程持久化的，仅重建对象不擦状态）。"""
    import shutil as _shutil
    _shutil.rmtree(fd.FUND_CACHE_DIR / "coordinator", ignore_errors=True)
    return fd._reset_request_coordination(policies=_ZERO_POLICY)


_fresh_coordination()

# 离线密闭性种子：沙箱预置合成基金清单——业务函数 resolve 链（norm_fund_code→
# fund_list）全部命中种子零网络；在线 L2 首测 force_refresh 拉真实清单覆盖。
# 合成行只求覆盖离线消费方需要的代码族（161725 穿透/快照、005001 费率、
# 华夏成长份额族与 000312 备用），600519 有意不进（L5 拦截断言依赖）。
_SEED_LIST = [
    {"code": "161725", "pinyin": "csbc", "name": "测试白酒基金", "type": "股票指数"},
    {"code": "005001", "pinyin": "csjja", "name": "测试基金A", "type": "混合型-灵活"},
    {"code": "000001", "pinyin": "hxcz", "name": "华夏成长", "type": "混合型-偏股"},
    {"code": "024808", "pinyin": "hxczc", "name": "华夏成长C", "type": "混合型-偏股"},
    {"code": "024809", "pinyin": "hxczhh", "name": "华夏成长混合", "type": "混合型-偏股"},
    {"code": "000312", "pinyin": "csxf", "name": "测试消费", "type": "股票指数"},
]
fd._cache_write("fund_list.json", _SEED_LIST)

_unknown = [a for a in sys.argv[1:] if a not in ("--offline-only", "--online")]
if _unknown or ("--offline-only" in sys.argv and "--online" in sys.argv):
    sys.exit(f"参数错误 {sys.argv[1:]}（默认离线；真实请求须显式 --online）")
OFFLINE_ONLY = "--online" not in sys.argv
R = {"pass": 0, "fail": 0, "results": []}


def check(layer: str, name: str, cond: bool, detail: str = ""):
    ok = bool(cond)
    R["pass" if ok else "fail"] += 1
    R["results"].append((layer, name, "PASS" if ok else "FAIL", detail))
    print(f"[{layer}] {'✅' if ok else '❌'} {name}" + (f"  | {detail}" if detail else ""))
    return ok


def expect_raise(layer, name, fn, contains=""):
    try:
        fn()
        check(layer, name, False, "未抛出异常")
    except Exception as e:
        ok = (not contains) or (contains in str(e))
        check(layer, name, ok, f"抛出: {str(e)[:90]}")


# ════════════════════ L1 单元测试（零网络）════════════════════
def test_l1_offline():

    # V0.7.5 新浪通道：解析纯函数（真实捕获行样本，零网络）
    _sina_sample = ('var hq_str_sh118078="航亚转债,0.000,130.730,0.000,0.000,0.000,'
                    '130.005,130.005,0,0.000,6660,130.005,0,0.000,0,0.000,0,0.000,'
                    '0,0.000,6660,130.005,10,0.000,0,0.000,0,0.000,0,0.000,'
                    '2026-11-17,09:19:46,00,";\n'
                    'var hq_str_sz127120="千红转债,0.000,128.801,0.000,0.000,0.000,'
                    '128.500,128.500,0,0.000,2110,128.500,0,0.000,0,0.000,0,0.000,'
                    '0,0.000,2110,128.500,1072,0.000,0,0.000,0,0.000,0,0.000,'
                    '2026-11-17,09:19:39,00";')
    _sina_parsed = fd._parse_sina_body(_sina_sample)
    check("L1", "新浪解析-两券齐全", sorted(_sina_parsed) == ["118078", "127120"])
    check("L1", "新浪解析-字段取位", _sina_parsed["118078"]["name"] == "航亚转债"
          and _sina_parsed["118078"]["prev_close"] == 130.73
          and _sina_parsed["118078"]["bid1"] == 130.005
          and _sina_parsed["118078"]["date"] == "2026-11-17"
          and _sina_parsed["118078"]["time"] == "09:19:46"
          and _sina_parsed["127120"]["time"] == "09:19:39")
    check("L1", "新浪解析-残行跳过",
          fd._parse_sina_body('var hq_str_bad="残";') == {})
    # V0.7.5 新浪通道：风控/空体语义（协调器在位，stub opener 零网络）
    import urllib.error as _ue
    import io as _io

    class _StubSinaOpener:
        def __init__(self, mode):
            self._mode = mode

        def open(self, req, timeout=None):
            if self._mode == "403":
                raise _ue.HTTPError(req.full_url, 403, "Forbidden", None, None)
            return _io.BytesIO(b"")          # 空体

    _orig_bo = fd.urllib.request.build_opener
    try:
        for _mode, _expect, _label in (
                ("403", fd.RiskControlError, "新浪-403计风控抛RiskControlError"),
                ("empty", RuntimeError, "新浪-空体计信号抛RuntimeError")):
            fd.urllib.request.build_opener = (
                lambda h=None, _m=_mode: _StubSinaOpener(_m))
            _fresh_coordination()
            try:
                fd._sina_raw("https://hq.sinajs.cn/list=sh118078")
                check("L1", _label + "-应抛未抛", False)
            except _expect as e:
                ok = not _mode == "403" or "新浪" in str(e)
                check("L1", _label, ok)
            except Exception:
                check("L1", _label + "-错型错误", False)
    finally:
        fd.urllib.request.build_opener = _orig_bo

    # ── V0.7.7 申购限额：解析纯函数 + 嵌套 span + 采集 + 快照集成（stub 零网络）──
    _pl = fd.parse_purchase_limit
    _w = _pl("限大额（单日累计购买上限1,000.00万元）")
    check("L1", "限额-万元千分位", _w["kind"] == "limited" and _w["limit_cny"] == 1e7
          and _w["currency"] == "万元", str(_w))
    check("L1", "限额-元", _pl("限大额(单日累计购买上限1000.00元)")["limit_cny"] == 1000.0)
    check("L1", "限额-外币近似折算",
          _pl("单日累计购买上限1,000美元")["limit_cny"] == 7200.0
          and _pl("单日累计购买上限100港币")["limit_cny"] == 92.0
          and _pl("单日累计购买上限100欧元")["limit_cny"] == 780.0
          and _pl("单日累计购买上限100英镑")["limit_cny"] == 920.0
          and _pl("单日累计购买上限1000日元")["limit_cny"] == 50.0)
    check("L1", "限额-fx参数覆盖", _pl("单日累计购买上限100美元",
          fx={"美元": 7.0})["limit_cny"] == 700.0)
    check("L1", "限额-金额前空格容忍", _pl("限大额（单日累计购买上限 1000.00元）")
          ["limit_cny"] == 1000.0)
    check("L1", "限额-万元无小数", _pl("单日累计购买上限100万元")
          ["limit_cny"] == 1e6)
    _s = _pl("暂停申购")
    check("L1", "限额-暂停申购", _s["kind"] == "suspended" and _s["limit_cny"] == 0.0)
    check("L1", "限额-暂停附带金额优先", _pl("暂停申购（单日累计购买上限100元）")["kind"] == "limited")
    _x = _pl("限大额")
    check("L1", "限额-限大额无金额诚实None", _x["kind"] == "limited"
          and _x["limit_cny"] is None, str(_x))
    check("L1", "限额-开放申购", _pl("开放申购")["kind"] == "unlimited")
    check("L1", "限额-未覆盖形态unknown", _pl("封闭期")["kind"] == "unknown"
          and _pl("")["kind"] == "unknown" and _pl(None)["kind"] == "unknown")
    # 嵌套 span 解析器（真实形态：金额在子 span——简单 regex 必截断，000369 实测）
    _sp = fd._StaticCellParser()
    _sp.feed('<span class="staticCell">限大额 (<span>单日累计购买上限1000.00元</span>)</span>'
             '<span class="staticCell">开放赎回</span>')
    check("L1", "限额-嵌套span全文拼接",
          _sp.cells == ["限大额 (单日累计购买上限1000.00元)", "开放赎回"], str(_sp.cells))
    _sp2 = fd._StaticCellParser()               # class 多 token（"staticCell xx"）不丢 cell
    _sp2.feed('<span class="staticCell xx"><span>开放申购</span></span>')
    check("L1", "限额-class多token容忍", _sp2.cells == ["开放申购"], str(_sp2.cells))

    class _PLResp:
        status_code = 200

        def __init__(self, text):
            self.content = text.encode("utf-8")
            self.text = text

        def raise_for_status(self):
            return None

    _PL_HTML = ('<title>广发全球医疗保健指数(QDII)A(000369)-天天基金网</title>'
                '<div class="tradeStatus"><span class="itemTit">交易状态：</span>'
                '<span class="staticCell">限大额 (<span>单日累计购买上限1000.00元</span>)'
                '</span><span class="staticCell">开放赎回</span></div>')
    _pz7 = ('var fS_name="测试基金";var fS_code="000369";var ishb="0";'
            'var Data_netWorthTrend=[{"x":1767225600000,"y":1.0,"equityReturn":"0"}];'
            'var Data_ACWorthTrend=[];var Data_grandTotal=[];'
            'var Data_rateInSimilarPersent=[];var Data_fundSharesPositions=[];'
            'var Data_fluctuationScale={"categories":[],"series":[]};')
    _orig_tt7, _orig_cr7, _orig_cw7 = fd.ttfund_get, fd._cache_read, fd._cache_write
    _orig_fl7 = fd.fund_list
    _store7, _hits7 = {}, [0]
    try:
        fd.fund_list = lambda **k: _SEED_LIST + [
            {"code": "000369", "pinyin": "gfqqyl", "name": "广发全球医疗保健指数A",
             "type": "指数型-海外股票"}]
        fd._cache_read = lambda rel, max_age_hours=None: None
        fd._cache_write = lambda rel, payload: _store7.update({rel: payload})
        fd.ttfund_get = lambda url, **k: _PLResp(_PL_HTML)
        _r7 = fd.fund_purchase_limit("000369")
        check("L1", "限额-采集结构化", _r7["kind"] == "limited"
              and _r7["limit_cny"] == 1000.0
              and _r7["status_text"] == "限大额 (单日累计购买上限1000.00元)；开放赎回"
              and _r7["not_for_sale"] is False
              and _r7["name"] == "广发全球医疗保健指数" and _r7["code"] == "000369",
              str(_r7)[:120])
        check("L1", "限额-缓存键独立", "purchase_limits/000369.json" in _store7)
        # 申购/赎回 cell 分离：赎回 cell 的「限大额」词样不得污染申购判定（审查修复）
        _PL_HTML2 = ('<title>某基金(000312)-天天基金网</title>'
                     '<div class="tradeStatus"><span class="itemTit">交易状态：</span>'
                     '<span class="staticCell">开放申购</span>'
                     '<span class="staticCell">限大额赎回</span></div>')
        fd.ttfund_get = lambda url, **k: _PLResp(_PL_HTML2)
        _r7c = fd.fund_purchase_limit("000312")
        check("L1", "限额-kind只看申购cell",
              _r7c["kind"] == "unlimited"
              and _r7c["status_text"] == "开放申购；限大额赎回", str(_r7c)[:120])
        fd.ttfund_get = lambda url, **k: _PLResp(
            '<title>某基金(000001)</title>该基金暂不开放购买')
        _r7b = fd.fund_purchase_limit("000001")
        check("L1", "限额-不代销+无状态栏容错", _r7b["not_for_sale"] is True
              and _r7b["kind"] == "unknown" and _r7b["status_text"] == "未找到交易状态栏")
        fd._cache_read = lambda rel, max_age_hours=None: (
            _hits7.__setitem__(0, _hits7[0] + 1), {"cached": 1})[1]
        fd.fund_purchase_limit("000369")
        check("L1", "限额-当日缓存命中0请求", _hits7[0] == 1)

        # 畸形 HTML 容错（审查遗留的 cells[0] 边角：空 cell 不得占位）
        fd._cache_read = lambda rel, max_age_hours=None: None   # 前测试哨兵复位
        _PL_HTML3 = ('<title>某基金(000312)</title>'
                     '<div class="tradeStatus"><span class="itemTit">交易状态：</span>'
                     '<span class="staticCell"> </span>'
                     '<span class="staticCell">开放申购</span></div>')
        fd.ttfund_get = lambda url, **k: _PLResp(_PL_HTML3)
        _r7d = fd.fund_purchase_limit("000312")
        check("L1", "限额-空cell滤除不占cells[0]",
              _r7d["kind"] == "unlimited" and _r7d["status_text"] == "开放申购")
        _sp3 = fd._StaticCellParser()
        _sp3.feed('<span class="staticCell">限大额')       # 未闭合 span（畸形）
        check("L1", "限额-未闭合span不崩", _sp3.cells == [], str(_sp3.cells))
        _sp4 = fd._StaticCellParser()
        _sp4.feed('<span class="staticCell">开放<b>申购</b></span>')   # 非 span 嵌套
        check("L1", "限额-嵌套非span标签不扰depth", _sp4.cells == ["开放申购"],
              str(_sp4.cells))
        # force_refresh 绕缓存（不走 _cache_read）
        _cr_hits = [0]

        def _cr_count(rel, max_age_hours=None):
            _cr_hits[0] += 1
            return None

        fd._cache_read = _cr_count
        fd.ttfund_get = lambda url, **k: _PLResp(_PL_HTML)
        fd.fund_purchase_limit("000369", force_refresh=True)
        check("L1", "限额-force_refresh绕缓存", _cr_hits[0] == 0)
        # 快照 force_refresh 只重拉 E2——限额页独立当日缓存不连坐
        _pz_hits = [0]

        def _tt_split(url, **k):
            if "pingzhongdata" in url:
                _pz_hits[0] += 1
                return _PLResp(_pz7)
            raise AssertionError("限额页不应重拉（force_refresh 只作用于 E2）")

        fd._cache_read = lambda rel, max_age_hours=None: (
            {"cached": 1} if rel.startswith("purchase_limits/") else None)
        fd.ttfund_get = _tt_split
        _snap7c = fd.fund_snapshot("000369", force_refresh=True)
        check("L1", "限额-快照force_refresh不连坐限额缓存",
              _pz_hits[0] == 1 and _snap7c["purchase_limit"] == {"cached": 1},
              f"E2实发{_pz_hits[0]}次")
        # 快照集成：E2 与主页面按 url 分流——purchase_limit 挂进快照
        def _tt_router(pl_html=None, pl_exc=None):
            def _get(url, **k):
                if "pingzhongdata" in url:
                    return _PLResp(_pz7)
                if pl_exc is not None:
                    raise pl_exc
                return _PLResp(pl_html)
            return _get

        fd._cache_read = lambda rel, max_age_hours=None: None
        fd.ttfund_get = _tt_router(pl_html=_PL_HTML)
        _snap7 = fd.fund_snapshot("000369")
        check("L1", "限额-快照集成数据集", _snap7["purchase_limit"]["limit_cny"] == 1000.0
              and _snap7["purchase_limit"]["status_text"].startswith("限大额"))
        fd.ttfund_get = _tt_router(pl_exc=fd.RiskControlError("模拟风控"))
        _snap7b = fd.fund_snapshot("000369")
        check("L1", "限额-主页面失败不炸快照",
              _snap7b["purchase_limit"] is None
              and "purchase_limit(fetch-failed)" in _snap7b["missing_datasets"]
              and len(_snap7b["nav_history"]) == 1,  # E2 成果不陪葬
              str(_snap7b["missing_datasets"]))
    finally:
        fd.ttfund_get, fd._cache_read, fd._cache_write = _orig_tt7, _orig_cr7, _orig_cw7
        fd.fund_list = _orig_fl7

    # ── V0.7.8 基金报告：清单分类/缓存解耦/EdgeOne 挑战/PDF 下载（stub 零网络）──
    class _RPResp:
        status_code = 200

        def __init__(self, payload):
            self.content = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            self.text = self.content.decode("utf-8", "replace")

        def raise_for_status(self):
            return None

        def json(self):
            return json.loads(self.text)

    _JJGG = {"Data": [
        {"ID": "AN202610271830986993",
         "TITLE": "交银施罗德趋势优先混合型证券投资基金2026年第3季度报告",
         "ShortTitle": "交银趋势混合A", "PUBLISHDATE": "2026-10-27 00:00:00"},
        {"ID": "AN202608311828777324",
         "TITLE": "交银施罗德趋势优先混合型证券投资基金2026年中期报告",
         "ShortTitle": "", "PUBLISHDATE": "2026-08-31"},
        {"ID": "AN202603281820829871",
         "TITLE": "交银施罗德趋势优先混合型证券投资基金2025年年度报告",
         "ShortTitle": "交银趋势混合A", "PUBLISHDATE": "2026-03-28"},
        {"ID": "", "TITLE": "无ID行应跳过", "ShortTitle": "", "PUBLISHDATE": "2026-01-01"},
    ], "TotalCount": 114, "ErrCode": 0}
    # 机制等价合成挑战（算术→document.cookie 两赋值→setTimeout 字符串；不存档真实挑战体）
    _SYN_CHALLENGE = (
        '<script>function a(a){function n(){for(var a={wQzOV:"4|2|0|3|1",'
        'iTyzs:function(a,n){return a+n}},n=a.wQzOV.split("|"),e=0;;){'
        'switch(n[e++]){case"0":t+="EO_Bot_Ssid=";continue;case"1":return t;'
        'case"2":t+="";continue;case"3":t=a.iTyzs(t,111222333);continue;'
        'case"4":var t="";continue}break}}'
        'var e={WTKkN:100000001,bOYDu:200000002,dtzqS:function(a,n){return a+n},'
        'wyeCN:300000003,pCQRM:function(a){return a()}},t=0;'
        'return t+=e.WTKkN,t+=e.bOYDu,t=e.dtzqS(t,e.wyeCN),[t,e.pCQRM(n)][a]}'
        'document.cookie="__tst_status="+a(0)+"#;",document.cookie=a(1)+";",'
        'setTimeout("location.href=location.href",1200);</script>')
    _cl = fd._classify_report_title
    check("L1", "报告-标题分类", _cl("某基金2026年第3季度报告") == {"year": 2026, "kind": "Q3"}
          and _cl("某基金2026年第1季度报告") == {"year": 2026, "kind": "Q1"}
          and _cl("某基金2026年中期报告") == {"year": 2026, "kind": "interim"}
          and _cl("某基金2025年年度报告") == {"year": 2025, "kind": "annual"}
          and _cl("某基金2025年年度报告摘要") == {"year": 2025, "kind": "annual"}
          and _cl("招募说明书(更新)") == {"year": None, "kind": "other"}
          and _cl("") == {"year": None, "kind": "other"}
          and _cl(None) == {"year": None, "kind": "other"})
    _eo = fd._edgeone_solve(_SYN_CHALLENGE)
    check("L1", "报告-EdgeOne挑战求解",
          _eo == {"__tst_status": "600000006#", "EO_Bot_Ssid": "111222333"}, str(_eo))
    try:
        fd._edgeone_solve("<script></script>")
        check("L1", "报告-空挑战体应抛未抛", False)
    except RuntimeError:
        check("L1", "报告-空挑战体显式报错", True)
    try:
        fd._edgeone_solve("<script>--version</script>")   # option 形挑战体（argv 注入面）
        check("L1", "报告-option形挑战体应抛未抛", False)
    except RuntimeError:
        check("L1", "报告-option形挑战体不进宿主进程", True)
    _eo_attrs = fd._edgeone_solve(
        '<script>document.cookie="__tst_status=7#; path=/;";'
        'document.cookie="EO_Bot_Ssid=42; Path=/; HttpOnly;";</script>')
    check("L1", "报告-EdgeOne cookie属性段剥离",
          _eo_attrs == {"__tst_status": "7#", "EO_Bot_Ssid": "42"}, str(_eo_attrs))

    _orig_tt8, _orig_cr8, _orig_cw8 = fd.ttfund_get, fd._cache_read, fd._cache_write
    _orig_fl8, _orig_eoc8 = fd.fund_list, list(fd._EO_COOKIE_CACHE)
    fd._EO_COOKIE_CACHE[:] = []                    # 块首复位进程级挑战缓存（防哨兵泄漏）
    _store8, _hits8, _ttlog8 = {}, [0], []
    try:
        fd.fund_list = lambda **k: _SEED_LIST + [
            {"code": "519702", "pinyin": "jyqs", "name": "交银趋势优先混合A", "type": "混合型-灵活"}]
        fd._cache_read = lambda rel, max_age_hours=None: None
        fd._cache_write = lambda rel, payload: _store8.update({rel: payload})

        def _tt_jjgg(url, **k):
            _ttlog8.append((url, k.get("params"), k.get("headers")))
            return _RPResp(json.dumps(_JJGG))

        fd.ttfund_get = _tt_jjgg
        _r8 = fd.fund_reports("519702")
        check("L1", "报告-清单结构化", _r8["code"] == "519702"
              and _r8["category"] == "periodic" and _r8["total"] == 114
              and len(_r8["reports"]) == 3                      # 无 ID 行被跳过
              and _r8["reports"][0] == {"code": "519702", "report_id": "AN202610271830986993",
                                        "title": "交银施罗德趋势优先混合型证券投资基金2026年第3季度报告",
                                        "short_title": "交银趋势混合A", "date": "2026-10-27",
                                        "year": 2026, "kind": "Q3",
                                        "pdf_url": "https://pdf.dfcfw.com/pdf/H2_AN202610271830986993_1.pdf"}
              and _r8["reports"][1]["kind"] == "interim"
              and _r8["reports"][1]["short_title"] is None
              and _r8["reports"][2]["kind"] == "annual",
              str(_r8["reports"][0])[:100])
        check("L1", "报告-Referer与全量抓取",
              len(_ttlog8) == 1 and _ttlog8[0][1]["type"] == 3
              and _ttlog8[0][1]["pageSize"] == 100                       # 缓存与 limit 解耦
              and f"jjgg_519702_3.html" in (_ttlog8[0][2] or {}).get("Referer", ""),
              str(_ttlog8[0][1]))
        check("L1", "报告-缓存键独立", "reports/519702_c3.json" in _store8
              and len(_store8["reports/519702_c3.json"]["reports"]) == 3)
        # limit 切片：未命中路径抓满 100 后按 limit 切；命中路径同样按 limit 切
        _r8b = fd.fund_reports("519702", limit=1)
        check("L1", "报告-limit切片(未命中抓满后切)",
              len(_r8b["reports"]) == 1 and len(_ttlog8) == 2
              and _ttlog8[-1][1]["pageSize"] == 100,
              f"rows={len(_r8b['reports'])}")
        fd._cache_read = lambda rel, max_age_hours=None: (
            _hits8.__setitem__(0, _hits8[0] + 1), _store8.get(rel))[1]
        _r8c = fd.fund_reports("519702", limit=2)
        check("L1", "报告-缓存命中按limit切0请求",
              len(_r8c["reports"]) == 2 and _hits8[0] == 1 and len(_ttlog8) == 2,
              f"limit2={len(_r8c['reports'])} 命中{_hits8[0]}")
        _cr_hits8 = [0]

        def _cr_count8(rel, max_age_hours=None):
            _cr_hits8[0] += 1
            return None

        fd._cache_read = _cr_count8
        fd.fund_reports("519702", force_refresh=True)
        check("L1", "报告-force_refresh绕缓存", _cr_hits8[0] == 0 and len(_ttlog8) == 3)
        try:
            fd.fund_reports("519702", category="quarterly")
            check("L1", "报告-非法category应抛未抛", False)
        except ValueError:
            check("L1", "报告-非法category显式报错", True)
        fd.ttfund_get = lambda url, **k: _RPResp('{"Data":"","ErrCode":-999,"TotalCount":0}')
        try:
            fd.fund_reports("519702")
            check("L1", "报告-假成功形态应抛未抛", False)
        except RuntimeError as e:
            check("L1", "报告-假成功形态(ErrCode-999)显式报错",
                  "ErrCode=-999" in str(e) or "Data 非 list" in str(e), str(e)[:80])
        fd.ttfund_get = _tt_jjgg
        _no_id = {"Data": [{"TITLE": "无ID", "PUBLISHDATE": "2026-01-01"}], "TotalCount": 7}
        fd.ttfund_get = lambda url, **k: _RPResp(json.dumps(_no_id))
        try:
            fd.fund_reports("519702")
            check("L1", "报告-total>0解析空应抛未抛", False)
        except RuntimeError as e:
            check("L1", "报告-total>0解析空拒绝空返回", "total=7" in str(e), str(e)[:80])
        fd.ttfund_get = lambda url, **k: _RPResp('{"Data": [], "TotalCount": 0}')
        _r8d = fd.fund_reports("519702", category="dividend")
        check("L1", "报告-空类别诚实空", _r8d["reports"] == [] and _r8d["total"] == 0,
              str(_r8d))

        # PDF 下载：URL 白名单 / 已存在跳过 / 挑战→重放 / 顽固挑战 / 非预期体
        try:
            fd.fund_report_pdf({"pdf_url": "https://example.com/x.pdf"})
            check("L1", "报告-非dfcfw直链应拒未拒", False)
        except ValueError:
            check("L1", "报告-非dfcfw直链拒绝", True)
        _pdf_row = {"code": "519702", "date": "2026-10-27", "kind": "Q3",
                    "pdf_url": "https://pdf.dfcfw.com/pdf/H2_TEST_1.pdf"}
        with tempfile.TemporaryDirectory() as _d8:
            _p8 = Path(_d8) / "519702_2026-10-27_Q3.pdf"
            _p8.write_bytes(b"%PDF-1.7" + b"e" * 1200)  # 合法已存在文件 → 0 请求直返

            def _tt_never(url, **k):
                raise AssertionError("已存在文件不应发起网络请求")

            fd.ttfund_get = _tt_never
            check("L1", "报告-已存在跳过0请求", fd.fund_report_pdf(_pdf_row, dest_dir=_d8) == str(_p8))
            _p8.unlink(missing_ok=True)
            _seq8 = [_RPResp(_SYN_CHALLENGE), _RPResp(b"%PDF-1.7" + b"x" * 3000)]

            def _tt_seq8(url, **k):
                _ttlog8.append(k.get("headers"))
                return _seq8.pop(0)

            fd.ttfund_get = _tt_seq8
            _p8r = fd.fund_report_pdf(_pdf_row, dest_dir=_d8)
            check("L1", "报告-挑战求解重放落盘",
                  Path(_p8r).name == "519702_2026-10-27_Q3.pdf"
                  and Path(_p8r).read_bytes()[:4] == b"%PDF"
                  and fd._EO_COOKIE_CACHE == ["__tst_status=600000006#",
                                               "EO_Bot_Ssid=111222333"]
                  and "EO_Bot_Ssid=111222333" in (_ttlog8[-1] or {}).get("Cookie", ""),
                  f"{Path(_p8r).name} cookie重放验证")
            check("L1", "V0.8.3报告PDF-原子写无part残留",
                  not list(Path(_d8).glob("*.part")), "")
            _p8.unlink(missing_ok=True)
            _seq8[:] = [_RPResp(_SYN_CHALLENGE), _RPResp(_SYN_CHALLENGE)]  # 顽固挑战
            fd.ttfund_get = _tt_seq8
            try:
                fd.fund_report_pdf(_pdf_row, dest_dir=_d8)
                check("L1", "报告-顽固挑战应抛未抛", False)
            except RuntimeError as e:
                check("L1", "报告-挑战后仍非PDF显式报错", "仍未取得 PDF" in str(e), str(e)[:70])
            _p8.unlink(missing_ok=True)
            fd.ttfund_get = lambda url, **k: _RPResp(b"<html>404 not found</html>")
            try:
                fd.fund_report_pdf(_pdf_row, dest_dir=_d8)
                check("L1", "报告-非预期响应体应抛未抛", False)
            except RuntimeError as e:
                check("L1", "报告-非预期响应体拒绝", "非预期" in str(e), str(e)[:70])
            fd.ttfund_get = lambda url, **k: _RPResp(b"%PDF-tiny" + b"y" * 50)   # <1000B
            try:
                fd.fund_report_pdf(_pdf_row, dest_dir=_d8)
                check("L1", "报告-残包应拒未拒", False)
            except RuntimeError as e:
                check("L1", "报告-残包(<1KB)拒绝落盘", "疑似不完整" in str(e), str(e)[:70])
            _p8.unlink(missing_ok=True)
            # 撞名防护：年报与年报(摘要)同日披露同 kind=annual——rid 尾 6 位隔离
            _row_a = {"code": "519702", "date": "2026-03-27", "kind": "annual",
                      "report_id": "AN202603271111000001",
                      "pdf_url": "https://pdf.dfcfw.com/pdf/H2_AAA_1.pdf"}
            _row_b = {"code": "519702", "date": "2026-03-27", "kind": "annual",
                      "report_id": "AN202603271111000002",
                      "pdf_url": "https://pdf.dfcfw.com/pdf/H2_BBB_1.pdf"}
            fd.ttfund_get = lambda url, **k: _RPResp(b"%PDF-1.7" + b"a" * 2500)
            _pa8 = fd.fund_report_pdf(_row_a, dest_dir=_d8)
            _pb8 = fd.fund_report_pdf(_row_b, dest_dir=_d8)
            check("L1", "报告-同日同kind不同报告不撞名",
                  _pa8 != _pb8 and Path(_pa8).name.endswith("000001.pdf")
                  and Path(_pb8).name.endswith("000002.pdf"),
                  f"{Path(_pa8).name} vs {Path(_pb8).name}")
            # 字符串直链：文件名取 URL 尾段；会话 cookie 复用（首查带缓存 cookie 直连）
            fd.ttfund_get = lambda url, **k: (
                _ttlog8.append(k.get("headers")), _RPResp(b"%PDF-1.5" + b"z" * 2500))[1]
            _p8s = fd.fund_report_pdf("https://pdf.dfcfw.com/pdf/H2_STR8_1.pdf", dest_dir=_d8)
            check("L1", "报告-字符串直链+cookie复用",
                  Path(_p8s).name == "H2_STR8_1.pdf"
                  and (_ttlog8[-1] or {}).get("Cookie", "").startswith("__tst_status="),
                  Path(_p8s).name)
    finally:
        fd.ttfund_get, fd._cache_read, fd._cache_write = _orig_tt8, _orig_cr8, _orig_cw8
        fd.fund_list = _orig_fl8
        fd._EO_COOKIE_CACHE[:] = _orig_eoc8

    # ── V0.7.5 行情通道防封加固：软封锁/连接级/结构守卫/403 分流（stub 零网络）──
    class _StubOpener2:
        def __init__(self, mode, counter=None):
            self._mode, self._ctr = mode, counter

        def open(self, req, timeout=None):
            if self._ctr is not None:
                self._ctr[0] += 1
            if self._mode == "403":
                raise _ue.HTTPError(req.full_url, 403, "Forbidden", None, None)
            if self._mode == "conn":
                raise _ue.URLError("connection reset")
            if self._mode == "softblock":
                return _io.BytesIO('var hq_str_sh118078="";'.encode("gbk"))
            if self._mode == "cleanmiss":               # V0.8.1：非空体但无 var 行
                return _io.BytesIO(b"\n")
            return _io.BytesIO('v_sh600519="1~名~600519~1";'.encode("gbk"))

    _orig_bo2 = fd.urllib.request.build_opener
    try:
        # sina 软封锁：200+非空体+全空 var 行 → 风控信号 + fail-fast（不静默 {}）
        fd.urllib.request.build_opener = lambda h=None: _StubOpener2("softblock")
        _fresh_coordination()
        try:
            fd.sina_quote(["sh118078"])
            check("L1", "新浪-软封锁应抛未抛", False)
        except RuntimeError as e:
            check("L1", "新浪-软封锁全空var计信号抛错",
                  "全未命中" in str(e) and fd._cbreak_state["sina"][0] >= 1)
        # V0.8.1：无 var 行的干净空体（代码全无效/退市）→ 报错但不计风控连击
        fd.urllib.request.build_opener = lambda h=None: _StubOpener2("cleanmiss")
        _fresh_coordination()
        _sina_strikes = fd._cbreak_state["sina"][0]
        try:
            fd.sina_quote(["sh999999"])
            check("L1", "新浪-干净空体应抛未抛", False)
        except RuntimeError as e:
            check("L1", "新浪-干净空体不计风控连击",
                  "无 var 行" in str(e) and fd._cbreak_state["sina"][0] == _sina_strikes)
        # sina 连接级熔断：2 连败写停机标记，第 3 次零网络直拒；期满自动复位
        _ctr = [0]
        fd.urllib.request.build_opener = lambda h=None: _StubOpener2("conn", _ctr)
        _fresh_coordination()
        fd._sina_conn_fails[0] = 0
        fd._sina_conn_file().unlink(missing_ok=True)
        for _ in range(2):
            try:
                fd._sina_raw("https://hq.sinajs.cn/list=sh118078")
            except _ue.URLError:
                pass
        _stopped = fd._sina_conn_file().exists()
        try:
            fd._sina_raw("https://hq.sinajs.cn/list=sh118078")
            check("L1", "新浪-连接级停机应拒未拒", False)
        except RuntimeError as e:
            check("L1", "新浪-连接级2连败停机零网络",
                  _stopped and "连接级熔断" in str(e) and _ctr[0] == 2)
        fd._sina_conn_file().write_text("0 stale-test")   # 已过期标记 → 自动复位放行
        try:
            fd._sina_raw("https://hq.sinajs.cn/list=sh118078")
        except _ue.URLError:
            pass
        check("L1", "新浪-停机期满自动复位",
              _ctr[0] == 3 and not fd._sina_conn_file().exists())
        # tencent 结构守卫：响应有行但 <53 段 → 拒绝静默空（无效码不回行，不在此列）
        fd.urllib.request.build_opener = lambda h=None: _StubOpener2("short")
        _fresh_coordination()
        try:
            fd.tencent_quote(["sh600519"])
            check("L1", "腾讯-结构守卫应抛未抛", False)
        except RuntimeError as e:
            check("L1", "腾讯-结构守卫有行0解析拒绝静默空", "0 行解析成功" in str(e))
        # V0.8.3：业务解析成功才恢复腾讯熔断；原始 200 字节本身不算成功
        _success83 = fd._cbreak_success
        _success_calls83 = []
        try:
            _fields83 = ["1"] * 60
            _fields83[1], _fields83[3] = "贵州茅台", "100"
            _raw83 = ('v_sh600519="' + "~".join(_fields83) + '";').encode("gbk")
            fd.urllib.request.build_opener = lambda h=None: type(
                "_Opener83", (), {"open": lambda self, req, timeout=None: _io.BytesIO(_raw83)})()
            fd._cbreak_success = lambda line: _success_calls83.append(line)
            fd.tencent_quote(["600519"])
            check("L1", "V0.8.3腾讯-业务成功才清熔断连击",
                  _success_calls83 == ["tencent"], str(_success_calls83))
        finally:
            fd._cbreak_success = _success83
        # tencent 403 分流：RiskControlError + 连击镜像（不再被 OSError 当瞬态重试）
        fd.urllib.request.build_opener = lambda h=None: _StubOpener2("403")
        _fresh_coordination()
        try:
            fd._tencent_raw("https://qt.gtimg.cn/q=sh600519")
            check("L1", "腾讯-403应抛未抛", False)
        except fd.RiskControlError as e:
            check("L1", "腾讯-403计风控抛RiskControlError",
                  "腾讯" in str(e) and fd._cbreak_state["tencent"][0] >= 1)
        # V0.8.1：_tencent_kline_page 有状态码失败不瞬态重试（HTTPError 是 OSError
        # 子类须先捕获——V0.7.5 tencent_quote 同口径，V0.7.6 新函数漏沿用）
        _kline404_ctr = [0]

        class _Kline404Opener:
            def open(self, req, timeout=None):
                _kline404_ctr[0] += 1
                raise _ue.HTTPError(req.full_url, 404, "Not Found", None, None)

        fd.urllib.request.build_opener = lambda h=None: _Kline404Opener()
        _fresh_coordination()
        try:
            fd._tencent_kline_page("sh000300")
            check("L1", "腾讯日K-HTTPError应抛未抛", False)
        except _ue.HTTPError:
            check("L1", "腾讯日K-有状态码失败不瞬态重试",
                  _kline404_ctr[0] == 1, f"实际请求 {_kline404_ctr[0]} 次")
    finally:
        fd.urllib.request.build_opener = _orig_bo2
        fd._sina_conn_file().unlink(missing_ok=True)
        fd._sina_conn_fails[0] = 0                  # 复位子测试残留 1 次连败，防误触发后续 2 连败停机

    # ── V0.7.5 中债 curl 通道接协调器（stub gateway 零网络）──
    class _StubPermit:                            # 假许可：钉住中性结算/空体结算顺序
        def __init__(self):
            self.settled = []

        def failure(self, reason=None, response_received=False):
            self.settled.append((reason, response_received))

    class _StubResp:
        def __init__(self, status, content=b""):
            self.status_code, self.content = status, content
            self.permit = _StubPermit()           # 真实 Gateway 2xx 恒附待裁决许可

    class _StubGateway:
        def __init__(self, mode):
            self._mode, self.calls, self.last_permit = mode, [0], None

        def curl_request(self, method, url, **kw):
            self.calls[0] += 1
            if self._mode == "403":
                raise fd.RiskControlError("HTTP 403（中债）：疑似中债风控信号，已计入熔断")
            resp = _StubResp(200, b"" if self._mode == "empty" else b"xlsx-bytes")
            self.last_permit = resp.permit
            return resp

    _gw = fd._REQUEST_GATEWAY
    try:
        # 成功路径：2xx 非空 → 返回字节 + permit 中性结算被调（cb-passthrough）
        fd._REQUEST_GATEWAY = _StubGateway("ok")
        check("L1", "中债-协调器curl通道成功返回字节",
              fd._cb_request("/yc/downYearBzqx", {"ycDefId": "x" * 32}) == b"xlsx-bytes"
              and fd._REQUEST_GATEWAY.last_permit.settled
              == [("cb-passthrough", True)],
              "返回字节且待裁决许可中性结算（业务清零归 cb_* 调用方）")
        # 空体：先中性结算（cb-empty-body）再风控信号 + RuntimeError（防挂起许可占坑）
        _fresh_coordination()                        # 先重建协调器再装 stub——顺序颠倒会重建真网关冲掉 stub（曾致真实请求漏网）
        fd._REQUEST_GATEWAY = _StubGateway("empty")
        try:
            fd._cb_request("/yc/queryTree", method="POST")
            check("L1", "中债-空体应抛未抛", False)
        except RuntimeError as e:
            check("L1", "中债-空体先结算后信号拒绝静默空",
                  "空体" in str(e)
                  and fd._REQUEST_GATEWAY.last_permit.settled
                  == [("cb-empty-body", True)]
                  and fd._cbreak_state["cb"][0] >= 1)
        # 403 → RiskControlError 透传且不重试（外层瞬态循环豁免风险型）
        fd._REQUEST_GATEWAY = _StubGateway("403")
        try:
            fd._cb_request("/yc/queryTree", method="POST")
            check("L1", "中债-403应抛未抛", False)
        except fd.RiskControlError:
            check("L1", "中债-403风控透传不重试", fd._REQUEST_GATEWAY.calls[0] == 1)
    finally:
        fd._REQUEST_GATEWAY = _gw
    # teardown 地板 spy：legacy 分支走 CB_MIN_INTERVAL 独立常量（不再误用东财间隔）。
    # 两常量数值同 1.5，元组比对无法区分——临时改 CB 值验证调用点引用的是 CB 常量
    import subprocess as _sp
    _orig_run, _orig_thr, _orig_cbiv = _sp.run, fd._cross_proc_throttle, fd.CB_MIN_INTERVAL
    _thr_calls = []
    try:
        fd._teardown_request_coordination()
        fd.CB_MIN_INTERVAL = 0.7                     # 临时改值：若调用点读 TT(1.5) 会露馅

        class _P:
            returncode, stdout, stderr = 0, b"legacy-bytes\n200", b""

        _sp.run = lambda *a, **k: _P()
        fd._cross_proc_throttle = lambda line, iv: _thr_calls.append((line, iv))
        got = fd._cb_request("/yc/queryTree", {"locale": "zh_CN"}, method="POST")
        check("L1", "中债-teardown地板走CB_MIN_INTERVAL",
              got == b"legacy-bytes" and ("cb", 0.7) in _thr_calls
              and ("cb", 1.5) not in _thr_calls,
              "调用点引用 CB_MIN_INTERVAL（临时 0.7 生效），非东财 TT_MIN_INTERVAL")

        class _P403:
            returncode, stdout, stderr = 0, b"<html>forbidden</html>\n403", b""

        _sp.run = lambda *a, **k: _P403()
        try:
            fd._cb_request("/yc/queryTree", {"locale": "zh_CN"}, method="POST")
            check("L1", "V0.8.3中债-teardown403应抛未抛", False)
        except fd.RiskControlError as e:
            check("L1", "V0.8.3中债-teardown403不当正文",
                  "403" in str(e) and fd._cbreak_state["cb"][0] >= 1)
    finally:
        _sp.run = _orig_run
        fd._cross_proc_throttle = _orig_thr
        fd.CB_MIN_INTERVAL = _orig_cbiv
        _fresh_coordination()
        _fresh_coordination()
    # 复权净值：无分红区间 == 单位净值比
    recs = [{"date": f"2026-01-0{i}", "nav": v, "dividend": 0}
            for i, v in enumerate([1.0, 1.1, 0.99], start=1)]
    adj = fd.adjusted_nav(recs)
    check("L1", "复权-无分红不变性", abs(adj[-1]["adj_nav"] / adj[0]["adj_nav"] - 0.99) < 1e-9,
          f"adj={adj[-1]['adj_nav']}")

    # 复权净值：分红再投链式（手工账：1.0 → 1.10 → 1.10×(1.05+0.06)/1.10 = 1.11）
    recs = [{"date": "2026-01-01", "nav": 1.00, "dividend": 0},
            {"date": "2026-01-02", "nav": 1.10, "dividend": 0},
            {"date": "2026-01-03", "nav": 1.05, "dividend": 0.06}]
    adj = fd.adjusted_nav(recs)
    check("L1", "复权-分红再投链式(回退路径)", abs(adj[-1]["adj_nav"] - 1.11) < 1e-9,
          f"adj={adj[-1]['adj_nav']}（朴素比值 1.05 会漏 6 个点）")
    expect_raise("L1", "复权-空输入拒绝", lambda: fd.adjusted_nav([]), "无法计算")

    # 复权主路径：链接官方日收益率（坑P6 回应——unitMoney 金额被剥离，equityReturn 才可靠）
    recs = [{"date": "2026-01-01", "nav": 1.00, "daily_return": 0.0},
            {"date": "2026-01-02", "nav": 1.05, "daily_return": 5.0},   # 除息日净值跌但含分红
            {"date": "2026-01-03", "nav": 1.1025, "daily_return": 5.0}]
    adj = fd.adjusted_nav(recs)
    check("L1", "复权-官方收益率链式(主路径)", abs(adj[-1]["adj_nav"] - 1.1025) < 1e-9,
          f"adj={adj[-1]['adj_nav']}（净值序列体现的是总回报而非 nav 比）")

    # 分红文本解析：每10份派0.5元 → 每份0.05；拆分事件 → 0；金额被剥离 → 0
    check("L1", "分红解析-每10份换算", abs(fd._div_from_unit_money("分红：每10份派现金0.5元") - 0.05) < 1e-9)
    check("L1", "分红解析-每份形态", abs(fd._div_from_unit_money("每份派现金0.0100元") - 0.01) < 1e-9)
    check("L1", "V0.8.3分红解析-E3裸数值形态",
          fd._div_from_unit_money("0.01") == 0.01 and fd._div_from_unit_money(0.05) == 0.05)
    check("L1", "分红解析-拆分不记为现金", fd._div_from_unit_money("拆分：每份基金份额折算份") == 0.0)
    check("L1", "分红解析-金额被剥离记0", fd._div_from_unit_money("分红：每10份派现金元") == 0.0)

    # 日期降序防线（坑P7）：E3 降序序列喂指标必须拒绝
    snap_bad = {"nav_history": [{"date": d, "nav": v, "dividend": 0} for d, v in
                                [("2026-01-03", 1.0), ("2026-01-02", 1.2), ("2026-01-01", 1.0)]]}
    expect_raise("L1", "坑P7-降序序列拒绝", lambda: fd.fund_metrics(snap_bad), "非升序")
    expect_raise("L1", "坑P7-降序序列拒绝(定投)",
                 lambda: fd.fund_dca_backtest(snap_bad), "非升序")

    # 空查询拒绝
    expect_raise("L1", "空查询拒绝", lambda: fd.norm_fund_code("   "), "查询为空")

    # 定投回测 3 期手工对账：净值 1.0/0.5/1.0，每月 1000 → 份额 4000，期末 4000
    snap = {"nav_history": [{"date": f"2026-0{i}-01", "nav": v, "dividend": 0}
                            for i, v in enumerate([1.0, 0.5, 1.0], start=1)]}
    dca = fd.fund_dca_backtest(snap, amount=1000, freq="monthly")
    check("L1", "定投-3期手工对账",
          dca["periods"] == 3 and abs(dca["final_value"] - 4000.0) < 1e-6
          and abs(dca["return_pct"] - 100 / 3) < 1e-3,
          f"投入{dca['total_invested']}→市值{dca['final_value']}, 收益{dca['return_pct']}%")
    expect_raise("L1", "定投-非法频率拒绝",
                 lambda: fd.fund_dca_backtest(snap, freq="daily"), "monthly/weekly")
    weekly_snap = {"nav_history": [
        {"date": d, "nav": 1.0, "daily_return": 0.0, "dividend": 0}
        for d in ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
                  "2026-01-09", "2026-01-12")]}
    weekly = fd.fund_dca_backtest(weekly_snap, amount=1000, freq="weekly")
    check("L1", "定投-每周按ISO周去重",
          weekly["periods"] == 2 and weekly["total_invested"] == 2000.0
          and weekly["first_invest"] == "2026-01-05"
          and weekly["last_invest"] == "2026-01-12",
          f"6个净值日→{weekly['periods']}期，{weekly['first_invest']}~{weekly['last_invest']}")

    # 指标：构造序列 [1.0,1.2,0.6,0.9] → 最大回撤 -50%
    snap = {"nav_history": [{"date": f"2026-01-0{i}", "nav": v, "dividend": 0}
                            for i, v in enumerate([1.0, 1.2, 0.6, 0.9], start=1)]}
    m = fd.fund_metrics(snap)
    check("L1", "指标-最大回撤构造验证", abs(m["max_drawdown_pct"] + 50.0) < 1e-6,
          f"dd={m['max_drawdown_pct']}% 窗口={m['max_drawdown_window']}")
    check("L1", "指标-字段完备",
          all(m.get(k) is not None for k in
              ["total_return_pct", "annualized_return_pct", "sharpe", "calmar"]))
    m30 = fd.fund_metrics({"nav_history": [
        {"date": "2026-01-01", "nav": 1.0, "daily_return": 0.0, "dividend": 0},
        {"date": "2026-01-31", "nav": 1.01, "daily_return": None, "dividend": 0}]})
    check("L1", "指标-不足一年仍复合年化",
          abs(m30["annualized_return_pct"] - 12.8695) < 1e-4,
          f"30天收益1%→年化{m30['annualized_return_pct']}%")

    # 缓存损坏容忍（读完即清理，不留垃圾在用户缓存目录）
    bad = fd.FUND_CACHE_DIR / "snapshots" / "_corrupt_test.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    try:
        check("L1", "缓存-损坏文件视为miss", fd._cache_read("snapshots/_corrupt_test.json", None) is None)
    finally:
        bad.unlink(missing_ok=True)

    class _DJ403:
        status_code = 403
        content = b"forbidden"

        def raise_for_status(self):
            raise AssertionError("403 应先被风控断言拦截")

    _orig_resolve, _orig_get = fd.resolve_fund_code, fd.ttfund_get
    fd._cbreak_state["tt"] = [0, 0.0]
    try:
        fd.resolve_fund_code = lambda q: q
        fd.ttfund_get = lambda *a, **k: _DJ403()
        expect_raise("L1", "蛋卷-403进入RiskControlError防线",
                     lambda: fd.fund_detail_danjuan("161725"), "D1 蛋卷")
    finally:
        fd.resolve_fund_code, fd.ttfund_get = _orig_resolve, _orig_get

    # ── L6 穿透层单元（零网络）：路由 / 标签清洗 / 注入拦截 ──
    cases = {"920982": "bj920982", "832982": "bj832982", "430047": "bj430047",
             "600519": "sh600519", "000858": "sz000858", "000300": "sz000300",
             "204007": "sh204007"}                       # 默认均为股票上下文；指数另测 kind="index"
    check("L1", "穿透-腾讯前缀路由(92段先于9x+204回购)",
          all(fd._tencent_prefix(k) == v for k, v in cases.items()),
          " ".join(f"{k}→{fd._tencent_prefix(k)}" for k in cases))
    check("L1", "穿透-东财市场号(92北交→0)",
          fd.em_market_code("920982") == 0 and fd.em_market_code("832982") == 0
          and fd.em_market_code("600519") == 1 and fd.em_market_code("000858") == 0)
    tag_cases = {"白酒Ⅲ": "白酒", "计算机设备II": "计算机设备", "食品饮料": "食品饮料",
                 "贵州板块": None, "酿酒概念": None, "HS300_": None,
                 "标准普尔": None, "融资融券": None}
    check("L1", "穿透-标签清洗与停用词",
          all(fd._clean_tag(k) == v for k, v in tag_cases.items()),
          f"白酒Ⅲ→{fd._clean_tag('白酒Ⅲ')} 计算机设备II→{fd._clean_tag('计算机设备II')}")
    expect_raise("L1", "穿透-代码注入拦截",
                 lambda: fd.concept_blocks("60051')('x"), "格式错误")
    check("L1", "穿透-单字符标签产物拒绝", fd._clean_tag("AI") is None,
          "'AI' 被 I+$ 剥成单字符 'A' → None")

    # ── 穿透确定性 + 降级路径（mock，零网络）──
    # fund_list 也 patch（照 473 块惯例）：penetrate→resolve_fund_code 会拉真实清单，
    # 不 patch 则离线套件对 fund.eastmoney.com 偷发一次真实请求（审查毒化复现的存量漏网）。
    _orig = (fd.fund_holdings, fd.fund_snapshot, fd.tencent_quote, fd.concept_blocks,
             fd.fund_list)
    _mk_hold = lambda: [
        {"period": "2026-09-30", "seq": i + 1, "stock_code": c, "stock_name": f"股{i}",
         "nav_pct": 10.0, "shares_wan": 1, "value_wan": 1}
        for i, c in enumerate(["600001", "600002", "600003", "600004"])]
    fd.fund_holdings = lambda q, **k: _mk_hold()
    fd.fund_snapshot = lambda q, **k: {"name": "测试基金"}
    fd.fund_list = lambda **k: [{"code": "161725", "name": "测试白酒基金",
                                 "type": "股票指数", "pinyin": "csbc"}]
    fd.tencent_quote = lambda cs: {c: {"name": c, "price": 1.0, "change_pct": 0.0,
                                       "turnover_pct": 0.0, "pe_ttm": 10.0,
                                       "float_mcap_yi": 1.0, "mcap_yi": 1.0, "pb": 1.0}
                                   for c in cs}
    try:
        # 平局裁决钉子：等权重双主题（白酒/食品饮料 各40%），两种标签插入序 Top1 恒为短词
        tops = []
        for order in (["白酒", "食品饮料", "白酒Ⅲ"], ["食品饮料", "白酒"]):
            fd.concept_blocks = lambda code, tags=order: tags
            tops.append(fd.penetrate("161725")["themes"][0][0])
        check("L1", "穿透-平局裁决确定性(短词胜)", all(t == "白酒" for t in tops),
              f"两种插入序 Top1: {tops}")
        fd.concept_blocks = lambda code, **k: ["白酒", "食品饮料", "白酒Ⅲ"]
        th = fd.penetrate("161725")["themes"][:2]
        check("L1", "穿透-平局全序钉死", th == [("白酒", 40.0), ("食品饮料", 40.0)],
              f"themes[:2]={th}（回归为仅按权重排序时必假）")

        # 单股板块失败 → failed_stocks 带权重、不中断、detail 保留
        def _flaky(code, **k):
            if code == "600003":
                raise RuntimeError("mock 风控")
            return ["白酒"]
        fd.concept_blocks = _flaky
        p = fd.penetrate("161725")
        check("L1", "穿透-单股失败降级",
              p["failed_stocks"] and p["failed_stocks"][0]["weight"] == 10.0
              and p["unclassified_weight_pct"] == 10.0 and len(p["detail"]) == 4,
              f"failed={p['failed_stocks'][0]['reason'][:20]} 未分类权重{p['unclassified_weight_pct']}%")

        # nav_pct 缺失 → weight=None、不计入未分类权重
        fd.fund_holdings = lambda q, **k: [
            {"period": "2026-09-30", "seq": 1, "stock_code": "600001", "stock_name": "A",
             "nav_pct": 10.0, "shares_wan": 1, "value_wan": 1},
            {"period": "2026-09-30", "seq": 2, "stock_code": "600002", "stock_name": "B",
             "nav_pct": None, "shares_wan": None, "value_wan": None}]
        fd.concept_blocks = lambda code, **k: ["白酒"]
        p = fd.penetrate("161725")
        check("L1", "穿透-nav缺失降级",
              p["failed_stocks"][0]["weight"] is None and len(p["detail"]) == 1
              and p["unclassified_weight_pct"] == 0.0,
              f"reason={p['failed_stocks'][0]['reason']}")

        # 全部权重缺失 / 无股票持仓 → 显式报错
        fd.fund_holdings = lambda q, **k: [
            {"period": "2026-09-30", "seq": 1, "stock_code": "600001", "stock_name": "A",
             "nav_pct": None, "shares_wan": None, "value_wan": None}]
        expect_raise("L1", "穿透-全权重缺失报错", lambda: fd.penetrate("161725"), "全部重仓股权重缺失")
        fd.fund_holdings = lambda q, **k: []
        expect_raise("L1", "穿透-无股票持仓报错", lambda: fd.penetrate("161725"), "无股票持仓")
    finally:
        (fd.fund_holdings, fd.fund_snapshot, fd.tencent_quote, fd.concept_blocks,
         fd.fund_list) = _orig

    # ── L7 宏观利率层单元（零网络：期限映射 / xlsx fixture 解析 / 未知曲线拒绝）──
    import io as _io
    import zipfile as _zf
    check("L1", "利率-期限码映射",
          fd._term_years("0d") == 0.0 and fd._term_years("3m") == 0.25
          and fd._term_years("2y") == 2.0 and fd._term_years("9m") == 0.75
          and fd._term_years("x") is None)
    _buf = _io.BytesIO()
    with _zf.ZipFile(_buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml",
                   "<worksheet><sheetData>"
                   "<row><c><is><t>日期</t></is></c><c><is><t>标准期限说明</t></is></c>"
                   "<c><is><t>收益率(%)</t></is></c></row>"
                   "<row><c><is><t>2026/11/05</t></is></c><c><is><t>10y</t></is></c>"
                   "<c><is><t>1.6038</t></is></c></row>"
                   "<row><c><is><t>2026/11/05</t></is></c><c><is><t>bad</t></is></c>"
                   "<c><is><t>abc</t></is></c></row>"
                   "</sheetData></worksheet>")
    pts = fd._parse_cb_xlsx(_buf.getvalue())
    check("L1", "利率-中债xlsx解析(inlineStr)",
          len(pts) == 1 and pts[0]["date"] == "2026-11-05"
          and pts[0]["yield_pct"] == 1.6038 and pts[0]["term_years"] == 10.0,
          f"{pts}（坏行已跳过）")
    _orig_tree = fd.cb_curve_tree                        # patch 掉网络依赖，保持离线纯度
    fd.cb_curve_tree = lambda **k: {"中债国债收益率曲线": "fake_id"}
    try:
        expect_raise("L1", "利率-未知曲线拒绝(离线)",
                     lambda: fd._cb_curve_id("不存在曲线xyz"), "未知曲线")
        check("L1", "利率-种子表直取", fd._cb_curve_id("treasury")
              == ("中债国债收益率曲线", "2c9081e50a2f9606010a3068cae70001"))
        check("L1", "利率-树模糊唯一命中",
              fd._cb_curve_id("国债收益率曲线") == ("中债国债收益率曲线", "fake_id"))
        fake_tree = {"中债企业债收益率曲线(AA＋)": "zz_not_hex_id!!"}
        fd.cb_curve_tree = lambda **k: fake_tree
        expect_raise("L1", "利率-树返回非法ID拒绝",
                     lambda: fd._cb_validated_id("中债企业债收益率曲线(AA＋)"), "32 位十六进制")
        fd.cb_curve_tree = lambda **k: {"中债企业债收益率曲线(AA＋)": 12345}   # 非字符串 ID
        expect_raise("L1", "利率-非字符串ID拒绝",
                     lambda: fd._cb_validated_id("中债企业债收益率曲线(AA＋)"), "曲线 ID 非法")
    finally:
        fd.cb_curve_tree = _orig_tree

    # ── V0.4.0 A1 XIRR / P18 归一化修复 / B1~B3 评测函数（零网络）──
    from datetime import date as _date, timedelta as _td
    r = fd._xirr([("2024-01-01", -1000.0), ("2024-12-31", -1000.0), ("2025-12-31", 2400.0)])
    check("L1", "XIRR-分期现金流锚点12.79%", r is not None and abs(r - 0.127930) < 0.0002,
          f"{r * 100:.4f}%")
    check("L1", "XIRR-清零无解返None",
          fd._xirr([("2024-01-01", -1000.0), ("2024-12-31", -1000.0),
                    ("2025-12-31", 0.0)]) is None)
    nav3 = [{"date": "2024-01-01", "nav": 1.0, "daily_return": None, "dividend": 0},
            {"date": "2024-12-31", "nav": 1.0, "daily_return": None, "dividend": 0},
            {"date": "2025-11-30", "nav": 1.2, "daily_return": None, "dividend": 0}]
    dca = fd.fund_dca_backtest({"nav_history": nav3}, amount=1000.0, freq="monthly")
    check("L1", "DCA-XIRR集成(月投3期)",
          dca["periods"] == 3 and dca["irr_xirr_pct"]
          and abs(dca["irr_xirr_pct"] - 13.59) < 0.15
          and dca["naive_annualized_pct"] < dca["irr_xirr_pct"],
          f"XIRR={dca['irr_xirr_pct']}% naive={dca['naive_annualized_pct']}%")

    recs = fd._echarts_to_records({"categories": ["2025-09-30", "2025-12-31"],
                                   "series": [{"y": 1.47, "mom": "11.95%"},
                                              {"y": 2.88, "mom": "96.28%"}]})
    check("L1", "归一化-行式{y,mom}修复(P18)",
          recs[1] == {"_date": "2025-12-31", "y": 2.88, "mom": "96.28%"}, str(recs))
    sh = fd._scale_history({"categories": ["2025-09-30"],
                            "series": [{"y": 1.47, "mom": "11.95%"}]})
    check("L1", "归一化-规模史恢复scale_yi",
          sh[0]["scale_yi"] == 1.47 and sh[0]["_date"] == "2025-09-30"
          and sh[0]["mom_pct"] == "11.95%", str(sh))
    check("L1", "归一化-旧列式(规模键)兼容",
          fd._scale_history({"categories": ["2025-09-30"],
                             "series": [{"name": "规模", "data": [2.5]}]})[0]["scale_yi"] == 2.5)
    pos = fd._xy_pairs_to_records([[1791475200000, 92.83], [1791734400000, 93.75]],
                                  "position_pct")
    check("L1", "归一化-时序对修复(P18)",
          pos[0]["position_pct"] == 92.83 and pos[1]["date"] > pos[0]["date"], str(pos[:1]))
    check("L1", "归一化-表格形态分流不误伤",
          fd._xy_pairs_to_records([["a", "b"], [1, 2]], "v") == [])
    bc = fd._benchmark_curves([{"name": "沪深300",
                                "data": [[1704067200000, 0.0], [1704153600000, -1.2]]},
                               {"name": "x", "data": []}])
    check("L1", "grandTotal曲线解析", len(bc) == 1 and bc[0]["name"] == "沪深300"
          and bc[0]["points"][1]["return_pct"] == -1.2, str(bc))

    _d0 = _date(2026, 5, 6)
    _dts = [(_d0 + _td(days=i)).isoformat() for i in range(120)]
    _bd = [0.0] + [((i * 37) % 23 - 11) / 1000.0 for i in range(1, 120)]
    _fd_r = [0.0] + [1.2 * b + 0.0003 for b in _bd[1:]]
    _c, _bc = 0.0, []
    for _x in _bd:
        _c += _x
        _bc.append(_c * 100.0)
    _curve = [{"name": "沪深300",
               "points": [{"date": _dts[i], "return_pct": _bc[i]} for i in range(120)]}]
    _nav, _n = [], 1.0
    for _i, _rr in enumerate(_fd_r):
        _n *= (1 + _rr)
        _nav.append({"date": _dts[_i], "nav": _n, "daily_return": _rr * 100, "dividend": 0})
    _snap = {"nav_history": _nav, "benchmark_curves": _curve}
    _rp = fd.relative_performance(_snap)
    check("L1", "相对表现-β恢复1.2(百分号口径)",
          abs(_rp["beta"] - 1.2) < 0.01 and _rp["correlation"] > 0.999,
          f"β={_rp['beta']} corr={_rp['correlation']}")
    expect_raise("L1", "相对表现-未知基准显式报错",
                 lambda: fd.relative_performance(_snap, benchmark="中证500"), "沪深300")

    pt = fd.position_trend({"stock_position_history": [
        {"date": "2025-03-31", "position_pct": 80}, {"date": "2025-06-30", "position_pct": 82},
        {"date": "2025-09-30", "position_pct": 92}, {"date": "2025-12-31", "position_pct": 94}]})
    check("L1", "仓位解读-加减仓标签", "加仓" in pt["label"] and pt["latest_pct"] == 94.0,
          pt["label"])
    expect_raise("L1", "仓位解读-数据缺失报错",
                 lambda: fd.position_trend({"stock_position_history": []}), "不足 2 期")
    _pr, _pv = [], 30
    for _m in range(22):                              # 跨 >1 年，保证 lookback 对照存在
        _yy, _mm = 2025 + _m // 12, _m % 12 + 1
        for _d in range(3):
            _pr.append({"date": f"{_yy}-{_mm:02d}-{_d * 2 + 1:02d}", "percentile": _pv})
            _pv += 1
    pr = fd.peer_rank({"peer_rank_history": _pr})
    check("L1", "同类百分位-解读", pr["latest_percentile"] == _pv - 1
          and "同类前列" in pr["label"] and pr["delta_pp"] is not None,
          f"最新{pr['latest_percentile']} 一年前{pr['year_ago_percentile']} Δ{pr['delta_pp']}pp")
    check("L1", "metrics-现申购费标注(V0.7改名)", fd.fund_metrics(
        {"nav_history": _nav, "fees": {"current_rate": "0.12"}})["subscription_fee_pct"] == 0.12
        and fd.fund_metrics({"nav_history": _nav})["subscription_fee_pct"] is None)

    # ── V0.4.1 审计修复项守卫（P-A~P-E + 复核遗留）──
    expect_raise("L1", "DCA-amount非正拦截",
                 lambda: fd.fund_dca_backtest({"nav_history": nav3}, amount=0),
                 "amount 必须为正数")
    expect_raise("L1", "peer_rank-乱序拒绝",
                 lambda: fd.peer_rank({"peer_rank_history": [
                     {"date": "2026-02-01", "percentile": 50},
                     {"date": "2026-01-01", "percentile": 60}]}), "非升序")
    _flat_bench = [{"name": "沪深300",
                    "points": [{"date": _dts[i], "return_pct": 0.0} for i in range(120)]}]
    _rp_flat = fd.relative_performance({"nav_history": _nav, "benchmark_curves": _flat_bench})
    check("L1", "相对表现-基准横盘beta_note",
          _rp_flat["beta"] is None and _rp_flat["beta_note"]
          and "基准曲线窗口内近乎横盘" in _rp_flat["beta_note"], _rp_flat["beta_note"][:30])
    _neg_bench = [{"name": "沪深300", "points": [
        {"date": "2026-05-06", "return_pct": 0.0}, {"date": "2026-05-07", "return_pct": -100.0},
        {"date": "2026-05-08", "return_pct": -101.0}]}]
    expect_raise("L1", "相对表现-基准归零守卫",
                 lambda: fd.relative_performance(
                     {"nav_history": _nav, "benchmark_curves": _neg_bench},
                     min_overlap=1), "数值异常")
    _nav_short = [{"date": "2026-01-02", "nav": 1.0, "daily_return": None, "dividend": 0},
                  {"date": "2026-01-20", "nav": 1.05, "daily_return": None, "dividend": 0}]
    _dca_short = fd.fund_dca_backtest({"nav_history": _nav_short}, amount=1000.0)
    check("L1", "DCA-短窗口警示note",
          _dca_short["short_window_note"] and "外推" in _dca_short["short_window_note"]
          and _dca_short["irr_xirr_pct"] and 100 < _dca_short["irr_xirr_pct"] < 300,
          f"note={_dca_short['short_window_note'][:24]} irr={_dca_short['irr_xirr_pct']}%")
    _nav_dead = [{"date": "2026-01-02", "nav": 1.0, "daily_return": None, "dividend": 0},
                 {"date": "2026-06-20", "nav": 0.001, "daily_return": None, "dividend": 0}]
    _dca_dead = fd.fund_dca_backtest({"nav_history": _nav_dead}, amount=1000.0)
    check("L1", "DCA-清零xirr_note含上限",
          _dca_dead["irr_xirr_pct"] is None and "1000%" in _dca_dead["xirr_note"],
          f"irr={_dca_dead['irr_xirr_pct']} note={(_dca_dead['xirr_note'] or '')[:30]}")
    check("L1", "XIRR求解域上限引用常量", fd.XIRR_MAX_RATE == 10.0
          and f"{fd.XIRR_MAX_RATE * 100:.0f}%" == "1000%")

    # fund_holdings 动态列映射（P-C）：插列变体不静默错列
    class _FakeResp:
        status_code = 200
        def __init__(self, text):
            self.content = text.encode("utf-8")
            self.text = text
        def raise_for_status(self):
            return None
    _th_std = ("<thead><tr><th>序号</th><th>股票代码</th><th>股票名称</th><th>最新价</th>"
               "<th>涨跌幅</th><th>资讯</th><th>占净值比例</th><th>持股数（万股）</th>"
               "<th>持仓市值（万元）</th></tr></thead>")
    _th_ins = ("<thead><tr><th>序号</th><th>股票代码</th><th>股票名称</th><th>最新价</th>"
               "<th>涨跌幅</th><th>涨跌额</th><th>资讯</th><th>占净值比例</th>"
               "<th>持股数（万股）</th><th>持仓市值（万元）</th></tr></thead>")
    _row_std = ("<tbody><tr><td>1</td><td>600519</td><td>贵州茅台</td><td>1500.0</td>"
                "<td>1.0%</td><td>公告</td><td>9.95</td><td>100</td><td>150000</td></tr></tbody>")
    _row_ins = ("<tbody><tr><td>1</td><td>600519</td><td>贵州茅台</td><td>1500.0</td>"
                "<td>1.0%</td><td>15.0</td><td>公告</td><td>9.95</td><td>100</td>"
                "<td>150000</td></tr></tbody>")
    def _jjcc_resp(thead, tbody):
        return _FakeResp('var apidata={ content:"<h4>截止至：<font>2026-09-30</font></h4>'
                         '<table>' + thead + tbody + '</table>", arryear:[2026]};')
    _orig_ttf, _orig_cache_r, _orig_cache_w = fd.ttfund_get, fd._cache_read, fd._cache_write
    _orig_fl_444 = fd.fund_list          # 本块 resolve 链路会拉清单——patch 防触网/防 mock 串味
    fd._cache_read = lambda rel, max_age_hours=None: None
    fd._cache_write = lambda rel, payload: None
    try:
        fd.fund_list = lambda **k: [{"code": "161725", "name": "测试基金",
                                     "type": "混合型-偏股", "pinyin": "cs"}]
        fd.ttfund_get = lambda url, **k: _jjcc_resp(_th_std, _row_std)
        h_std = fd.fund_holdings("161725")
        fd.ttfund_get = lambda url, **k: _jjcc_resp(_th_ins, _row_ins)
        h_ins = fd.fund_holdings("161725")
        check("L1", "持仓-动态列映射(插列不变错)",
              h_std and h_ins and h_std[0]["nav_pct"] == h_ins[0]["nav_pct"] == 9.95
              and h_ins[0]["stock_code"] == "600519" and h_ins[0]["value_wan"] == 150000.0,
              f"std={h_std[0]['nav_pct']} ins={h_ins[0]['nav_pct']}")
        fd.ttfund_get = lambda url, **k: _jjcc_resp(
            _th_std.replace("<th>占净值比例</th>", ""), _row_std)
        expect_raise("L1", "持仓-缺列显式报错",
                     lambda: fd.fund_holdings("161725"), "缺 '占净值'")
        # snapshot present-unparsed（P18 关联）：raw 存在但形态解析失败进 missing
        _pz = ('var fS_name="测试基金";var fS_code="161725";var ishb="0";'
               'var Data_netWorthTrend=[{"x":1767225600000,"y":1.0,"equityReturn":"0"}];'
               'var Data_ACWorthTrend=[];var Data_grandTotal=[];'
               'var Data_rateInSimilarPersent=[[1767225600000,50]];'
               'var Data_fundSharesPositions=[["坏行"],[1,2,3]];'
               'var Data_fluctuationScale={"categories":["2025-09-30"],'
               '"series":[{"y":1.5}]};' + ("/*" + "x" * 1200 + "*/"))
        fd.ttfund_get = lambda url, **k: _FakeResp(_pz)
        _snap_fake = fd.fund_snapshot("161725")
        check("L1", "快照-present,unparsed进missing",
              "Data_fundSharesPositions(present,unparsed)" in _snap_fake["missing_datasets"]
              and _snap_fake["peer_rank_history"][0]["percentile"] == 50
              and _snap_fake["scale_history"][0]["scale_yi"] == 1.5,
              str(_snap_fake["missing_datasets"]))
    finally:
        fd.ttfund_get, fd._cache_read, fd._cache_write = \
            (_orig_ttf, _orig_cache_r, _orig_cache_w)
        fd.fund_list = _orig_fl_444

    # penetrate 不再为名字拉快照（P-A）——fund_list 也 patch，与真实清单缓存隔离
    _orig = (fd.fund_holdings, fd.tencent_quote, fd.concept_blocks, fd.fund_snapshot,
             fd.fund_list)
    def _bomb(*a, **k):
        raise AssertionError("penetrate 不应再调用 fund_snapshot（名字应从 fund_list 直查）")
    try:
        fd.fund_list = lambda **k: [{"code": "161725", "name": "测试白酒基金",
                                     "type": "股票指数", "pinyin": "csbc"}]
        fd.fund_holdings = lambda q, **k: [
            {"period": "2026-09-30", "seq": 1, "stock_code": "600519", "stock_name": "贵州茅台",
             "nav_pct": 9.95, "shares_wan": 100, "value_wan": 150000}]
        fd.tencent_quote = lambda codes: {"600519": {"name": "贵州茅台", "price": 1500.0,
                                                     "pe_ttm": 20.0, "pb": 8.0,
                                                     "change_pct": 1.0}}
        fd.concept_blocks = lambda code, **k: ["白酒Ⅲ", "酿酒概念"]
        fd.fund_snapshot = _bomb
        p = fd.penetrate("161725")
        check("L1", "穿透-名字零快照(P-A)", p["fund"] == "测试白酒基金"
              and p["themes"] and p["themes"][0][0] == "白酒",
              f"fund={p['fund']} 主题={p['themes'][:2]}")
    finally:
        (fd.fund_holdings, fd.tencent_quote, fd.concept_blocks, fd.fund_snapshot,
         fd.fund_list) = _orig

    # credit_spread 三段报错（P-E）与 beta_note 基金侧横盘——离线分支覆盖
    _orig_cb = fd.cb_yield_curve
    try:
        # 企业债 3.1 / 国债 2.5 → 锚 0.6（方向：企业债−国债；同值 0.0 锚测不出
        # 实现被误反转的方向颠倒——复核建议⑤）
        _mk = lambda y: (lambda term, dates: {"points": [
            {"date": d, "term": term, "yield_pct": y} for d in dates]})
        fd.cb_yield_curve = lambda curve, year=None: (
            _mk(3.1)("3y", ["2026-01-05", "2026-01-06"]) if "企业债" in str(curve)
            else _mk(2.5)("3y", ["2026-01-05", "2026-01-06"]))
        sp = fd.credit_spread("AA", "3y")
        check("L1", "利差-正常算差(方向锚0.6)", len(sp) == 2 and sp[0]["spread_pct"] == 0.6,
              f"{sp[0]}")
        fd.cb_yield_curve = lambda curve, year=None: (
            {"points": [{"date": "2026-01-05", "term": "1y", "yield_pct": 2.0}]}
            if "企业债" in str(curve) else _mk(2.5)("3y", ["2026-01-05"]))
        expect_raise("L1", "利差-企业债期限缺失报错",
                     lambda: fd.credit_spread("AA", "3y"), "企业债曲线")
        fd.cb_yield_curve = lambda curve, year=None: (
            _mk(2.5)("3y", ["2026-01-05"]) if "企业债" in str(curve)
            else {"points": [{"date": "2026-01-05", "term": "1y", "yield_pct": 2.0}]})
        expect_raise("L1", "利差-国债期限缺失报错",
                     lambda: fd.credit_spread("AA", "3y"), "国债曲线")
        fd.cb_yield_curve = lambda curve, year=None: (
            _mk(2.5)("3y", ["2026-01-05"]) if "企业债" in str(curve)
            else _mk(2.5)("3y", ["2026-02-01"]))
        expect_raise("L1", "利差-日期错位报错",
                     lambda: fd.credit_spread("AA", "3y"), "无共同日期")
    finally:
        fd.cb_yield_curve = _orig_cb
    _fund_flat = [{"date": _dts[i], "nav": 1.0, "daily_return": 0.0, "dividend": 0}
                  for i in range(120)]
    _rp_ff = fd.relative_performance({"nav_history": _fund_flat,
                                      "benchmark_curves": _curve})
    check("L1", "相对表现-基金横盘beta_note",
          _rp_ff["beta"] is not None and _rp_ff["correlation"] is None
          and _rp_ff["beta_note"] and "基金净值窗口内近乎横盘" in _rp_ff["beta_note"],
          _rp_ff["beta_note"][:30])

    # ── V0.4.2 渐进加载架构守卫（路由层瘦身/签名覆盖/reference 内嵌）──
    from pathlib import Path as _P
    import ast as _ast
    _repo = _P(__file__).resolve().parent
    _thin = (_repo / "SKILL.md").read_text(encoding="utf-8")
    _ref = (_repo / "reference.md").read_text(encoding="utf-8")
    _thin_b = len(_thin.encode("utf-8"))
    check("L1", "架构-瘦SKILL体积上限", _thin_b < 14500,
          f"{_thin_b}B/上限14500（V0.8.0 四个全收益入口纳入；长文仍移 reference.md）")
    _pub = [n.name for n in _ast.parse((_repo / "fund_data.py").read_text(encoding="utf-8")).body
            if isinstance(n, _ast.FunctionDef) and not n.name.startswith("_")]
    check("L1", "架构-签名速查全覆盖", len(_pub) >= 32 and all(f"| `{n}(" in _thin for n in _pub),
          f"{len(_pub)} 个公开函数")
    check("L1", "架构-reference内嵌与坑区",
          "def fund_snapshot(query: str, force_refresh: bool = False) -> dict:" in _ref
          and "P18" in _ref and "使用示例" in _ref and "不覆盖" in _ref,
          f"{len(_ref.encode('utf-8'))}B")
    # 平台分发必填 frontmatter 字段回归锁（workbuddy 曾因缺字段拒包）
    _fm = _thin.split("---\n")[1]
    _need = ["name:", "display_name:", "display_name_en:", "description:", "description_zh:",
             "description_en:", "author:", "version:"]
    check("L1", "架构-平台必填frontmatter齐全",
          all(f"\n{k}" in _fm or _fm.startswith(k) for k in _need),
          f"缺 {[k for k in _need if f'\n{k}' not in _fm and not _fm.startswith(k)]}")
    check("L1", "架构-路由层零内嵌代码",
          _thin.count("```python") == 1 and "import sys" in _thin,
          f"python 代码块 {_thin.count('```python')} 个（应仅 1 个 import 示例）")

    # ── V0.5 全市场层离线单元（第四线 cninfo + rankhandler 家族）──
    check("L1", "cninfo-enckey确定性(P20)",
          fd._cninfo_enckey(ts=1731000000) == "teu1Gv6Sv0Zz8DY+oqvqxg=="
          and fd._cninfo_enckey().endswith("="),
          "AES-128-CBC(秒级时间戳)→Base64，锚 ts=1731000000")
    _row = ("161725,招商中证白酒指数(LOF)A,ZSZZBJZSLOFA,2026-11-09,0.5859,2.302,"
            "-0.63,-0.31,10.34,3.85,-4.87,-23.77,--,--,-21.02,-30.45,"
            "2008-05-09,--,--,0.15%")
    rr = fd._rank_row(_row, {"161725": "指数型-股票"})
    check("L1", "排行-行解析官方syl四点校准",
          rr["code"] == "161725" and rr["nav"] == 0.5859
          and rr["ret_1m_pct"] == 10.34 and rr["ret_3m_pct"] == 3.85
          and rr["ret_6m_pct"] == -4.87 and rr["ret_1y_pct"] == -23.77
          and rr["ret_ytd_pct"] == -21.02 and rr["ret_since_pct"] == -30.45
          and rr["fee_pct"] == 0.15 and rr["fund_type"] == "指数型-股票"
          and rr["founded"] == "2008-05-09",
          f"1m={rr['ret_1m_pct']} 3m={rr['ret_3m_pct']} 6m={rr['ret_6m_pct']} "
          f"1y={rr['ret_1y_pct']}（字段序对照官方syl，2026-11-10 校准）")
    check("L1", "排行-缺失值'--'归None",
          rr["ret_2y_pct"] is None and rr["ret_3y_pct"] is None)
    check("L1", "排行-短行不炸(缺字段None)",
          fd._rank_row("654321,残缺行,CHQ,,", {})["nav"] is None)

    class _FR:                                    # 内容级风控断言（坑P19）：200+拒绝文案
        def __init__(self, code, body):
            self.status_code, self.content = code, body.encode("utf-8")

    fd._cbreak_file("tt").unlink(missing_ok=True)
    fd._cbreak_state["tt"] = [0, 0.0]
    expect_raise("L1", "排行-内容级风控断言(P19)",
                 lambda: fd.assert_rankhandler(_FR(200, 'var r="无访问权限"'), "t"),
                 "无访问权限")
    check("L1", "排行-风控信号计入熔断计数(未达阈值不落盘)",
          fd._cbreak_state["tt"][0] == 1 and not fd._cbreak_file("tt").exists(),
          str(fd._cbreak_state["tt"]))
    for attempt in (2, 3):
        expect_raise("L1", f"排行-连续内容风控第{attempt}次",
                     lambda: fd.assert_rankhandler(_FR(200, 'var r="无访问权限"'), "t"),
                     "无访问权限" if attempt == 2 else "已自动熔断")
    check("L1", "排行-连续三次内容风控触发熔断",
          fd._COORDINATOR.snapshot("eastmoney")["circuit"] == "OPEN",
          "第三次信号后协调器闸门 OPEN（core 路由；legacy 落盘文件已退位）")
    fd._cbreak_file("tt").unlink(missing_ok=True)
    _fresh_coordination()               # 闸门复位：风控演练不拖累后续东财请求

    fd._cbreak_state["tt"] = [2, time.time()]
    fd.assert_not_risk_control(_FR(500, "server error"), "tt", "t")
    check("L1", "熔断-HTTP500不清零连击",
          fd._cbreak_state["tt"][0] == 2, str(fd._cbreak_state["tt"]))
    fd._cbreak_state["tt"] = [1, time.time()]
    fd.assert_rankhandler(_FR(200, 'var records=["161725,x"];'), "t")
    check("L1", "熔断-业务校验前不清零",
          fd._cbreak_state["tt"][0] == 1, str(fd._cbreak_state["tt"]))

    expect_raise("L1", "排行-参数钳制fund_type",
                 lambda: fd.fund_rank(fund_type="mm"), "fund_type")
    expect_raise("L1", "排行-参数钳制sort", lambda: fd.fund_rank(sort="5y"), "sort")
    expect_raise("L1", "排行-参数钳制top上限", lambda: fd.fund_rank(top=101), "top")
    expect_raise("L1", "排行-参数钳制top下限", lambda: fd.fund_rank(top=0), "top")

    # top=N 服务端单页模式（mock 网络锁回归：曾因 set 下标 100% TypeError 无覆盖）
    class _RR:
        def __init__(self, body):
            self.status_code = 200
            self.content = body.encode("utf-8")

    _body = ('var rankData = {allRecords:2, pagesize:20}, records=['
             '"161725,招商中证白酒指数(LOF)A,ZS,2026-11-09,0.5859,2.302,-0.63,'
             '-0.31,10.34,3.85,-4.87,-23.77,--,--,-21.02,-30.45,2008-05-09,--,--,0.15%",'
             '"005827,易方达蓝筹精选混合,YFDLCJSHH,2026-11-09,1.9,2.1,-0.5,'
             '0.2,5.0,8.0,12.0,15.0,--,--,9.5,20.0,2018-09-05,--,--,1.50%"];')
    _calls = []
    _orig_get, _orig_fl = fd.ttfund_get, fd.fund_list

    def _fake_get(url, params=None, headers=None, **k):
        _calls.append(params.get("pn"))
        return _RR(_body)

    try:
        fd.fund_list = lambda: [{"code": "161725", "type": "指数型-股票"},
                                {"code": "005827", "type": "混合型-偏股"}]
        fd.ttfund_get = _fake_get
        for rel in ("rank/top_gp_ytd_20.json", "rank/top_gp_ytd_50.json",
                    "rank/top_gp_1m_20.json", "rank/top_gp_20.json",
                    "rank/top_gp_50.json"):                 # 预清：序列确定性不受历史缓存影响
            (fd.FUND_CACHE_DIR / rel).unlink(missing_ok=True)
        rk_t = fd.fund_rank(top=20, fund_type="gp", sort="ytd", force_refresh=True)
        check("L1", "排行-top模式正常路径(mock)", len(rk_t) == 2
              and rk_t[0]["code"] == "005827" and rk_t[0]["ret_ytd_pct"] == 9.5
              and rk_t[1]["code"] == "161725" and rk_t[1]["fund_type"] == "指数型-股票",
              f"desc: {[r['code'] for r in rk_t]}")
        rk_a = fd.fund_rank(top=20, fund_type="gp", sort="ytd", ascending=True)
        check("L1", "排行-top模式ascending本地重排+缓存0请求",
              rk_a[0]["code"] == "161725" and len(_calls) == 1,
              f"asc: {[r['code'] for r in rk_a]}，实发 {len(_calls)} 次")
        fd.fund_rank(top=50, fund_type="gp", sort="ytd", force_refresh=True)
        check("L1", "排行-top入缓存键(不同top不串缓存)", len(_calls) == 2,
              f"top=50 触发新拉取，实发 {len(_calls)} 次")
        # 刻意不带 force_refresh：必须走缓存读取路径才有锁力——键含 sort 时
        # top_gp_1m_20.json miss → 第 3 次实发；键回退丢 sort 时命中 call1 的
        # top_gp_20.json → len==2 断言红（force_refresh 会绕读缓存成空锁）
        fd.fund_rank(top=20, fund_type="gp", sort="1m")
        check("L1", "排行-sort入缓存键(跨sort不串服务端窗口)", len(_calls) == 3,
              f"sort=1m 走 miss 路径触发新拉取（窗口随 sc 字段变化），实发 {len(_calls)} 次")
        fd.ttfund_get = lambda *a, **k: _RR(_body.replace("allRecords:2", "allRecords:5086"))
        window_rows = fd._rank_fetch("zs", "1n", 2)
        check("L1", "排行-TopN总数与本页条数分校验",
              len(window_rows) == 2,
              "allRecords=5086、pn=2、实收2行应通过（真实接口口径）")
        fd.ttfund_get = lambda *a, **k: _RR(_body.replace("allRecords:2", "allRecords:25001"))
        expect_raise("L1", "排行-全量超过单页上限仍拒绝截断",
                     lambda: fd._rank_fetch("all", "1nzf", 25000), "校验失败")
    finally:
        fd.ttfund_get, fd.fund_list = _orig_get, _orig_fl
        for rel in ("rank/top_gp_ytd_20.json", "rank/top_gp_ytd_50.json",
                    "rank/top_gp_1m_20.json"):
            (fd.FUND_CACHE_DIR / rel).unlink(missing_ok=True)   # mock 数据不入真缓存

    # 熔断成功语义：业务校验成功才清零，结构异常不得清零
    try:
        fd.ttfund_get = _fake_get
        fd._cbreak_state["tt"] = [1, time.time()]
        fd._rank_fetch("gp", "1nzf", 2)
        check("L1", "熔断-业务校验成功清零",
              fd._cbreak_state["tt"] == [0, 0.0], str(fd._cbreak_state["tt"]))
        fd._cbreak_state["tt"] = [2, time.time()]
        fd.ttfund_get = lambda *a, **k: _RR("var no_records_here")
        expect_raise("L1", "熔断-排行结构异常拒绝",
                     lambda: fd._rank_fetch("gp", "1nzf", 2), "返回空")
        check("L1", "熔断-结构异常不清零",
              fd._cbreak_state["tt"][0] == 2, str(fd._cbreak_state["tt"]))
    finally:
        fd.ttfund_get = _orig_get

    # E2 风控页/结构异常不触发分拆降级
    class _E2Resp:
        def __init__(self, text):
            self.status_code = 200
            self.content = text.encode("utf-8")

        def raise_for_status(self):
            return None

    def _no_degrade(*a, **k):
        raise AssertionError("E2 异常路径不得调用分拆降级")

    _orig_resolve_e2, _orig_get_e2, _orig_deg_e2 = (
        fd.resolve_fund_code, fd.ttfund_get, fd._snapshot_degraded)
    try:
        fd.resolve_fund_code = lambda q: q
        fd._snapshot_degraded = _no_degrade
        fd._cbreak_file("tt").unlink(missing_ok=True)
        fd._cbreak_state["tt"] = [0, 0.0]
        fd.ttfund_get = lambda *a, **k: _E2Resp("无访问权限" + "x" * 1100)
        expect_raise("L1", "E2-内容风控页不降级",
                     lambda: fd.fund_snapshot("161725", force_refresh=True), "无访问权限")
        fd.ttfund_get = lambda *a, **k: _E2Resp("var unrelated=1;" + "x" * 1100)
        expect_raise("L1", "E2-结构异常不降级",
                     lambda: fd.fund_snapshot("161725", force_refresh=True), "缺少关键变量")
        check("L1", "E2-结构异常不清零风控连击",
              fd._cbreak_state["tt"][0] == 1, str(fd._cbreak_state["tt"]))
        fd.ttfund_get = lambda *a, **k: _E2Resp("<html>bad jump</html>")
        expect_raise("L1", "E2-小型非空畸形体不降级",
                     lambda: fd.fund_snapshot("161725", force_refresh=True), "缺少关键变量")
        fd.ttfund_get = lambda *a, **k: _E2Resp("  \n")
        expect_raise("L1", "E2-纯空白非空体不降级",
                     lambda: fd.fund_snapshot("161725", force_refresh=True), "缺少关键变量")
        fd._snapshot_degraded = lambda *a, **k: {"_meta": {"source": "degraded(mock)"}}
        fd.ttfund_get = lambda *a, **k: _E2Resp("")
        empty = fd.fund_snapshot("161725", force_refresh=True)
        check("L1", "E2-真正空体允许降级",
              empty["_meta"]["source"] == "degraded(mock)", empty["_meta"]["source"])
    finally:
        fd.resolve_fund_code, fd.ttfund_get, fd._snapshot_degraded = (
            _orig_resolve_e2, _orig_get_e2, _orig_deg_e2)
        fd._cbreak_file("tt").unlink(missing_ok=True)

    # 降级链与穿透必须原样上抛风控，不得伪装成局部数据缺失
    _orig_deg_parts = (fd.fund_nav_history, fd.fund_holdings, fd.fund_detail_danjuan)
    try:
        fd.fund_nav_history = lambda *a, **k: [
            {"date": "2026-01-01", "nav": 1.0, "daily_return_pct": 0.0, "dividend": None}]
        for branch in ("holdings", "danjuan"):
            for exc_type in (fd.RiskControlError, fd.CircuitOpenError):
                msg = f"{branch}-{exc_type.__name__}"
                fd.fund_holdings = lambda *a, **k: []
                fd.fund_detail_danjuan = lambda *a, **k: {"name": "测试基金"}
                if branch == "holdings":
                    fd.fund_holdings = lambda *a, _msg=msg, **k: (_ for _ in ()).throw(
                        exc_type(_msg))
                else:
                    fd.fund_detail_danjuan = lambda *a, _msg=msg, **k: (_ for _ in ()).throw(
                        exc_type(_msg))
                expect_raise("L1", f"降级链-{msg}不吞异常",
                             lambda: fd._snapshot_degraded("161725"), msg)
    finally:
        fd.fund_nav_history, fd.fund_holdings, fd.fund_detail_danjuan = _orig_deg_parts

    _orig_pen_parts = (fd.resolve_fund_code, fd.fund_list, fd.fund_holdings,
                       fd.tencent_quote, fd.concept_blocks)
    try:
        fd.resolve_fund_code = lambda q: q
        fd.fund_list = lambda: [{"code": "161725", "name": "测试基金"}]
        fd.fund_holdings = lambda *a, **k: [
            {"period": "2026-09-30", "stock_code": "600519", "stock_name": "贵州茅台",
             "nav_pct": 10.0}]
        fd.tencent_quote = lambda *a, **k: {}
        for exc_type in (fd.RiskControlError, fd.CircuitOpenError):
            msg = f"板块-{exc_type.__name__}"
            fd.concept_blocks = lambda *a, _msg=msg, **k: (_ for _ in ()).throw(
                exc_type(_msg))
            expect_raise("L1", f"穿透-{msg}不吞异常",
                         lambda: fd.penetrate("161725"), msg)
    finally:
        fd.resolve_fund_code, fd.fund_list, fd.fund_holdings, fd.tencent_quote, fd.concept_blocks = (
            _orig_pen_parts)

    # 宏观公开函数各自确认成功；通用查询层不得提前清零
    _orig_dc, _orig_rw_dc = fd._dc_query, (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd._dc_query = lambda *a, **k: ([{
            "SOLAR_DATE": "2026-11-12", "EMM00588704": 1.4, "EMM00166466": 1.6}], False)
        fd._cbreak_state["tt"] = [1, time.time()]
        fd.treasury_yield_daily(days=1)
        check("L1", "宏观-国债完整结果清零",
              fd._cbreak_state["tt"] == [0, 0.0], str(fd._cbreak_state["tt"]))
        fd._dc_query = lambda *a, **k: ([{
            "REPORT_DATE": "2026-11-12", "REPORT_PERIOD": "1年(1Y)",
            "MARKET": "上海银行同业拆借市场", "IR_RATE": 1.5, "CHANGE_RATE": 0.0}], False)
        fd._cbreak_state["tt"] = [1, time.time()]
        fd.shibor_history(period="1年(1Y)", days=1)
        check("L1", "宏观-SHIBOR完整结果清零",
              fd._cbreak_state["tt"] == [0, 0.0], str(fd._cbreak_state["tt"]))
        fd._dc_query = lambda *a, **k: ([{"REPORT_DATE": "2026-11-12"}] * 5, False)
        fd._cbreak_state["tt"] = [1, time.time()]
        fd.benchmark_rate_history()
        check("L1", "宏观-基准利率完整结果清零",
              fd._cbreak_state["tt"] == [0, 0.0], str(fd._cbreak_state["tt"]))
    finally:
        fd._dc_query = _orig_dc
        fd._cache_read, fd._cache_write = _orig_rw_dc

    class _DCResp:
        status_code = 200
        content = b"{}"

        def __init__(self, rows):
            self._rows = rows

        def json(self):
            return {"success": True, "result": {"data": self._rows}}

        def raise_for_status(self):
            return None

    _orig_get_dc, _orig_write_dc, _orig_read_dc = fd.ttfund_get, fd._cache_write, fd._cache_read
    _dc_writes = []
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda rel, payload: _dc_writes.append(rel)
        fd._cbreak_state["tt"] = [2, time.time()]
        fd.ttfund_get = lambda *a, **k: _DCResp([{"REPORT_DATE": "2026-11-12"}] * 4)
        expect_raise("L1", "宏观-基准利率不足拒绝",
                     lambda: fd.benchmark_rate_history(), "仅 4 条")
        check("L1", "宏观-业务校验前不清零",
              fd._cbreak_state["tt"][0] == 2, str(fd._cbreak_state["tt"]))
        check("L1", "宏观-业务异常不写缓存", not _dc_writes, str(_dc_writes))
    finally:
        fd.ttfund_get, fd._cache_write, fd._cache_read = _orig_get_dc, _orig_write_dc, _orig_read_dc

    # 宏观纯缓存命中正常返回，但不能证明上游风控已解除
    _orig_macro_cache_parts = (fd._cache_read, fd.ttfund_get)
    try:
        def _macro_cache(rel, max_age_hours=None):
            if rel.startswith("macro/tsy_"):
                return [{"SOLAR_DATE": "2026-11-12", "EMM00588704": 1.4, "EMM00166466": 1.6}]
            if rel.startswith("macro/shibor_"):
                return [{"REPORT_DATE": "2026-11-12", "REPORT_PERIOD": "1年(1Y)",
                         "MARKET": "上海银行同业拆借市场", "IR_RATE": 1.5, "CHANGE_RATE": 0.0}]
            if rel == "macro/deposit_rate_all.json":
                return [{"REPORT_DATE": "2026-11-12"}] * 5
            return None

        fd._cache_read = _macro_cache
        fd.ttfund_get = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("宏观缓存测试不得发网络请求"))
        for name, fn in (("国债", lambda: fd.treasury_yield_daily(days=1)),
                         ("SHIBOR", lambda: fd.shibor_history(period="1年(1Y)", days=1)),
                         ("基准利率", fd.benchmark_rate_history)):
            fd._cbreak_state["tt"] = [2, time.time()]
            fn()
            check("L1", f"宏观-{name}纯缓存命中不清零",
                  fd._cbreak_state["tt"][0] == 2, str(fd._cbreak_state["tt"]))
    finally:
        fd._cache_read, fd.ttfund_get = _orig_macro_cache_parts

    # SHIBOR 全期限缓存键继续沿用 days×8，兼容已有缓存
    _orig_shibor_parts = (fd._cache_read, fd._cache_write, fd.ttfund_get)
    _shibor_reads, _shibor_writes = [], []
    try:
        def _old_shibor_cache(rel, max_age_hours=None):
            _shibor_reads.append(rel)
            if rel == "macro/shibor_all_240.json":
                return [{"REPORT_DATE": "2026-11-12", "REPORT_PERIOD": "隔夜(O/N)",
                         "MARKET": "上海银行同业拆借市场", "IR_RATE": 1.4, "CHANGE_RATE": 0.0}]
            return None

        fd._cache_read = _old_shibor_cache
        fd._cache_write = lambda rel, payload: _shibor_writes.append(rel)
        fd.ttfund_get = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("SHIBOR 旧缓存兼容测试不得发网络请求"))
        fd._cbreak_state["tt"] = [2, time.time()]
        rows = fd.shibor_history(period=None, days=30)
        check("L1", "SHIBOR-全期限旧缓存键兼容",
              rows and _shibor_reads == ["macro/shibor_all_240.json"]
              and not _shibor_writes and fd._cbreak_state["tt"][0] == 2,
              f"reads={_shibor_reads} writes={_shibor_writes}")
    finally:
        fd._cache_read, fd._cache_write, fd.ttfund_get = _orig_shibor_parts

    # jbgk 真实成功 + 公司缓存命中应清零；直接公司ID纯缓存命中不清零
    class _CompanyResp:
        status_code = 200
        content = '<a href="https://fund.eastmoney.com/company/80065113.html">基金公司</a>'.encode()

    _orig_company_parts = (fd.resolve_fund_code, fd.ttfund_get, fd.fund_new_issues, fd._cache_read)
    try:
        fd.resolve_fund_code = lambda q: q
        fd.fund_new_issues = lambda: []
        fd._cache_read = lambda *a, **k: {"company_id": "80065113", "name": "测试公司"}
        fd.ttfund_get = lambda *a, **k: _CompanyResp()
        fd._cbreak_state["tt"] = [2, time.time()]
        company = fd.fund_company("161725")
        check("L1", "公司-jbgk成功且缓存命中清零",
              company["name"] == "测试公司" and fd._cbreak_state["tt"] == [0, 0.0],
              str(fd._cbreak_state["tt"]))
        fd._cbreak_state["tt"] = [2, time.time()]
        fd.fund_company("80065113")
        check("L1", "公司-纯缓存命中不清零",
              fd._cbreak_state["tt"][0] == 2, str(fd._cbreak_state["tt"]))
    finally:
        fd.resolve_fund_code, fd.ttfund_get, fd.fund_new_issues, fd._cache_read = _orig_company_parts

    # 国债实时风险型错误不得落入中债降级
    _orig_rt_parts = (fd._push2_get, fd.cb_yield_curve, fd._cache_read)
    _rt_cb_calls = []
    try:
        fd._cache_read = lambda *a, **k: None
        fd.cb_yield_curve = lambda *a, **k: _rt_cb_calls.append(1)
        for exc_type in (fd.RiskControlError, fd.CircuitOpenError):
            msg = f"国债-{exc_type.__name__}"
            fd._push2_get = lambda *a, _msg=msg, **k: (_ for _ in ()).throw(exc_type(_msg))
            expect_raise("L1", f"国债实时-{msg}不降级",
                         lambda: fd.cn_treasury_realtime(), msg)
        check("L1", "国债实时-风险路径零降级请求", not _rt_cb_calls, str(_rt_cb_calls))
    finally:
        fd._push2_get, fd.cb_yield_curve, fd._cache_read = _orig_rt_parts

    # V0.8.1：中债降级值同过 (0,15) 值域守卫——脏值不得借降级键驻留 300s
    _orig_rt_parts = (fd._push2_get, fd.cb_yield_curve, fd._cache_read, fd._cache_write)
    _deg_writes = []
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda rel, payload: _deg_writes.append(rel)
        fd._push2_get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("push2 桶空"))
        fd.cb_yield_curve = lambda *a, **k: {"points": [
            {"date": "2026-11-21", "term": t, "yield_pct": 20.0 if t == "10y" else 2.0}
            for t in ("2y", "5y", "10y", "30y")]}
        expect_raise("L1", "国债实时-降级脏值拒绝静默",
                     lambda: fd.cn_treasury_realtime(), "收益率异常")
        check("L1", "国债实时-降级守卫零缓存写入", not _deg_writes, str(_deg_writes))
    finally:
        fd._push2_get, fd.cb_yield_curve, fd._cache_read, fd._cache_write = _orig_rt_parts

    # cninfo 451 刷新重试（P20：钥匙时效非风控，不计熔断）
    class _CJ:
        def __init__(self, d):
            self.status_code, self._d, self.content = 200, d, b"{}"

        def json(self):
            return self._d

    _seq = [_CJ({"resultcode": 451, "resultmsg": "token null"}),
            _CJ({"resultcode": 200, "records": [
                {"SECCODE": "300308", "SECNAME": "中际旭创", "F001N": "2424",
                 "F002N": "123", "F003N": "456", "ENDDATE": "2026-09-30 00:00:00"}]})]
    _orig_post, _orig_key = fd.TT_SESSION.post, fd._cninfo_enckey
    try:
        fd._cbreak_state["cninfo"] = [0, 0.0]
        fd.TT_SESSION.post = lambda *a, **k: _seq.pop(0)
        fd._cninfo_enckey = lambda ts=None: "mock="
        recs = fd._cninfo_post("p_sysapi1112", {"rdate": "2026-09-30"}, "mock")
        check("L1", "cninfo-451刷新钥匙重试一次成功(P20)",
              len(recs) == 1 and recs[0]["SECCODE"] == "300308" and not _seq,
              f"耗 {2 - len(_seq)} 次请求成功")
        check("L1", "cninfo-451重试不计熔断",
              fd._cbreak_state["cninfo"] == [0, 0.0], str(fd._cbreak_state["cninfo"]))
        _seq.append(_CJ({"resultcode": 451, "resultmsg": "token null"}))
        _seq.append(_CJ({"resultcode": 451, "resultmsg": "token null"}))
        expect_raise("L1", "cninfo-两次451仍拒报错",
                     lambda: fd._cninfo_post("p_sysapi1112", {}, "mock"), "两次被拒")
    finally:
        fd.TT_SESSION.post, fd._cninfo_enckey = _orig_post, _orig_key

    # ── V0.6 经理画像与专项组装层离线单元 ──
    # 持续性矩阵：构造两类别各3只、收益梯度锁分位与纳入门槛
    _rk_rows = [
        {"code": "000001", "name": "甲债A", "fund_type": "债券型-长债", "ret_1m_pct": 1.0,
         "ret_3m_pct": 2.0, "ret_6m_pct": 3.0, "ret_ytd_pct": 4.0, "ret_1y_pct": 5.0,
         "ret_2y_pct": 6.0, "ret_3y_pct": 7.0},
        {"code": "000002", "name": "乙债B", "fund_type": "债券型-长债", "ret_1m_pct": 2.0,
         "ret_3m_pct": 3.0, "ret_6m_pct": 4.0, "ret_ytd_pct": 5.0, "ret_1y_pct": 6.0,
         "ret_2y_pct": 7.0, "ret_3y_pct": 8.0},
        {"code": "000003", "name": "丙债C", "fund_type": "债券型-长债", "ret_1m_pct": 3.0,
         "ret_3m_pct": 4.0, "ret_6m_pct": 5.0, "ret_ytd_pct": 6.0, "ret_1y_pct": 7.0,
         "ret_2y_pct": 8.0, "ret_3y_pct": 9.0},
        {"code": "100001", "name": "丁股D", "fund_type": "股票型", "ret_1m_pct": 9.0,
         "ret_3m_pct": 9.0, "ret_6m_pct": 9.0, "ret_ytd_pct": 9.0, "ret_1y_pct": 9.0,
         "ret_2y_pct": None, "ret_3y_pct": None},
        {"code": "100002", "name": "戊股E", "fund_type": "股票型", "ret_1m_pct": None,
         "ret_3m_pct": None, "ret_6m_pct": None, "ret_ytd_pct": None,
         "ret_1y_pct": None, "ret_2y_pct": None, "ret_3y_pct": None},
    ]
    _orig_rank = fd.fund_rank
    try:
        fd.fund_rank = lambda **kw: list(_rk_rows)
        rp = fd.rank_persistence(top=10)
        _by = {x["code"]: x for x in rp}
        check("L1", "持续性-类别内分位梯度",
              _by["000003"]["score_pct"] == 100.0 and _by["000001"]["score_pct"] == 33.3
              and _by["000002"]["score_pct"] == 66.7,
              f"甲33.3/乙66.7/丙100（同桶3只全梯度，不高于该值占比口径）")
        check("L1", "持续性-纳入门槛(ytd+1y且≥4周期)",
              "100002" not in _by and "100001" in _by
              and _by["100001"]["covered"] == 5 and _by["100001"]["consistent"] is True,
              f"戊股全None剔除；丁股5周期覆盖全≥75→consistent")
    finally:
        fd.fund_rank = _orig_rank

    # 规模：ENDNAV 元→亿换算锚（209.47亿=20946896763.35 元）
    class _SC:
        status_code = 200
        content = b"{}"

        def json(self):
            return {"Datas": {"ENDNAV": "20946896763.35", "JJGS": "招商基金", "FCODE": "161725"}}

    _orig_get2, _orig_fl2 = fd.ttfund_get, fd.fund_list
    try:
        fd.ttfund_get = lambda *a, **k: _SC()
        fd.fund_list = lambda: [{"code": "161725", "name": "招商中证白酒指数(LOF)A"}]
        sc = fd.fund_scale("161725", force_refresh=True)
        check("L1", "规模-ENDNAV元精确换算",
              sc["scale_yi"] == 209.47 and sc["company"] == "招商基金"
              and sc["name"] == "招商中证白酒指数(LOF)A",
              f"20946896763.35元→{sc['scale_yi']}亿（实测锚分毫不差）")
        fd.ttfund_get = lambda *a, **k: type("_SC2", (), {
            "status_code": 200, "content": b"",
            "json": lambda self: {"Datas": {}}})()
        expect_raise("L1", "规模-缺ENDNAV显式报错",
                     lambda: fd.fund_scale("161725", force_refresh=True), "ENDNAV")
    finally:
        fd.ttfund_get, fd.fund_list = _orig_get2, _orig_fl2
        (fd.FUND_CACHE_DIR / "scale" / "161725.json").unlink(missing_ok=True)  # mock不入真缓存

    # 固收+画像：构造偏债/偏股两形态（快照 mock）
    def _mk_snap(stock, bond):
        pos = [{"date": "2026-01-02", "nav": 1.0, "daily_return": 0.0, "dividend": 0},
               {"date": "2026-07-02", "nav": 0.98, "daily_return": None, "dividend": 0}]
        return {"_meta": {"code": "007300", "version": "x"}, "name": "测试固收+",
                "nav_history": pos,
                "asset_allocation": [{"_date": "2026-09-30", "股票占净比": stock,
                                      "债券占净比": bond, "现金占净比": 5.0}],
                "stock_position_history": [{"date": "2026-07-02", "position_pct": 35.0}]}

    _orig_snap, _orig_metrics = fd.fund_snapshot, fd.fund_metrics
    try:
        fd.fund_snapshot = lambda q, **k: _mk_snap(25.0, 70.0)
        fd.fund_metrics = lambda s: {"max_drawdown_pct": -3.2, "annualized_volatility_pct": 4.1}
        bp = fd.bond_plus_profile("007300")
        check("L1", "固收+-偏债分级与利率敏感标注",
              bp["grade"] == "偏债" and bp["stock_pct"] == 25.0 and bp["bond_pct"] == 70.0
              and bp["drawdown_band"] == "优" and bp["rate_note"] and "cb_yield_curve" in bp["rate_note"],
              f"股25/债70→偏债，回撤3.2→优，利率标注带出")
        fd.fund_snapshot = lambda q, **k: _mk_snap(70.0, 10.0)
        fd.fund_metrics = lambda s: {"max_drawdown_pct": -25.0, "annualized_volatility_pct": 20.0}
        bp2 = fd.bond_plus_profile("007300")
        check("L1", "固收+-偏股分级与回撤带",
              bp2["grade"] == "偏股" and bp2["drawdown_band"] == "弱" and bp2["rate_note"] is None,
              "股70→偏股，回撤25→弱，债<50 无利率标注")
    finally:
        fd.fund_snapshot, fd.fund_metrics = _orig_snap, _orig_metrics

    # QDII 溢价：腾讯+E6 双 mock，溢价数学锚
    _orig_tq, _orig_e6 = fd.tencent_quote, fd.fund_realtime_nav
    try:
        fd.tencent_quote = lambda codes: {c: {"name": "纳指ETF国泰", "price": 2.21} for c in codes}
        fd.fund_realtime_nav = lambda codes: [{"code": c, "name": "纳指ETF国泰", "nav": 1.8056,
                                               "nav_date": "2026-11-06"} for c in codes]
        qp = fd.qdii_premium(["513100"])
        check("L1", "QDII溢价-数学锚与滞后标注",
              qp[0]["premium_pct"] == 22.4 and "T-1/T-2" in qp[0]["note"],
              f"2.21/1.8056-1={qp[0]['premium_pct']}%（实测锚）")
        fd.tencent_quote = lambda codes: {c: {"name": "X", "price": None} for c in codes}
        qp2 = fd.qdii_premium(["513100"])
        check("L1", "QDII溢价-缺价标注不炸",
              qp2[0]["premium_pct"] is None and "缺失" in qp2[0]["note"], str(qp2[0]["note"]))
        expect_raise("L1", "QDII溢价-空列表拒绝", lambda: fd.qdii_premium([]), "不能为空")
    finally:
        fd.tencent_quote, fd.fund_realtime_nav = _orig_tq, _orig_e6

    # 重仓探测：mock K线/持仓/快照，数学锚（基金+2pp 背离→轻微背离带）
    class _KL:
        status_code = 200
        content = b""

        def json(self):
            return {"data": {"sz600519": {"qfqday": [
                ["2026-09-30", "1.0", "100.0", "0", "0", "0"],
                ["2026-10-31", "1.0", "110.0", "0", "0", "0"]]}}}

    _orig_snap3, _orig_hold3, _orig_kline = fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline
    try:
        fd.fund_snapshot = lambda q, **k: {
            "_meta": {"code": "161725", "version": "x"}, "name": "测试白酒",
            "nav_history": [{"date": "2026-09-25", "nav": 1.0, "daily_return": None, "dividend": 0},
                            {"date": "2026-10-01", "nav": 1.02, "daily_return": None, "dividend": 0},
                            {"date": "2026-10-31", "nav": 1.05, "daily_return": None, "dividend": 0}]}
        fd.fund_holdings = lambda q, **k: [
            {"period": "2026-09-30", "stock_code": "600519", "stock_name": "贵州茅台", "nav_pct": 8.04},
            {"period": "2026-09-30", "stock_code": "00700", "stock_name": "腾讯控股", "nav_pct": 3.0}]
        fd._tencent_kline = lambda c, bars=90, force_refresh=False: [
            {"date": "2026-09-25", "close": 100.0},
            {"date": "2026-10-08", "close": 103.0}, {"date": "2026-10-31", "close": 108.0}]
        nd = fd.nav_divergence("161725")
        # 基点=报告期前最后净值1.0→基金 1.05/1.00-1=5.0%；重仓收益=108/100-1=8%
        # 覆盖度仅 8.04% → 归一后背离=5.0-8.0=-3.0pp，但结论必须降级
        check("L1", "重仓探测-背离数学锚与港股跳过",
              nd["fund_return_pct"] == 5.0 and nd["divergence_pp"] == -3.0
              and "覆盖度不足" in nd["verdict"] and nd["stocks_used"] == 1
              and nd["skipped"] and nd["skipped"][0]["code"] == "00700"
              and nd["coverage_pct"] == 8.0,
              f"基{nd['fund_return_pct']}% vs 重仓加权{nd['holdings_weighted_return_pct']}%"
              f"→背离{nd['divergence_pp']}pp；港股00700进skipped")
    finally:
        fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline = _orig_snap3, _orig_hold3, _orig_kline

    # V0.8.3：取数失败不得包装成“持仓分散”的基本面结论
    _orig_pen83 = (fd.resolve_fund_code, fd.fund_list, fd.fund_holdings,
                   fd.tencent_quote, fd.concept_blocks)
    try:
        fd.resolve_fund_code = lambda q: "000001"
        fd.fund_list = lambda: [{"code": "000001", "name": "测试基金"}]
        fd.fund_holdings = lambda q: [
            {"period": "2026-09-30", "stock_code": f"60051{i}",
             "stock_name": f"股票{i}", "nav_pct": 5.0} for i in range(10)]
        fd.tencent_quote = lambda codes: {}
        def _pen_fail83(code):
            raise RuntimeError("mock connection failure")
        fd.concept_blocks = _pen_fail83
        _pen83 = fd.penetrate("000001")
        check("L1", "V0.8.3穿透-全失败显式降级不假分散",
              _pen83.get("degraded") is True
              and "通道不可用" in fd.plain_text_map(_pen83)
              and "持仓分散，无明显单一主题" not in fd.plain_text_map(_pen83),
              f"failed={len(_pen83['failed_stocks'])} degraded={_pen83.get('degraded')}")
    finally:
        (fd.resolve_fund_code, fd.fund_list, fd.fund_holdings,
         fd.tencent_quote, fd.concept_blocks) = _orig_pen83

    # V0.8.3：场外码不得借用同名股票行情；52 号段真实 QDII 不得误拒
    _orig_qd83 = (fd.tencent_quote, fd.fund_realtime_nav)
    _qd_calls83 = []
    try:
        fd.tencent_quote = lambda codes: _qd_calls83.append(("quote", codes)) or {
            c: {"name": "沙特ETF华泰柏瑞", "price": 1.1} for c in codes}
        fd.fund_realtime_nav = lambda codes: [
            {"code": c, "name": "沙特ETF华泰柏瑞", "nav": 1.0,
             "nav_date": "2026-11-24"} for c in codes]
        _qd83 = fd.qdii_premium(["520830"])
        check("L1", "V0.8.3QDII-52号段合法场内码",
              _qd83[0]["premium_pct"] == 10.0, str(_qd83))
        expect_raise("L1", "V0.8.3QDII-场外码拒绝假溢价",
                     lambda: fd.qdii_premium(["000001"]), "非场内基金码")
        check("L1", "V0.8.3QDII-非法码零网络",
              _qd_calls83 == [("quote", ["520830"])], str(_qd_calls83))
    finally:
        fd.tencent_quote, fd.fund_realtime_nav = _orig_qd83

    # V0.8.3：重仓复制法归一；低股票仓位不强行下调仓结论
    _orig_nd83 = (fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline)
    try:
        def _nd_snap83(fund_nav, position):
            return {"_meta": {"code": "161725"}, "name": "仓位样本",
                    "nav_history": [
                        {"date": "2026-09-30", "nav": 1.0, "daily_return": None, "dividend": 0},
                        {"date": "2026-10-02", "nav": fund_nav, "daily_return": None, "dividend": 0}],
                    "stock_position_history": [{"date": "2026-09-30", "position_pct": position}]}
        fd.fund_snapshot = lambda q, **k: _nd_snap83(1.18, 90.0)
        fd.fund_holdings = lambda q, **k: [
            {"period": "2026-09-30", "stock_code": "600519",
             "stock_name": "样本股", "nav_pct": 45.0}]
        fd._tencent_kline = lambda *a, **k: [
            {"date": "2026-09-30", "close": 100.0},
            {"date": "2026-10-02", "close": 120.0}]
        _nd_high83 = fd.nav_divergence("161725")
        check("L1", "V0.8.3重仓探测-覆盖度归一消除趋势假背离",
              _nd_high83["holdings_weighted_return_pct"] == 20.0
              and _nd_high83["divergence_pp"] == -2.0
              and _nd_high83["verdict"] == "持仓一致",
              f"基金18% vs 前十归一20%：{_nd_high83['divergence_pp']}pp")
        fd.fund_snapshot = lambda q, **k: _nd_snap83(1.12, 60.0)
        _nd_low83 = fd.nav_divergence("161725")
        check("L1", "V0.8.3重仓探测-低股票仓位结论降级",
              _nd_low83["divergence_pp"] == -8.0
              and "股票仓位不足" in _nd_low83["verdict"],
              f"60%股票仓位：{_nd_low83['verdict']}")
    finally:
        fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline = _orig_nd83

    # 经理画像：主页 HTML 形态 mock（mgrlists+三标签）+ ID/公司ID分流
    _pad = "<!--" + "x" * 11000 + "-->"     # 真实主页 ~71KB；mock 需越过 10000B 空壳防线
    _mgr_html = ('<html><title>侯昊 _ 基金经理档案 _ 天天基金网</title>' + _pad +
                 '<div>累计任职时间：9年又82天</div><div>现任基金(461.32亿)</div>'
                 '<div>任期回报：92.81%</div>'
                 '<script>var mgrlists=[{valueField:\'159243\',name:\'创业板人工智能ETF招商\',type:\'3\'},'
                 '{valueField:\'016880\',name:\'招商碳中和C\',type:\'3\'}];</script></html>')

    class _MP:
        status_code = 200
        content = _mgr_html.encode("utf-8")

    _orig_get4 = fd.ttfund_get
    _orig_snap4 = fd.fund_snapshot
    try:
        fd.ttfund_get = lambda *a, **k: _MP()
        pg = fd.fund_manager("30379533", force_refresh=True)
        check("L1", "经理画像-ID路径解析",
              pg["name"] == "侯昊" and pg["tenure"] == "9年又82天"
              and pg["scale_yi"] == 461.32 and pg["return_since_pct"] == 92.81
              and pg["fund_count"] == 2 and pg["funds"][0]["code"] == "159243",
              "title/任职/现任规模/任期回报/mgrlists 五锚齐")
        fd.fund_snapshot = lambda q: {"_meta": {"code": "161725", "version": "x"},
                                      "name": "白酒A", "manager": {"manager_id": "30379533", "name": "侯昊"}}
        pg2 = fd.fund_manager("161725", force_refresh=True)   # 基金码转接（mock主页同响应）
        check("L1", "经理画像-基金码转接现任经理",
              pg2["manager_id"] == "30379533" and pg2["name"] == "侯昊", "快照→ID→主页")
        expect_raise("L1", "经理画像-80开头公司ID引导",
                     lambda: fd.fund_manager("80065113"), "fund_company")
    finally:
        fd.ttfund_get, fd.fund_snapshot = _orig_get4, _orig_snap4
        (fd.FUND_CACHE_DIR / "manager" / "30379533.json").unlink(missing_ok=True)  # mock不入真缓存

    # 经理排行：行解析锚（12字段/规模带亿/record校验）
    _rank_body = ('var returnjson= {data:[["30792181","祝祯哲","80000221","富国基金",'
                  '"009758,013632","富国可转债C,富国利享回报A","1317","8.30%","009758",'
                  '"富国可转债C","25.91亿元","8.30%"]],record:1,pages:1,curpage:1}')

    class _MR:
        status_code = 200
        content = _rank_body.encode("utf-8")

    _orig_get5 = fd.ttfund_get
    try:
        fd.ttfund_get = lambda *a, **k: _MR()
        mr = fd.manager_rank(pages=1, force_refresh=True)
        check("L1", "经理排行-行解析与record校验",
              len(mr) == 1 and mr[0]["manager_id"] == "30792181"
              and mr[0]["name"] == "祝祯哲" and mr[0]["company"] == "富国基金"
              and mr[0]["scale_yi"] == 25.91 and mr[0]["return_pct"] == 8.3
              and mr[0]["fund_codes"] == ["009758", "013632"],
              "12字段/25.91亿元→25.91/回报8.30%→8.3")
        expect_raise("L1", "经理排行-record不符拒绝",
                     lambda: fd.manager_rank(pages=2, force_refresh=True), "校验失败")
        fd.ttfund_get = lambda *a, **k: type(
            "_MRDuplicate", (), {"status_code": 200,
                                 "content": _rank_body.replace("record:1", "record:2").encode("utf-8")})()
        expect_raise("L1", "V0.8.3经理排行-重复拼页拒绝",
                     lambda: fd.manager_rank(pages=2, force_refresh=True), "重复 manager_id")
        expect_raise("L1", "经理排行-pages上限",
                     lambda: fd.manager_rank(pages=89, force_refresh=True), "pages")
    finally:
        fd.ttfund_get = _orig_get5
        (fd.FUND_CACHE_DIR / "manager" / "rank_p1.json").unlink(missing_ok=True)

    check("L1", "报告期-归一化与空=最近季末",
          fd._norm_rdate("2026-09-30") == "20260930"
          and fd._norm_rdate("") == fd._latest_quarter_end().replace("-", ""),
          f"空入参→{fd._norm_rdate('')}")
    expect_raise("L1", "报告期-非季末日拒绝",
                 lambda: fd._norm_rdate("20261015"), "季末日")
    check("L1", "报告期-最近季末锚点(跨年回退)",
          fd._latest_quarter_end(date(2026, 11, 10)) == "2026-09-30"
          and fd._latest_quarter_end(date(2026, 1, 1)) == "2025-12-31"
          and fd._latest_quarter_end(date(2026, 12, 31)) == "2026-12-31"
          and fd._latest_quarter_end(date(2026, 3, 31)) == "2026-03-31", "")
    check("L1", "类型桶-E1类型→类别映射",
          fd._bucket_of("指数型-股票") == "zs" and fd._bucket_of("QDII") == "qdii"
          and fd._bucket_of("混合型-偏股") == "hh" and fd._bucket_of("债券型-长债") == "zq"
          and fd._bucket_of("股票型") == "gp" and fd._bucket_of("FOF-稳健") == "fof"
          and fd._bucket_of("Reits") == "zq" and fd._bucket_of("商品") == "zs"
          and fd._bucket_of(None) is None,
          "fund_rank 本地过滤的映射依据")

    # ── V0.6.1 基准指数层离线单元（kline_daily 四路解析 + F6 不降级 + 防线）──
    check("L1", "K线-入参归一四形态",
          fd._kline_resolve("沪深300") == ("index", "1.000300")
          and fd._kline_resolve("中证A500") == ("index", "1.000510")
          and fd._kline_resolve("1.000905") == ("index", "1.000905")
          and fd._kline_resolve("600519") == ("stock", "600519")
          and fd._kline_resolve("SZ300308") == ("stock", "sz300308")
          and fd._kline_resolve("上证指数") == ("index", "1.000001")
          and fd._kline_resolve("上证综指") == ("index", "1.000001")
          and fd._kline_resolve("深证成指") == ("index", "0.399001")
          and fd._kline_resolve("深证综指") == ("index", "0.399106")
          and fd._kline_resolve("上证180") == ("index", "1.000010"),
          "指数名/secid/股票码/带前缀码（前缀保留市场信息；V0.7.5 扩表 4 指数）")
    expect_raise("L1", "K线-未知名拒绝",
                 lambda: fd._kline_resolve("纳斯达克"), "不识别")
    check("L1", "K线-secid→腾讯符号(市场2不降级)",
          fd._secid_tencent_sym("1.000300") == "sh000300"
          and fd._secid_tencent_sym("0.399006") == "sz399006"
          and fd._secid_tencent_sym("2.932000") is None,
          "中证指数公司发布的指数无腾讯代码")
    expect_raise("L1", "K线-lmt下限", lambda: fd.kline_daily("沪深300", lmt=5), "lmt")

    class _KL:                                     # push2his mock（含重复日期行）
        def __init__(self, klines):
            self.status_code = 200
            self._k = klines
            self.content = b"{}"

        def json(self):
            return {"data": {"klines": self._k}}

        def raise_for_status(self):
            return None

    _orig_p2g, _tx0 = fd._push2_get, fd.TENCENT_REQUEST_COUNT[0]
    _cache_rw = (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None       # L1 不读不写真缓存（防 mock 数据落盘污染）
        fd._cache_write = lambda *a, **k: None
        # lmt=10 → floor=10；12 行 mock（10 有效+1 重复+1 脏）去重后恰好过线；
        # fallback 关闭 = 纯解析路径，零腾讯请求
        def _kl_mock(klines):
            return lambda url, params=None, context="": {"data": {"klines": klines}}

        fd._push2_get = _kl_mock(["2026-01-02,3000.5", "2026-01-02,3000.5",
                                  "2026-01-03,3001.0", "bad,xx"]
                                 + [f"2026-01-{d:02d},{3000 + d}.0"
                                    for d in range(4, 13)])
        rows = fd.kline_daily("1.000905", lmt=10, force_refresh=True,
                              use_tencent_fallback=False)
        check("L1", "K线-EM解析+按日期去重",
              len(rows) == 11 and rows[0]["date"] == "2026-01-02"
              and rows[-1]["date"] == "2026-01-12"
              and rows[0]["source"] == "push2his",
              f"{len(rows)} 根（重复日期行+脏行剔除，01-02 只留一条，source=push2his）")
        _cw83, _cache_keys83 = fd._cache_write, []
        try:
            fd._cache_write = lambda rel, payload: _cache_keys83.append(rel)
            fd.kline_daily("1.000905", lmt=10, force_refresh=True,
                           use_tencent_fallback=True)
            fd.kline_daily("1.000905", lmt=10, force_refresh=True,
                           use_tencent_fallback=False)
            check("L1", "V0.8.3K线-备胎参数缓存键隔离",
                  len(_cache_keys83) == 2 and _cache_keys83[0] != _cache_keys83[1]
                  and _cache_keys83[1].endswith("_nofb.json"), str(_cache_keys83))
        finally:
            fd._cache_write = _cw83
        fd._push2_get = _kl_mock(["2026-01-02,3000.5"] * 5)
        expect_raise("L1", "K线-样本不足语义化报错",
                     lambda: fd.kline_daily("1.000905", lmt=10, force_refresh=True,
                                            use_tencent_fallback=False), "指数日线不足")

        def _raise_rc(*a, **k):                        # 差分锁：EM 403 → RiskControlError
            raise fd.RiskControlError("mock 403")      # 必须原样上抛且绝不触发腾讯备胎

        fd._push2_get = _raise_rc
        expect_raise("L1", "K线-风控不降级",
                     lambda: fd.kline_daily("1.000905", force_refresh=True), "mock 403")
        check("L1", "K线-风控路径零腾讯请求",
              fd.TENCENT_REQUEST_COUNT[0] == _tx0, f"腾讯账本 {fd.TENCENT_REQUEST_COUNT[0]}")

        _stock_kline83 = fd._tencent_kline
        try:
            fd._tencent_kline = _raise_rc
            try:
                fd.kline_daily("600519", lmt=60, force_refresh=True)
                check("L1", "V0.8.3股票K线-风控类型应抛未抛", False)
            except RuntimeError as e:
                check("L1", "V0.8.3股票K线-风控类型不抹掉",
                      isinstance(e, fd.RiskControlError), type(e).__name__)
        finally:
            fd._tencent_kline = _stock_kline83

        # core 路由：开闸由协调器三连击达成（legacy 落盘文件已退位为镜像）
        for _sig in range(3):
            try:
                fd._cbreak_signal("em", "kline-风险熔断演练")
            except fd.RiskControlError:
                pass                      # 第 3 次触闸按 F2 契约抛出，闸门已开

        def _raise_open(*a, **k):
            fd._cbreak_check("em")

        fd._push2_get = _raise_open
        import urllib.request as _u_guard
        _orig_bo_guard = _u_guard.build_opener
        _u_guard.build_opener = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("风险熔断不得调用腾讯备胎"))
        expect_raise("L1", "K线-风险熔断不降级",
                     lambda: fd.kline_daily("1.000905", force_refresh=True), "熔断中")
        _u_guard.build_opener = _orig_bo_guard
        fd._cbreak_file("em").unlink(missing_ok=True)
        _fresh_coordination()               # 闸门复位：演练不拖累后续用例
        check("L1", "K线-风险熔断零腾讯请求",
              fd.TENCENT_REQUEST_COUNT[0] == _tx0, f"腾讯账本 {fd.TENCENT_REQUEST_COUNT[0]}")

        def _raise_net(*a, **k):                    # P26 主干：通道网络失败→落腾讯备胎
            raise RuntimeError("push2 通道失败(mock 冷却)")

        import types as _types6
        import urllib.request as _u6
        _orig_bo6 = _u6.build_opener                # 腾讯备胎也须 mock（L1 零网络契约）

        class _FakeOpen6:
            def open(self, req, timeout=None):
                # 测试 secid=1.000905（沪市）→ 腾讯符号 sh000905（市场号 1→sh）
                payload = {"data": {"sh000905": {"qfqday": [
                    [f"2026-01-{d:02d}", "1", f"{3.0 + d / 100:.2f}"]
                    for d in range(1, 12)]}}, "code": 0}
                return _types6.SimpleNamespace(
                    read=lambda: json.dumps(payload).encode("utf-8"))

        _u6.build_opener = lambda *a, **k: _types6.SimpleNamespace(
            open=lambda req, timeout=None: _FakeOpen6().open(req, timeout))
        fd._push2_get = _raise_net
        fb_rows = fd.kline_daily("1.000905", lmt=10, force_refresh=True)
        _u6.build_opener = _orig_bo6                 # 用毕即还（后续块免受污染）
        check("L1", "K线-通道网络失败落腾讯备胎(P26主干)",
              len(fb_rows) == 10 and fb_rows[0]["source"] == "tencent"
              and fd.TENCENT_REQUEST_COUNT[0] == _tx0 + 1,
              f"mock 备胎供 {len(fb_rows)} 根（V0.7.6 翻页按 lmt 截齐：mock 11 行 payload "
              f"取最近 10；腾讯账本 +1，零真实网络，source=tencent）")
    finally:
        fd._push2_get = _orig_p2g
        fd._cache_read, fd._cache_write = _cache_rw
        fd.TENCENT_REQUEST_COUNT[0] = _tx0

    # ── V0.7.6 腾讯翻页全史备胎（缝合纯函数 + 翻页端到端，stub 零网络）──
    from datetime import date as _d7, timedelta as _td7

    def _pg7(start, n):                           # 合成连续 n 根（跳周末，日期严格递增）
        out, cur = [], _d7.fromisoformat(start)
        while len(out) < n:
            if cur.weekday() < 5:
                out.append({"date": cur.isoformat(),
                            "close": round(10.0 + len(out) * 0.01, 2)})
            cur += _td7(days=1)
        return out

    _long7 = _pg7("2018-01-01", 2000)
    _pgA = _long7[-800:]                          # 最新页 = long[1200:2000]
    _pgB = _long7[401:1201]                       # 次页 800 根：末恰为 _pgA[0]（锚定页含锚日）
    check("L1", "腾讯翻页-缝合纯函数正常",
          len(fd._stitch_tencent_pages([_pgA, _pgB])) == 1599
          and fd._stitch_tencent_pages([_pgA, _pgB])[0]["date"] == _long7[401]["date"],
          "两页 800+800 缝合（页尾锚重叠 1 行）=1599，起点对齐次页首")
    _bad_gap = [dict(r) for r in _pgB]
    _bad_gap[-1] = {"date": "1999-01-01", "close": _pgA[0]["close"]}
    expect_raise("L1", "腾讯翻页-缝断档整批拒绝",
                 lambda: fd._stitch_tencent_pages([_pgA, _bad_gap]), "断档或越界")
    _bad_px = [dict(r) for r in _pgB]
    _bad_px[-1] = {"date": _pgA[0]["date"], "close": _pgA[0]["close"] + 9.9}
    expect_raise("L1", "腾讯翻页-缝收盘漂移整批拒绝",
                 lambda: fd._stitch_tencent_pages([_pgA, _bad_px]), "收盘不一致")

    # 端到端：push2 熔断 → 翻页备胎拼全史（opener 按 param 末锚分流三页）
    import types as _types7
    import urllib.request as _u7
    _orig_bo7 = _u7.build_opener
    _pgC = _long7[102:402]                        # 第三页短页 300 根：末恰为 _pgB[0] 锚
    _pages7 = {"": _pgA, _pgA[0]["date"]: _pgB, _pgB[0]["date"]: _pgC}
    _tx7 = fd.TENCENT_REQUEST_COUNT[0]

    def _tx_open7(req, timeout=None):             # 按 URL 的 end 参数返回对应页
        seg = req.full_url.split("day,,")[1].split(",")[0]
        rows = _pages7[seg if seg else ""]
        payload = {"data": {"sh000905": {"qfqday": [
            [r["date"], "1", str(r["close"])] for r in rows]}}}
        return _types7.SimpleNamespace(read=lambda: json.dumps(payload).encode("utf-8"))

    _p2g7, _cw7 = fd._push2_get, (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        _u7.build_opener = lambda *a, **k: _types7.SimpleNamespace(open=_tx_open7)
        fd._push2_get = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("push2 通道失败(mock 熔断)"))
        _k7 = fd.kline_daily("1.000905", lmt=2000, force_refresh=True)
        check("L1", "腾讯翻页-备胎端到端拼全史",
              len(_k7) == 1898 and _k7[0]["date"] == _long7[102]["date"]
              and _k7[-1]["date"] == _long7[-1]["date"]
              and all(r["source"] == "tencent" for r in _k7)
              and fd.TENCENT_REQUEST_COUNT[0] == _tx7 + 3,
              f"熔断期备胎 3 发串行拼 {_long7[102]['date']}→今 1898 根（原短窗只有 800）")
        # 锚停滞：恒返同一满页 → 缝校验/停滞守卫整批拒绝（拒绝静默截断）。
        # 直测分页函数：kline_daily 层 fb_note 截断 40 字会吃掉「整批拒绝」字样
        _u7.build_opener = lambda *a, **k: _types7.SimpleNamespace(
            open=lambda req, timeout=None: _types7.SimpleNamespace(
                read=lambda: json.dumps({"data": {"sh000905": {"qfqday": [
                    [r["date"], "1", str(r["close"])] for r in _pgA]}}}).encode("utf-8")))
        expect_raise("L1", "腾讯翻页-锚停滞整批拒绝",
                     lambda: fd._tencent_kline_paginated("sh000905", 3000),
                     "整批拒绝")
    finally:
        _u7.build_opener = _orig_bo7
        fd._push2_get = _p2g7
        fd._cache_read, fd._cache_write = _cw7
        fd.TENCENT_REQUEST_COUNT[0] = _tx7

    # 页数阀耗尽 / 非首页空页：两条拒绝静默截断防线（stub 在 _tencent_kline_page 层）
    _big7 = _pg7("1980-01-01", 9000)

    def _mkpages7(series, count):                 # 尾部起连续翻页（页尾=前页首，各 800 根）
        pages, hi = [], len(series)
        while len(pages) < count:
            pages.append(series[hi - 800:hi])
            hi -= 799
        return pages

    _orig_tkp7 = fd._tencent_kline_page
    try:
        _calls7 = [0]
        _stack7 = _mkpages7(_big7, 12)

        def _fake_tkp7(pre, end_date=None, bars=None):
            _calls7[0] += 1
            return _stack7[_calls7[0] - 1]
        fd._tencent_kline_page = _fake_tkp7
        expect_raise("L1", "腾讯翻页-页数阀耗尽整批拒绝",
                     lambda: fd._tencent_kline_paginated("sh000300", 8500),
                     "页数阀耗尽")                  # 10 页去重 7991 < 8500，末页满页=历史未到头

        _calls7b = [0]

        def _fake_tkp7b(pre, end_date=None, bars=None):
            _calls7b[0] += 1
            return _pgA if _calls7b[0] == 1 else []   # 满页后锚定页回空（协议异常形态）
        fd._tencent_kline_page = _fake_tkp7b
        expect_raise("L1", "腾讯翻页-中段空页整批拒绝",
                     lambda: fd._tencent_kline_paginated("sh000300", 2600),
                     "+1 重叠协议")
    finally:
        fd._tencent_kline_page = _orig_tkp7

    # ── V0.7.6 行业板块指数（BK 查表 + 直传放行 + 腾讯符号边界）──
    check("L1", "K线-行业板块查表与BK直传",
          fd._kline_resolve("半导体") == ("index", "90.BK1036")
          and fd._kline_resolve("银行Ⅱ") == ("index", "90.BK0475")
          and fd._kline_resolve("90.BK0475") == ("index", "90.BK0475")
          and len(fd.BENCH_SECID) == 125,
          "东财行业板块 100 个入表（clist t:2 快照 2026-11-17）+ BK secid 直传放行")
    check("L1", "K线-BK指数无腾讯备胎",
          fd._secid_tencent_sym("90.BK1036") is None
          and fd._secid_tencent_sym("1.000300") == "sh000300",
          "板块指数腾讯无码不降级（与市场2同款 EM 单源语义）")

    # 审查必须项回归锁：sh000xxx 沪指与深股 000xxx 碰撞（实测 sh000016 曾静默错成深康佳A）
    # V0.7.5 上证指数(1.000001)/上证180(1.000010) 入表：sh000001/sh000010 现随
    # 值域回 index（原断言"sh000001→stock"编码的是旧表缺口，非期望行为）
    check("L1", "K线-前缀命中基准值域回index",
          fd._kline_resolve("sh000016") == ("index", "1.000016")
          and fd._kline_resolve("sh000001") == ("index", "1.000001")
          and fd._kline_resolve("sh000010") == ("index", "1.000010")
          and fd._kline_resolve("sz000016") == ("stock", "sz000016")
          and fd._kline_resolve("399006") == ("index", "0.399006"),
          "前缀保留市场信息+值域回index+399深指号段")

    # ── 指数路由派生锁：只有显式指数上下文才查询 _SH_INDEX_Q ──
    check("L1", "路由-中证沪指码派生覆盖",
          fd._tencent_prefix("000922", kind="index") == "sh000922"
          and fd._tencent_prefix("000906", kind="index") == "sh000906"
          and fd._tencent_prefix("000015", kind="index") == "sh000015"
          and fd._tencent_prefix("000510", kind="index") == "sh000510"
          and fd._tencent_prefix("000985", kind="index") == "sh000985"
          and fd._tencent_prefix("000300", kind="index") == "sh000300"
          and fd._tencent_prefix("000010", kind="index") == "sh000010",
          "显式指数上下文全部回沪（历史成员 000010 保留）")
    check("L1", "路由-股票码不受派生影响",
          fd._tencent_prefix("000001") == "sz000001"
          and fd._tencent_prefix("600519") == "sh600519"
          and fd._tencent_prefix("300308") == "sz300308",
          "深股/沪股/创业板原路由不变")

    # ── V0.7.1 push2 连接级熔断（坑P26：IP 缓释桶）离线单元 ──
    import tempfile as _tempfile7
    import shutil as _shutil7
    _tmpdir7 = Path(_tempfile7.mkdtemp())
    _connf7 = fd._push2_conn_file
    _jit7, _thr7, _cbchk7 = fd._PUSH2_JITTER, fd._cross_proc_throttle, fd._cbreak_check
    _sess7, _fails7 = fd.requests.Session, fd._push2_conn_fails[0]
    _mk7 = [0]

    class _ConnFailSess7:                          # 连接级失败（RemoteDisconnected mock）
        def __init__(self):
            _mk7[0] += 1
            self.trust_env = True
            self.headers = {}

        def get(self, *a, **k):
            raise fd.requests.RequestException("RemoteDisconnected mock")

        def close(self):
            pass

    class _OkResp7:
        status_code = 200
        text = '{"rc":0}'

        def json(self):
            return {"rc": 0}

    class _OkSess7:
        def __init__(self):
            _mk7[0] += 1
            self.trust_env = True
            self.headers = {}

        def get(self, *a, **k):
            return _OkResp7()

        def close(self):
            pass

    try:
        fd._push2_conn_file = lambda: _tmpdir7 / ".cbreak_push2conn"   # 标记重定向 tmp
        fd._PUSH2_JITTER = (0.0, 0.0)             # 零抖动（离线快跑）
        fd._cross_proc_throttle = lambda *a, **k: None
        fd._cbreak_check = lambda *a, **k: None    # F2 真标记隔离（本机在线跑挂过不污染）
        _fresh_coordination()   # 前序风控演练可能已开东财闸门：本块需要网关放行
        fd._push2_conn_fails[0] = 0
        fd.requests.Session = _ConnFailSess7
        expect_raise("L1", "熔断-首败上抛通道错",
                     lambda: fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="t1"), "push2 通道失败")
        check("L1", "熔断-首败仅计数不落盘",
              not fd._push2_conn_file().exists() and fd._push2_conn_fails[0] == 1
              and _mk7[0] == 1,
              f"Session 建 {_mk7[0]} 次，连败计数 1（未触发停机）")
        expect_raise("L1", "熔断-二连败触发停机",
                     lambda: fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="t2"), "push2 通道失败")
        check("L1", "熔断-停机标记落盘",
              fd._push2_conn_file().exists() and _mk7[0] == 2,
              f".cbreak_push2conn 已写（Session 建 {_mk7[0]} 次，独立于 F2 标记文件）")
        _mk_before = _mk7[0]
        expect_raise("L1", "熔断-停机期零网络直通",
                     lambda: fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="t3"), "熔断中")
        check("L1", "熔断-停机期不再发请求",
              _mk7[0] == _mk_before,
              f"停机期 Session 新建 {_mk7[0] - _mk_before} 次（应为 0——降级链接管）")
        # 成功清零语义：删标记 → 1 败 → 1 成功（清零）→ 再 1 败 → 不落盘
        fd._push2_conn_file().unlink()
        fd._push2_conn_fails[0] = 0
        expect_raise("L1", "熔断-清零场景首败",
                     lambda: fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="t4"), "push2 通道失败")
        fd.requests.Session = _OkSess7
        d7 = fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="ok")
        check("L1", "熔断-成功清零+期满自愈放行",
              d7 == {"rc": 0} and fd._push2_conn_fails[0] == 0
              and not fd._push2_conn_file().exists(),
              "成功响应清零连败计数；过/删标记后正常放行")
        fd.requests.Session = _ConnFailSess7
        expect_raise("L1", "熔断-清零后孤立失败",
                     lambda: fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="t5"), "push2 通道失败")
        check("L1", "熔断-孤立失败不累积停机",
              fd._push2_conn_fails[0] == 1 and not fd._push2_conn_file().exists(),
              "成功清零后单次失败重新从 1 计（不误停机）")
        # 过期标记自动复位（有界试探：写远过去 until → check 复位放行）
        fd._push2_conn_file().write_text("1000")
        fd.requests.Session = _OkSess7
        d7b = fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get", context="heal")
        check("L1", "熔断-过期标记自动复位",
              d7b == {"rc": 0} and not fd._push2_conn_file().exists(),
              "停机期满自动复位放行（每小时探测有界）")
    finally:
        fd._push2_conn_file = _connf7
        fd._PUSH2_JITTER, fd._cross_proc_throttle = _jit7, _thr7
        fd._cbreak_check = _cbchk7
        fd.requests.Session = _sess7
        fd._push2_conn_fails[0] = 0
        _shutil7.rmtree(_tmpdir7, ignore_errors=True)

    # ── V0.7.1 国债实时短缓存 + 值域守卫 + 脏值降级（离线）──
    _p2g7b, _cw7b = fd._push2_get, (fd._cache_read, fd._cache_write)
    _cbcurve7 = fd.cb_yield_curve
    _calls7b, _store7b = [0], {}
    try:
        def _fake_p2g7b(url, params=None, context=""):
            _calls7b[0] += 1
            return {"data": {"f43": 16059, "f58": "中国10年期国债",
                             "f169": -34, "f170": -21}}

        fd._push2_get = _fake_p2g7b
        fd._cache_read = lambda rel, max_age_hours=None: _store7b.get(rel)
        fd._cache_write = lambda rel, payload: _store7b.__setitem__(rel, payload)
        rt7 = fd.cn_treasury_realtime()
        rt7b = fd.cn_treasury_realtime()           # 300s 内二调：命中短缓存 0 请求
        check("L1", "国债实时-短缓存二调0请求",
              _calls7b[0] == 4 and rt7["10y"]["yield_pct"] == 1.6059
              and rt7["as_of"] == "盘中(push2)"
              and "treasury/realtime_push2" in next(iter(_store7b), "")
              and rt7b["time"] == rt7["time"],
              f"一调 {_calls7b[0]} 请求（4 期限）、二调 0（300s 短缓存），"
              f"10y={rt7['10y']['yield_pct']}")
        # 值域守卫：f43=200000 → y=20 越界 → 拒绝静默脏值 → 降级中债收盘。
        # V0.7.5：降级写独立短缓存键（防降级期重打 push2 软失败窗口），健康键不污染
        def _fake_dirty7b(url, params=None, context=""):
            _calls7b[0] += 1
            return {"data": {"f43": 200000}}

        fd._push2_get = _fake_dirty7b
        fd.cb_yield_curve = lambda curve, year=None: {"points": [
            {"date": "2026-11-11", "term": t, "yield_pct": 1.5 + i * 0.1}
            for i, t in enumerate(["2y", "5y", "10y", "30y"])]}
        _store7b.clear()
        _calls7b[0] = 0
        rt7c = fd.cn_treasury_realtime()
        rt7d = fd.cn_treasury_realtime()           # 300s 内二调：命中降级短缓存 0 请求
        check("L1", "国债实时-脏值守卫降级写独立短缓存",
              "收盘(降级" in rt7c["as_of"] and rt7c["10y"]["change"] is None
              and any("realtime_degraded" in k for k in _store7b)
              and not any("realtime_push2" in k for k in _store7b)
              and _calls7b[0] == 1 and rt7d["time"] == rt7c["time"],
              f"首期限越界即触发降级（{_calls7b[0]} 请求后走中债收盘，独立键已写，"
              f"二调 0 请求命中降级缓存）")
    finally:
        fd._push2_get = _p2g7b
        fd._cache_read, fd._cache_write = _cw7b
        fd.cb_yield_curve = _cbcurve7

    expect_raise("L1", "K线-股票超腾讯上限语义化报错",
                 lambda: fd.kline_daily("600519", lmt=801), "上限")

    # 备胎成功回落路径（审查覆盖缺口）：EM 短返 1 行 → 腾讯 mock 满 10 行
    import types as _types
    import urllib.request as _u
    _orig_get7, _orig_bo, _orig_p2g7 = fd.ttfund_get, _u.build_opener, fd._push2_get
    _tx7 = fd.TENCENT_REQUEST_COUNT[0]
    _cw7 = (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd._push2_get = _kl_mock(["2026-01-02,3000.5"])

        class _FakeOpen:
            def open(self, req, timeout=None):
                payload = {"data": {"sz000905": {"qfqday": [
                    ["2026-01-02", "1", "3.0"], ["2026-01-03", "1", "3.1"],
                    ["2026-01-06", "1", "3.2"], ["2026-01-07", "1", "3.3"],
                    ["2026-01-08", "1", "3.4"], ["2026-01-09", "1", "3.5"],
                    ["2026-01-10", "1", "3.6"], ["2026-01-13", "1", "3.7"],
                    ["2026-01-14", "1", "3.8"], ["2026-01-15", "1", "3.9"]]}},
                    "code": 0}
                return _types.SimpleNamespace(
                    read=lambda: json.dumps(payload).encode("utf-8"))

        _u.build_opener = lambda *a, **k: _types.SimpleNamespace(
            open=lambda req, timeout=None: _FakeOpen().open(req, timeout))
        rows = fd.kline_daily("0.000905", lmt=10, force_refresh=True)
        check("L1", "K线-EM短返→腾讯备胎成功回落",
              len(rows) == 10 and rows[0]["date"] == "2026-01-02"
              and rows[0]["source"] == "tencent"
              and fd.TENCENT_REQUEST_COUNT[0] == _tx7 + 1,
              f"{len(rows)} 根来自备胎（腾讯账本 +{fd.TENCENT_REQUEST_COUNT[0] - _tx7}，source=tencent）")
    finally:
        fd.ttfund_get = _orig_get7
        fd._push2_get = _orig_p2g7
        _u.build_opener = _orig_bo
        fd._cache_read, fd._cache_write = _cw7
        fd.TENCENT_REQUEST_COUNT[0] = _tx7

    # P23 实测补充：dt=15 目录页忽略 pi/pn，恒返回拼音前10——姓名路径不可查询（诚实边界）
    check("L1", "P23-dt15目录页陷阱入册",
          "dt=15" in (Path(__file__).parent / "fund_data.py").read_text(encoding="utf-8"),
          "fund_manager docstring/坑区标注 dt=15 忽略分页")

    # ── V0.7.0 费率全表（jjfl 通道）离线单元 ──
    _jjfl = ("<html><title>测试基金A(000001)基金费率 _ 天天基金网</title><body>"
             "<table><tr><td>管理费率</td><td>1.20%（每年）</td><td>托管费率</td>"
             "<td>0.20%（每年）</td><td>销售服务费率</td><td>0.00%（每年）</td></tr></table>"
             "<table><tr><th>适用金额</th><th>费率</th></tr>"
             "<tr><td>小于200万元</td><td>1.20%</td></tr>"
             "<tr><td>大于等于500万元</td><td>每笔1000元</td></tr></table>"
             "<table><tr><th>适用金额</th><th>原费率|天天基金优惠费率</th></tr>"
             "<tr><td>小于200万元</td><td>1.50% | 0.15%</td></tr>"
             "<tr><td>大于等于500万元</td><td>每笔1000元</td></tr></table>"
             "<table><tr><th>适用期限</th><th>赎回费率</th></tr>"
             "<tr><td>小于7天</td><td>1.50%</td></tr>"
             "<tr><td>大于等于730天</td><td>0.00%</td></tr></table>"
             "</body></html>")
    class _FR:
        def __init__(self, text):
            self.status_code, self.text = 200, text
            self.content = text.encode("utf-8")

        def raise_for_status(self):
            return None

    _orig_get12, _cw12 = fd.ttfund_get, (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd.ttfund_get = lambda *a, **k: _FR(_jjfl)
        ff = fd.fund_fees("005001", force_refresh=True)
        check("L1", "费率全表-运作费三率+表头特征分派",
              ff["management_fee_pct"] == 1.2 and ff["custody_fee_pct"] == 0.2
              and ff["sales_service_fee_pct"] == 0.0 and ff["name"] == "测试基金A",
              "管理/托管/销售服务按标签邻接取值；title 剥费率尾")
        check("L1", "费率全表-申购档原|优惠拆分+固定费档原文",
              ff["purchase_tiers"][0] == {"range": "小于200万元", "origin_pct": 1.5,
                                           "discount_pct": 0.15, "fee_text": "1.50%|0.15%"}
              and ff["purchase_tiers"][1]["origin_pct"] is None
              and ff["purchase_tiers"][1]["fee_text"] == "每笔1000元",
              "百分比对拆分；每笔N元档 None+原文")
        check("L1", "费率全表-认购/赎回阶梯",
              ff["founding_tiers"][0]["fee_text"] == "1.20%"
              and [(x["holding"], x["fee_pct"]) for x in ff["redemption_tiers"]]
              == [("小于7天", 1.5), ("大于等于730天", 0.0)],
              "认购适用金额→费率；赎回适用期限阶梯")
        empty_html = "<html><title>x</title><body><table><tr><td>无关</td></tr></table></body></html>"
        fd.ttfund_get = lambda *a, **k: _FR(empty_html)
        expect_raise("L1", "费率全表-空解析拒绝",
                     lambda: fd.fund_fees("005001", force_refresh=True), "解析为空")
    finally:
        fd.ttfund_get = _orig_get12
        fd._cache_read, fd._cache_write = _cw12


    # ── V0.6.2 规模全历史（gmbd 通道）离线单元 ──
    _gmbd = ('var gmbd_apidata={ content:"<table class=\'w782\'><thead><tr>'
             '<th>日期</th><th>期间申购（亿份）</th><th>期间赎回（亿份）</th>'
             '<th>期末总份额（亿份）</th><th>期末净资产（亿元）</th><th>净资产变动率</th>'
             '</tr></thead><tbody>'
             '<tr><td>2026-09-30</td><td>0.15</td><td>0.36</td><td>1.73</td><td>4.30</td>'
             '<td>-21.51%</td></tr>'
             '<tr><td>2026-06-30</td><td>0.28</td><td>0.32</td><td>1.94</td><td>5.48</td>'
             '<td>13.79%</td></tr>'
             '<tr><td>2013-09-27</td><td>---</td><td>---</td><td>0.36</td><td>---</td>'
             '<td>---</td></tr>'
             '</tbody></table>", "FSRQ" : "华安沪深300增强A", "ABBNAME" : "HAHS300ZQA" }')

    class _GR:
        def __init__(self, text):
            self.status_code, self.text, self.content = 200, text, text.encode("utf-8")

        def raise_for_status(self):
            return None

    _orig_get8, _cw8 = fd.ttfund_get, (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd.ttfund_get = lambda *a, **k: _GR(_gmbd)
        sh3 = fd.fund_scale_history("000312", force_refresh=True)
        check("L1", "规模全史-解析+升序反转+None形态",
              len(sh3) == 3 and sh3[0]["date"] == "2013-09-27"
              and sh3[-1]["date"] == "2026-09-30"
              and sh3[0]["shares_yi"] == 0.36 and sh3[0]["nav_yi"] is None
              and sh3[2]["subscribe_yi"] == 0.15
              and sh3[2]["nav_change_pct"] == -21.51,
              f"源序新→旧已反转：{[x['date'] for x in sh3]}（募集期 ---→None）")
        bad_head = _gmbd.replace("<th>期末净资产（亿元）</th>", "")
        fd.ttfund_get = lambda *a, **k: _GR(bad_head)
        expect_raise("L1", "规模全史-缺必需列拒绝(动态列映射)",
                     lambda: fd.fund_scale_history("000312", force_refresh=True), "必需列")
        thin1 = ('var gmbd_apidata={ content:"<table><thead><tr><th>日期</th>'
                 '<th>期末总份额（亿份）</th><th>期末净资产（亿元）</th></tr></thead>'
                 '<tbody><tr><td>2026-09-30</td><td>1.73</td><td>4.30</td></tr>'
                 '</tbody></table>", "FSRQ" : "x" }')
        fd.ttfund_get = lambda *a, **k: _GR(thin1)
        expect_raise("L1", "规模全史-单期记录不足拒绝",
                     lambda: fd.fund_scale_history("000312", force_refresh=True), "记录不足")

        def _raise_rc2(*a, **k):
            raise fd.RiskControlError("mock gmbd 403")

        fd.ttfund_get = _raise_rc2
        expect_raise("L1", "规模全史-风控不降级",
                     lambda: fd.fund_scale_history("000312", force_refresh=True), "mock gmbd 403")
    finally:
        fd.ttfund_get = _orig_get8
        fd._cache_read, fd._cache_write = _cw8

    # ── 盲区补测：plain_text_map 渲染（白话输出是产品门面，此前零覆盖）──
    _pmap = {"fund": "测试白酒基金", "period": "2026-09-30", "topn_weight_pct": 86.0,
             "themes": [("白酒", 86.0), ("食品饮料", 40.0)],
             "detail": [
                 {"stock_name": "贵州茅台", "nav_pct": 9.9,
                  "quote": {"price": 1439.0, "change_pct": -0.4, "pe_ttm": 22.5, "pb": 8.8}},
                 {"stock_name": "五粮液", "nav_pct": 5.2,
                  "quote": {"price": 128.0, "change_pct": 1.1, "pe_ttm": -3.0, "pb": 4.2}}],
             "pe_median_profitable": 22.5,
             "failed_stocks": [{"name": "未分类股"}], "unclassified_weight_pct": 3.5}
    txt = fd.plain_text_map(_pmap)
    check("L1", "白话地图-标题/一句话/主题排行/PE中位数",
          "《测试白酒基金 里装了什么》（2026-09-30 季报，前十大占净值 86.0%）" in txt
          and "主要是白酒（相关持仓权重约 86%）" in txt
          and "主题词排行: 白酒 86%、食品饮料 40%" in txt
          and "盈利股 PE 中位数 22.5 倍" in txt and "不高于30 倍参考线" in txt,
          f"{len(txt)} 字符")
    _wly_line = [l for l in txt.splitlines() if "五粮液" in l][0]
    check("L1", "白话地图-亏损股PE文案/未分类警示/明细行",
          "亏损" in _wly_line and "9.9%" in txt and "1439.00" in txt
          and "⚠ 1 只未参与主题聚合（合计权重 3.5%）: 未分类股" in txt,
          "负 PE→明细行「亏损」（按行断言防'已剔除亏损股'恒真）、格式、failed 渲染")
    _disp = fd.plain_text_map({**_pmap, "themes": [("小主题", 3.0)], "detail": []})
    check("L1", "白话地图-分散持仓分支",
          "持仓分散，无明显单一主题。" in _disp and "主题词排行" not in _disp,
          "top 权重<5 → 分散文案")

    # ── 盲区补测：fund_new_issues 解析/分页/校验（此前仅缓存致盲的真数据调用）──
    def _ni_page(items, record):
        arr = ",".join('["01234%s","新发基金%s","测试公司","80000999","混合型-偏股",'
                       '"2026.09.01-09.28","0.15%%","经理甲"]' % (str(i).zfill(2), i)
                       for i in items)
        return ('var datas:[' + arr + '],record:' + str(record) + ",pages:9,curpage:1")

    class _NR:
        def __init__(self, body):
            self.status_code = 200
            self.content = body.encode("utf-8")
            self.text = body

        def raise_for_status(self):
            return None

    _orig_get9, _cw9 = fd.ttfund_get, (fd._cache_read, fd._cache_write)
    _pages = []
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None

        def _get_paged(url, params=None, headers=None, **k):
            pg = int(str(params.get("page", "1,50")).split(",")[0])
            _pages.append(pg)
            return _NR(_ni_page([pg * 10, pg * 10 + 1], 4))

        fd.ttfund_get = _get_paged
        got = []
        fd._cache_write = lambda rel, out: got.append((rel, out))
        # 两页聚合（record=4，每页 2 条 → 第 2 页后 len(out)=4>=total 即 break）
        # record=4：p1→2条 p2→4条 → 第2页后 len(4)>=4 break，共 2 页
        out = fd.fund_new_issues(force_refresh=True)
        check("L1", "新发-两页聚合+record达标终止+费率剥离",
              len(out) == 4 and len(_pages) == 2
              and out[0]["fee_pct"] == 0.15 and out[0]["company_id"] == "80000999"
              and got and got[0][0].startswith("new_issues"),
              f"{len(out)} 条/{len(_pages)} 页（record=4 达标 break），费率 0.15%→0.15")
        _seq = {"1": _ni_page([1], 3), "2": "var datas:[],record:3,pages:9,curpage:2"}
        fd.ttfund_get = lambda url, params=None, headers=None, **k: _NR(
            _seq[str(params["page"]).split(",")[0]])
        expect_raise("L1", "新发-截断拒绝",
                     lambda: fd.fund_new_issues(force_refresh=True), "截断拒绝")
        fd.ttfund_get = lambda *a, **k: _NR("var datas:[],record:9,pages:9,curpage:1")
        expect_raise("L1", "新发-空列表拒绝(截断拒绝形态)",
                     lambda: fd.fund_new_issues(force_refresh=True), "截断拒绝")
        fd.ttfund_get = lambda *a, **k: _NR("var no_datas_here,record:9")
        expect_raise("L1", "新发-无datas报错",
                     lambda: fd.fund_new_issues(force_refresh=True), "无 datas")
        fd.ttfund_get = lambda *a, **k: _NR('var datas:[["a"]],records:9')
        expect_raise("L1", "新发-无record总数报错",
                     lambda: fd.fund_new_issues(force_refresh=True), "record 总数")
    finally:
        fd.ttfund_get = _orig_get9
        fd._cache_read, fd._cache_write = _cw9

    # ── 盲区补测：fund_company 公司页解析（_num_near/title 清理/去重/防线）──
    _co = ("<html><title>中欧基金管理有限公司_主页 _ 天天基金网</title><body>"
           "管理规模：<span>7,070.33</span>亿元"
           "<div>基金数量：<span>540</span>只</div><div>经理人数：<span>73</span>人</div>"
           '<a href="https://fund.eastmoney.com/166002.html">中欧新蓝筹</a>'
           '<a href="https://fund.eastmoney.com/166002.html">中欧新蓝筹</a>'
           '<a href="https://fund.eastmoney.com/000001.html">88</a>'
           '<a href="https://fund.eastmoney.com/161725.html">基金品种</a>'
           + "<!--" + "x" * 10200 + "--></body></html>")
    _orig_get10, _cw10 = fd.ttfund_get, (fd._cache_read, fd._cache_write)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd.ttfund_get = lambda *a, **k: _NR(_co)
        co = fd.fund_company("80000001", force_refresh=True)
        check("L1", "公司页-千分位/标题清理/去重黑名单",
              co["name"] == "中欧基金管理有限公司" and co["scope_yi"] == 7070.33
              and co["fund_count"] == 540 and co["manager_count"] == 73
              and len(co["funds"]) == 1 and co["funds"][0]["code"] == "166002",
              f"7,070.33→{co['scope_yi']}，标题下划线截断，重复码/数字名/黑名单剔除")
        fd.ttfund_get = lambda *a, **k: _NR("<html><title>跳转</title></html>")
        expect_raise("L1", "公司页-小页防线",
                     lambda: fd.fund_company("80000002", force_refresh=True), "公司页异常")
        fd.ttfund_get = lambda *a, **k: _NR("<html><title>" + "y" * 10200 +
                                            "</title><body>无任何字段</body></html>")
        expect_raise("L1", "公司页-必需字段缺失拒绝",
                     lambda: fd.fund_company("80000003", force_refresh=True), "字段解析失败")
    finally:
        fd.ttfund_get = _orig_get10
        fd._cache_read, fd._cache_write = _cw10

    # ── 盲区补测：fund_list 解析结构 + <5000 防线（30 天缓存主键表）──
    _orig_get11, _cw11 = fd.ttfund_get, (fd._cache_read, fd._cache_write)
    _mem_backup = list(fd._FUND_LIST_MEM)
    _repo_backup11 = fd._CACHE_REPO
    _tmp11 = tempfile.TemporaryDirectory(prefix="fund-l1-fundlist-mock-")
    from fund_data_core import CacheRepository as _Repo11
    # Sprint 3：core 路径经 repo 直写、不再过 _cache_write——mock 拦截点改为
    # 换 repo 根到临时目录，假清单绝不落沙箱（原 _cache_write mock 仅拦 legacy 回退）。
    fd._CACHE_REPO = _Repo11(Path(_tmp11.name))
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        rows_js = "[" + ",".join(
            '["c%05d","p%05d","基金%05d","混合型-偏股"]' % (i, i, i)
            for i in range(12)) + "]"
        fd.ttfund_get = lambda *a, **k: _NR("var r=" + rows_js)
        expect_raise("L1", "清单-<5000拒绝缓存脏数据",
                     lambda: fd.fund_list(force_refresh=True), "疑似上游结构变更")
        rows_js = "[" + ",".join(
            '["%06d","p%06d","基金%06d","混合型-偏股"]' % (i, i, i)
            for i in range(5003)) + "]"
        fd.ttfund_get = lambda *a, **k: _NR("var r=" + rows_js)
        got_list = fd.fund_list(force_refresh=True)
        check("L1", "清单-解析结构与写缓存",
              len(got_list) == 5003 and got_list[0]["code"] == "000000"
              and got_list[0]["type"] == "混合型-偏股" and got_list[-1]["pinyin"] == "p005002",
              f"{len(got_list)} 条全解析")
    finally:
        fd.ttfund_get = _orig_get11
        fd._cache_read, fd._cache_write = _cw11
        fd._FUND_LIST_MEM[:] = _mem_backup          # mock 清单不得残留进程内存
        fd._CACHE_REPO = _repo_backup11
        _tmp11.cleanup()

    # ── V0.7.3 审查修复回归：先钉住静默错数据与口径错位 ──
    check("L1", "审查修复-同码股票默认走深市而非沪指",
          fd._tencent_prefix("000016") == "sz000016"
          and fd._tencent_prefix("000510") == "sz000510")

    _orig_review = (fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline)
    _refresh_calls = []
    try:
        _review_snap = {"_meta": {"code": "161725"}, "name": "离线样本", "nav_history": [
            {"date": "2026-09-30", "nav": 1.0, "daily_return": 0.0},
            {"date": "2026-10-01", "nav": 1.1, "daily_return": 10.0},
            {"date": "2026-10-02", "nav": 1.21, "daily_return": 10.0}]}
        fd.fund_snapshot = lambda q, **k: _refresh_calls.append(("snapshot", k)) or _review_snap
        fd.fund_holdings = lambda q, **k: _refresh_calls.append(("holdings", k)) or [
            {"period": "2026-09-30", "stock_code": "600519", "stock_name": "样本股", "nav_pct": 100.0}]
        fd._tencent_kline = lambda *a, **k: _refresh_calls.append(("kline", k)) or [
            {"date": "2026-09-30", "close": 100.0},
            {"date": "2026-10-01", "close": 110.0},
            {"date": "2026-10-02", "close": 121.0}]
        _review_nd = fd.nav_divergence("161725", force_refresh=True)
        check("L1", "审查修复-重仓探测同走势零背离",
              _review_nd["fund_return_pct"] == 21.0
              and _review_nd["holdings_weighted_return_pct"] == 21.0
              and _review_nd["divergence_pp"] == 0.0,
              f"基金{_review_nd['fund_return_pct']}% 股票{_review_nd['holdings_weighted_return_pct']}%")
        check("L1", "审查修复-重仓探测强制刷新全链路",
              len(_refresh_calls) == 3
              and all(kw.get("force_refresh") is True for _, kw in _refresh_calls),
              str(_refresh_calls))
    finally:
        fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline = _orig_review

    _review_relative = fd.relative_performance({
        "nav_history": [
            {"date": "2026-01-02", "nav": 1.0, "daily_return": 0.0},
            {"date": "2026-01-03", "nav": 1.1, "daily_return": 10.0}],
        "benchmark_curves": [{"name": "沪深300", "points": [
            {"date": "2026-01-01", "return_pct": 0.0},
            {"date": "2026-01-02", "return_pct": 10.0},
            {"date": "2026-01-03", "return_pct": 21.0}]}]}, min_overlap=1)
    check("L1", "审查修复-相对表现共同窗口",
          _review_relative["fund_return_pct"] == 10.0
          and _review_relative["benchmark_return_pct"] == 10.0
          and _review_relative["relative_return_pct"] == 0.0
          and _review_relative["window"]["start"] == "2026-01-02",
          str(_review_relative))

    class _ReviewResp:
        status_code = 200
        def __init__(self, text="", payload=None):
            self.text = text
            self.content = text.encode("utf-8")
            self._payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    _orig_snapshot_review = (fd.fund_list, fd.ttfund_get, fd._cache_read,
                             fd._cache_write, fd._cbreak_success)
    try:
        fd.fund_list = lambda **k: [{"code": "161725", "name": "测试基金",
                                     "type": "混合型", "pinyin": "cs"}]
        _bad_tables = (
            'var fS_name="测试基金";var fS_code="161725";var ishb=0;'
            'var Data_netWorthTrend=[{"x":1767225600000,"y":1.0,"equityReturn":"0"}];'
            'var Data_assetAllocation={"unexpected":true};'
            'var Data_holderStructure=[["只有表头"]];'
            'var Data_industryAllocation={"categories":[],"series":[{"data":"bad"}]};')
        fd.ttfund_get = lambda *a, **k: _ReviewResp(_bad_tables)
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd._cbreak_success = lambda *a: None
        _bad_snap = fd.fund_snapshot("161725")
        check("L1", "审查修复-快照全部表格解析失败均入missing",
              all(any(x.startswith(key) for x in _bad_snap["missing_datasets"])
                  for key in ("Data_assetAllocation", "Data_holderStructure",
                              "Data_industryAllocation")),
              str(_bad_snap["missing_datasets"]))
    finally:
        (fd.fund_list, fd.ttfund_get, fd._cache_read,
         fd._cache_write, fd._cbreak_success) = _orig_snapshot_review

    _orig_e6_review = (fd.resolve_fund_code, fd.ttfund_get, fd._cbreak_success)
    try:
        fd.resolve_fund_code = lambda c: c
        fd._cbreak_success = lambda *a: None
        fd.ttfund_get = lambda *a, **k: _ReviewResp(payload={"Datas": [
            {"FCODE": "000001", "NAV": "1.0"}]})
        try:
            _dedup = fd.fund_realtime_nav(["000001", "000001"])
            _dedup_ok = len(_dedup) == 1 and _dedup[0]["code"] == "000001"
        except RuntimeError:
            _dedup_ok = False
        check("L1", "审查修复-批量净值重复输入去重", _dedup_ok)
        fd.ttfund_get = lambda *a, **k: _ReviewResp(payload={"Datas": [
            {"FCODE": "110022", "NAV": "1.0"}]})
        expect_raise("L1", "审查修复-批量净值拒绝意外代码",
                     lambda: fd.fund_realtime_nav(["000001"]), "意外代码")
        fd.ttfund_get = lambda *a, **k: _ReviewResp(payload={"Datas": [
            {"FCODE": "000001", "NAV": "1.0"},
            {"FCODE": "000001", "NAV": "1.0"}]})
        expect_raise("L1", "审查修复-批量净值拒绝重复响应",
                     lambda: fd.fund_realtime_nav(["000001"]), "重复代码")
    finally:
        fd.resolve_fund_code, fd.ttfund_get, fd._cbreak_success = _orig_e6_review

    _orig_quarter_review = (fd._cninfo_post, fd._cache_read, fd._cache_write,
                            fd._cbreak_success)
    try:
        _ttl_seen = []
        fd._cache_read = lambda rel, max_age_hours=None: _ttl_seen.append(
            (rel, max_age_hours)) and None
        fd._cache_write = lambda *a, **k: None
        fd._cbreak_success = lambda *a: None
        fd._cninfo_post = lambda *a, **k: [
            {"SECCODE": f"{i:06d}", "SECNAME": f"股票{i}", "F001N": 1,
             "F002N": 1, "F003N": 1, "ENDDATE": "2026-09-30"} for i in range(100)]
        _latest_rd = fd._latest_quarter_end().replace("-", "")
        fd.fund_crowding(_latest_rd)
        fd.fund_crowding("20200930")
        check("L1", "审查修复-披露期短缓存稳定历史季末长缓存",
              _ttl_seen[0][1] == 1 and _ttl_seen[1][1] == 90 * 24,
              str(_ttl_seen))
    finally:
        (fd._cninfo_post, fd._cache_read, fd._cache_write,
         fd._cbreak_success) = _orig_quarter_review

    _orig_fee_review = (fd.ttfund_get, fd._cache_read, fd._cache_write,
                        fd._cbreak_success)
    try:
        fd._cache_read = lambda *a, **k: None
        _fee_writes = []
        fd._cache_write = lambda rel, payload: _fee_writes.append(rel)
        fd._cbreak_success = lambda *a: None
        fd.ttfund_get = lambda *a, **k: _ReviewResp(
            'var datas:[["000001","零费基金","公司","80000000","混合型",'
            '"2026-01-01","0%",""]],record:1,pages:1,curpage:1')
        _zero_fee = fd.fund_new_issues(force_refresh=True)
        check("L1", "审查修复-新发零费率保留且缓存升级",
              _zero_fee[0]["fee_pct"] == 0.0 and _fee_writes == ["new_issues_v2.json"],
              f"fee={_zero_fee[0]['fee_pct']} cache={_fee_writes}")
    finally:
        (fd.ttfund_get, fd._cache_read, fd._cache_write,
         fd._cbreak_success) = _orig_fee_review

    _orig_dj_review = (fd.resolve_fund_code, fd.ttfund_get)
    _old_cache_dir_review = fd.FUND_CACHE_DIR
    try:
        fd.resolve_fund_code = lambda c: c
        fd.ttfund_get = lambda *a, **k: _ReviewResp(payload={"data": {
            "fd_code": "000001", "fd_name": "样本基金"}})
        with tempfile.TemporaryDirectory(prefix="fund-danjuan-review-") as _dj_tmp:
            fd.FUND_CACHE_DIR = Path(_dj_tmp)
            _dj_coord = fd._reset_request_coordination(policies=_ZERO_POLICY)
            _dj_coord.signal_risk("danjuan", "review")
            _dj_coord.signal_risk("eastmoney", "review")
            fd.fund_detail_danjuan("000001")
            check("L1", "审查修复-蛋卷成功仅清蛋卷风控",
                  _dj_coord.snapshot("danjuan")["consecutive_risk"] == 0
                  and _dj_coord.snapshot("eastmoney")["consecutive_risk"] == 1)
        check("L1", "审查修复-蛋卷风控文案标签", fd._line_label("danjuan") == "蛋卷")
    finally:
        fd.resolve_fund_code, fd.ttfund_get = _orig_dj_review
        fd.FUND_CACHE_DIR = _old_cache_dir_review
        _fresh_coordination()

    # ── V0.7.3 边界补测：分块、合法空值与半开状态隔离 ──
    _orig_batch_edges = (fd.resolve_fund_code, fd.ttfund_get, fd._cbreak_success,
                         fd.tencent_quote, fd.fund_realtime_nav)
    try:
        fd.resolve_fund_code = lambda c: c
        fd._cbreak_success = lambda *a: None
        _chunks = []
        def _bulk_resp(url, params=None, **k):
            codes = params["FCodes"].split(",")
            _chunks.append(codes)
            return _ReviewResp(payload={"Datas": [
                {"FCODE": c, "NAV": "1.0"} for c in reversed(codes)]})
        fd.ttfund_get = _bulk_resp
        _bulk = fd.fund_realtime_nav([f"{i:06d}" for i in range(51)])
        check("L1", "审查修复-批量净值51只分块且保持输入序",
              len(_bulk) == 51 and [len(x) for x in _chunks] == [50, 1]
              and [x["code"] for x in _bulk] == [f"{i:06d}" for i in range(51)])
        _qdii_calls = []
        fd.tencent_quote = lambda codes: _qdii_calls.append(("quote", codes)) or {
            c: {"name": "纳指ETF", "price": 2.0} for c in codes}
        fd.fund_realtime_nav = lambda codes: _qdii_calls.append(("nav", codes)) or [
            {"code": c, "name": "纳指ETF", "nav": 1.0, "nav_date": "2026-01-01"}
            for c in codes]
        _qdii_dedup = fd.qdii_premium(["513100", "513100"])
        check("L1", "审查修复-QDII重复输入去重",
              len(_qdii_dedup) == 1 and _qdii_calls == [
                  ("quote", ["513100"]), ("nav", ["513100"])])
    finally:
        (fd.resolve_fund_code, fd.ttfund_get, fd._cbreak_success,
         fd.tencent_quote, fd.fund_realtime_nav) = _orig_batch_edges

    _orig_empty_snap = (fd.fund_list, fd.ttfund_get, fd._cache_read,
                        fd._cache_write, fd._cbreak_success)
    try:
        fd.fund_list = lambda **k: [{"code": "161725", "name": "测试基金",
                                     "type": "混合型", "pinyin": "cs"}]
        fd.ttfund_get = lambda *a, **k: _ReviewResp(
            'var fS_name="测试基金";var ishb=0;'
            'var Data_netWorthTrend=[{"x":1767225600000,"y":1.0}];'
            'var Data_assetAllocation=[];var Data_holderStructure=[];'
            'var Data_industryAllocation=[];')
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd._cbreak_success = lambda *a: None
        _empty_snap = fd.fund_snapshot("161725")
        check("L1", "审查修复-合法空表不误标解析失败",
              not any(x.endswith("(present,unparsed)")
                      for x in _empty_snap["missing_datasets"]))
    finally:
        (fd.fund_list, fd.ttfund_get, fd._cache_read,
         fd._cache_write, fd._cbreak_success) = _orig_empty_snap

    _orig_force_quarter = (fd._cninfo_post, fd._cache_read, fd._cache_write,
                           fd._cbreak_success)
    try:
        _force_reads = []
        fd._cache_read = lambda *a, **k: _force_reads.append(1)
        fd._cache_write = lambda *a, **k: None
        fd._cbreak_success = lambda *a: None
        fd._cninfo_post = lambda *a, **k: [
            {"SECCODE": f"{i:06d}", "SECNAME": f"股票{i}", "F001N": 1,
             "F002N": 1, "F003N": 1, "ENDDATE": "2026-09-30"} for i in range(100)]
        fd.fund_crowding(force_refresh=True)
        check("L1", "审查修复-季度强制刷新绕缓存", not _force_reads)
    finally:
        (fd._cninfo_post, fd._cache_read, fd._cache_write,
         fd._cbreak_success) = _orig_force_quarter

    _orig_empty_fee = (fd.ttfund_get, fd._cache_read, fd._cache_write,
                       fd._cbreak_success)
    try:
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd._cbreak_success = lambda *a: None
        fd.ttfund_get = lambda *a, **k: _ReviewResp(
            'var datas:[["000001","空费基金","公司","80000000","混合型",'
            '"2026-01-01","",""]],record:1,pages:1,curpage:1')
        check("L1", "审查修复-新发空费率仍为None",
              fd.fund_new_issues(force_refresh=True)[0]["fee_pct"] is None)
    finally:
        (fd.ttfund_get, fd._cache_read, fd._cache_write,
         fd._cbreak_success) = _orig_empty_fee

    _orig_base_edge = (fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline)
    try:
        fd.fund_snapshot = lambda *a, **k: {
            "_meta": {"code": "161725"}, "name": "缺基点样本", "nav_history": [
                {"date": "2026-09-30", "nav": 1.0},
                {"date": "2026-10-01", "nav": 1.1}]}
        fd.fund_holdings = lambda *a, **k: [
            {"period": "2026-09-30", "stock_code": "600519", "nav_pct": 100.0}]
        fd._tencent_kline = lambda *a, **k: [
            {"date": "2026-10-01", "close": 100.0},
            {"date": "2026-10-02", "close": 110.0}]
        expect_raise("L1", "审查修复-重仓探测缺共同基点拒绝计算",
                     lambda: fd.nav_divergence("161725"), "无一可取")
    finally:
        fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline = _orig_base_edge

    class _ReviewClock:
        def __init__(self):
            self.now = 1_000_000.0
        def time(self):
            return self.now
        def sleep(self, seconds):
            self.now += seconds

    _orig_half_dj = (fd.resolve_fund_code, fd.ttfund_get)
    _old_half_dir = fd.FUND_CACHE_DIR
    try:
        fd.resolve_fund_code = lambda c: c
        fd.ttfund_get = lambda *a, **k: _ReviewResp(payload={"data": {
            "fd_code": "000001", "fd_name": "样本基金"}})
        with tempfile.TemporaryDirectory(prefix="fund-half-open-review-") as _half_tmp:
            fd.FUND_CACHE_DIR = Path(_half_tmp)
            _half_clock = _ReviewClock()
            _half_coord = fd._reset_request_coordination(
                clock=_half_clock, policies=_ZERO_POLICY)
            for _ in range(3):
                _half_coord.signal_risk("eastmoney", "review")
                _half_coord.signal_risk("danjuan", "review")
            _half_clock.now += 601
            check("L1", "审查修复-半开前置状态",
                  _half_coord.snapshot("eastmoney")["circuit"] == "HALF_OPEN"
                  and _half_coord.snapshot("danjuan")["circuit"] == "HALF_OPEN")
            fd.fund_detail_danjuan("000001")
            check("L1", "审查修复-蛋卷成功不关东财半开",
                  _half_coord.snapshot("eastmoney")["circuit"] == "HALF_OPEN")
            check("L1", "审查修复-蛋卷半开成功恢复自身",
                  _half_coord.snapshot("danjuan")["circuit"] == "CLOSED")
    finally:
        fd.resolve_fund_code, fd.ttfund_get = _orig_half_dj
        fd.FUND_CACHE_DIR = _old_half_dir
        _fresh_coordination()

    # ── 独立复审回归：终点、季度切换与 legacy provider ──
    _orig_end_edge = (fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline)
    try:
        fd.fund_snapshot = lambda *a, **k: {
            "_meta": {"code": "161725"}, "name": "缺终点样本", "nav_history": [
                {"date": "2026-09-30", "nav": 1.0, "daily_return": 0.0},
                {"date": "2026-10-01", "nav": 1.1, "daily_return": 10.0},
                {"date": "2026-10-02", "nav": 1.21, "daily_return": 10.0}]}
        fd.fund_holdings = lambda *a, **k: [
            {"period": "2026-09-30", "stock_code": "600519", "nav_pct": 100.0}]
        fd._tencent_kline = lambda *a, **k: [
            {"date": "2026-09-30", "close": 100.0},
            {"date": "2026-10-01", "close": 110.0}]
        expect_raise("L1", "复审-重仓探测缺共同终点拒绝计算",
                     lambda: fd.nav_divergence("161725"), "无一可取")
    finally:
        fd.fund_snapshot, fd.fund_holdings, fd._tencent_kline = _orig_end_edge

    try:
        _transition_ttl = [fd._quarter_cache_ttl_hours(previous, today=today)
                           for previous, today in (
                               ("20251231", date(2026, 3, 31)),
                               ("20260331", date(2026, 6, 30)),
                               ("20260630", date(2026, 9, 30)),
                               ("20260930", date(2026, 12, 31)))]
    except TypeError:
        _transition_ttl = []
    check("L1", "复审-季度切换上一季仍短缓存", _transition_ttl == [1, 1, 1, 1],
          str(_transition_ttl))
    expect_raise("L1", "复审-未来季末拒绝",
                 lambda: fd._quarter_cache_ttl_hours("20261231", today=date(2026, 1, 1)),
                 "未来")
    _orig_future_post = fd._cninfo_post
    try:
        _future_calls = []
        fd._cninfo_post = lambda *a, **k: _future_calls.append(1) or []
        _future_blocked = 0
        for _public_fn in (fd.fund_crowding, fd.fund_industry_allocation):
            try:
                _public_fn("20991231", force_refresh=True)
            except ValueError as exc:
                _future_blocked += "未来" in str(exc)
        check("L1", "复审-公开函数强制刷新未来季末零请求",
              _future_blocked == 2 and not _future_calls,
              f"blocked={_future_blocked} transport={len(_future_calls)}")
    finally:
        fd._cninfo_post = _orig_future_post

    _orig_malformed_e6 = (fd.resolve_fund_code, fd.ttfund_get, fd._cbreak_success)
    try:
        fd.resolve_fund_code = lambda c: c
        fd._cbreak_success = lambda *a: None
        fd.ttfund_get = lambda *a, **k: _ReviewResp(payload={"Datas": [
            {"FCODE": "000001", "NAV": "1.0"}, {"NAV": "9.9"}]})
        expect_raise("L1", "复审-批量净值拒绝畸形记录",
                     lambda: fd.fund_realtime_nav(["000001"]), "畸形记录")
    finally:
        fd.resolve_fund_code, fd.ttfund_get, fd._cbreak_success = _orig_malformed_e6

    _orig_echarts_empty = (fd.fund_list, fd.ttfund_get, fd._cache_read,
                           fd._cache_write, fd._cbreak_success)
    try:
        fd.fund_list = lambda **k: [{"code": "161725", "name": "测试基金",
                                     "type": "混合型", "pinyin": "cs"}]
        fd.ttfund_get = lambda *a, **k: _ReviewResp(
            'var fS_name="测试基金";var ishb=0;'
            'var Data_netWorthTrend=[{"x":1767225600000,"y":1.0}];'
            'var Data_assetAllocation={"categories":[],"series":[]};'
            'var Data_holderStructure={"categories":[],"series":[]};'
            'var Data_industryAllocation={"categories":[],"series":[]};')
        fd._cache_read = lambda *a, **k: None
        fd._cache_write = lambda *a, **k: None
        fd._cbreak_success = lambda *a: None
        _echarts_snap = fd.fund_snapshot("161725")
        check("L1", "复审-ECharts合法空结构不误标",
              not any(x.endswith("(present,unparsed)")
                      for x in _echarts_snap["missing_datasets"]))
    finally:
        (fd.fund_list, fd.ttfund_get, fd._cache_read,
         fd._cache_write, fd._cbreak_success) = _orig_echarts_empty

    _orig_hold_force = (fd.fund_list, fd.ttfund_get, fd._cache_read, fd._cache_write)
    try:
        fd.fund_list = lambda **k: [{"code": "161725", "name": "测试基金",
                                     "type": "混合型", "pinyin": "cs"}]
        fd._cache_read = lambda *a, **k: [{"period": "old", "stock_code": "cached"}]
        fd._cache_write = lambda *a, **k: None
        fd.ttfund_get = lambda *a, **k: _jjcc_resp(_th_std, _row_std)
        _forced_hold = fd.fund_holdings("161725", force_refresh=True)
        check("L1", "复审-持仓自身强制刷新绕缓存",
              _forced_hold[0]["stock_code"] == "600519"
              and _forced_hold[0]["period"] == "2026-09-30")
    finally:
        fd.fund_list, fd.ttfund_get, fd._cache_read, fd._cache_write = _orig_hold_force

    _orig_legacy = (fd.TT_SESSION.get, fd.resolve_fund_code, fd._cross_proc_throttle)
    _old_legacy_dir = fd.FUND_CACHE_DIR
    try:
        fd._cross_proc_throttle = lambda *a, **k: None
        with tempfile.TemporaryDirectory(prefix="fund-legacy-danjuan-") as _legacy_tmp:
            fd.FUND_CACHE_DIR = Path(_legacy_tmp)
            fd._reset_request_coordination(policies=_ZERO_POLICY)
            fd._teardown_request_coordination()
            _legacy_calls = []
            def _legacy_forbidden(*a, **k):
                _legacy_calls.append(1)
                response = _ReviewResp("forbidden")
                response.status_code = 403
                return response
            fd.TT_SESSION.get = _legacy_forbidden
            for _ in range(3):
                try:
                    _legacy_resp = fd.ttfund_get("https://danjuanfunds.com/djapi/fund/000001")
                    fd.assert_not_risk_control(_legacy_resp, "danjuan", "legacy")
                except fd.RiskControlError:
                    pass
            _legacy_resp = None
            try:
                _legacy_resp = fd.ttfund_get("https://danjuanfunds.com/djapi/fund/000001")
                fd.assert_not_risk_control(_legacy_resp, "danjuan", "legacy")
                _legacy_blocked = False
            except fd.CircuitOpenError:
                _legacy_blocked = True
            except fd.RiskControlError:
                _legacy_blocked = False
            check("L1", "复审-legacy蛋卷第四次零transport",
                  _legacy_blocked and len(_legacy_calls) == 3,
                  f"transport={len(_legacy_calls)}")
        with tempfile.TemporaryDirectory(prefix="fund-legacy-lines-") as _legacy_tmp2:
            fd.FUND_CACHE_DIR = Path(_legacy_tmp2)
            fd._reset_request_coordination(policies=_ZERO_POLICY)
            fd._teardown_request_coordination()
            fd.resolve_fund_code = lambda c: c
            fd.TT_SESSION.get = lambda *a, **k: _ReviewResp(payload={"data": {
                "fd_code": "000001", "fd_name": "样本基金"}})
            fd._cbreak_signal("tt", "seed")
            fd.fund_detail_danjuan("000001")
            check("L1", "复审-legacy蛋卷成功不清东财", fd._cbreak_state["tt"][0] == 1)
    finally:
        fd.TT_SESSION.get, fd.resolve_fund_code, fd._cross_proc_throttle = _orig_legacy
        fd.FUND_CACHE_DIR = _old_legacy_dir
        _fresh_coordination()


# ════════════════════ V0.8.0 中证全收益指数层（固定样本零网络）════════════
def test_csindex_offline():
    check("L1", "中证-代码归一与安全字符",
          fd._csindex_code(" h00300 ") == "H00300"
          and fd._csindex_code("932000cny010") == "932000CNY010")
    expect_raise("L1", "中证-非法代码拒绝", lambda: fd._csindex_code("../H00300"), "代码")
    _cs_policy = fd._COORDINATOR.policy("csindex")
    check("L1", "中证防封-独立配额单并发与慢请求租约",
          _cs_policy.max_requests == 120 and _cs_policy.window_seconds == 300
          and _cs_policy.max_in_flight == 1 and _cs_policy.lease_seconds > 120)
    fd._reset_request_coordination()               # 真实策略解析（沙箱可能处于零间隔态）
    _cs_real = fd._COORDINATOR.policy("csindex")
    _fresh_coordination()                          # 复位零间隔——后续 fake 流程不节流
    from fund_data_core.policies import DEFAULT_POLICIES as _DP
    _cs_default = _DP["csindex"]                   # V0.8.1：core 默认表↔主模块覆盖双向锁
    check("L1", "中证防封-core默认表与主模块覆盖一致",
          (_cs_default.max_requests, _cs_default.window_seconds, _cs_default.min_interval,
           _cs_default.max_in_flight, _cs_default.lease_seconds)
          == (_cs_real.max_requests, _cs_real.window_seconds, _cs_real.min_interval,
              _cs_real.max_in_flight, _cs_real.lease_seconds)
          == (120, 300, fd.CSINDEX_MIN_INTERVAL, 1, 180),
          "单一来源两处一致（V0.8.1 双向锁）")
    _deriv = [
        {"indexCode": "H00300", "indexNameCn": "300收益", "indexNameEn": "CSI 300 TRI"},
        {"indexCode": "N00300", "indexNameCn": "沪深300净收益", "indexNameEn": "CSI 300 NTRI"},
        {"indexCode": "000300HKD01", "indexNameCn": "沪深300(全)HKD",
         "indexNameEn": "CSI 300 TRI(HKD)"},
    ]
    _cands = fd._csindex_derivative_candidates(_deriv)
    check("L1", "中证-TRI候选排除净收益与外币",
          len(_cands) == 1 and _cands[0]["index_code"] == "H00300")

    def _info(code, name, en, asset="stock"):
        return {"indexCode": code, "indexFullNameCn": name, "indexFullNameEn": en,
                "indexShortNameCn": name, "indexShortNameEn": en,
                "currencyCn": "人民币", "basicDate": "2004-12-31",
                "basicIndex": 1000.0, "indexType": asset}

    _orig_request = fd._csindex_request
    try:
        _calls = []

        def _universe_fake(path, params=None, payload=None, timeout=30):
            _calls.append(payload["pager"]["pageNum"])
            page = payload["pager"]["pageNum"]
            raw = [
                {"indexCode": "000300", "indexName": "沪深300", "indexSeries": "中证系列指数",
                 "assetsClassify": "股票", "currency": "人民币"},
                {"indexCode": "H30269", "indexName": "红利低波", "indexSeries": "中证系列指数",
                 "assetsClassify": "股票", "currency": "人民币"},
                {"indexCode": "930001", "indexName": "样本债指", "indexSeries": "中证系列指数",
                 "assetsClassify": "固定收益", "currency": "人民币"},
            ]
            data = raw[:2] if page == 1 else raw[2:]
            return {"code": "200", "success": True, "total": 3, "size": 2, "data": data}

        fd._csindex_request = _universe_fake
        _uni = fd.csindex_universe(force_refresh=True)
        check("L1", "中证清单-分页完整性",
              len(_uni) == 3 and _calls == [1, 2]
              and _uni[0]["index_code"] == "000300")
        _n_calls = len(_calls)
        _stock = fd.csindex_universe(asset_class="股票")
        check("L1", "中证清单-缓存与本地筛选",
              len(_stock) == 2 and len(_calls) == _n_calls)

        def _mapping_fake(path, params=None, payload=None, timeout=30):
            if path.startswith("/indexInfo/index-basic-info/"):
                code = path.rsplit("/", 1)[-1]
                infos = {
                    "000300": _info(code, "沪深300指数", "CSI 300 Index"),
                    "H00300": _info(code, "沪深300全收益指数", "CSI 300 Total Return Index"),
                    "H30269": _info(code, "中证红利低波动指数", "CSI Dividend Low Volatility Index"),
                    "H20269": _info(code, "中证红利低波动全收益指数", "CSI Dividend Low Volatility TRI"),
                    "932000": _info(code, "中证2000指数", "CSI 2000 Index"),
                    "932000CNY010": _info(code, "中证2000全收益指数", "CSI 2000 TRI"),
                    "932047": _info(code, "中证REITs全收益指数", "CSI REITs Total Return Index"),
                    "930001": _info(code, "样本债券指数", "CSI Sample Bond Index", "bond"),
                    "930002": _info(code, "无全收益股票指数", "CSI No TRI Index"),
                    "930003": _info(code, "歧义股票指数", "CSI Ambiguous Index"),
                }
                return {"code": "200", "success": True, "data": infos[code]}
            if path == "/perf/get-derivative-index":
                code = params["indexCode"]
                rows = {
                    "000300": [_deriv[0], _deriv[1], _deriv[2]],
                    "H30269": [{"indexCode": "H20269", "indexNameCn": "红利低波全收益",
                                "indexNameEn": "CSI Dividend Low VolatilityTRI"}],
                    "932000": [{"indexCode": "932000CNY010", "indexNameCn": "中证2000全收益",
                                "indexNameEn": "CSI 2000 TRI"}],
                    "930001": [], "930002": [],
                    "930003": [_deriv[0], {"indexCode": "H20269", "indexNameCn": "另一全收益",
                                           "indexNameEn": "Other TRI"}],
                }[code]
                return {"code": "200", "success": True, "data": rows}
            raise AssertionError(path)

        fd._csindex_request = _mapping_fake
        _m300 = fd.csindex_resolve_total_return("000300", force_refresh=True)
        _mlv = fd.csindex_resolve_total_return("H30269", force_refresh=True)
        _m2000 = fd.csindex_resolve_total_return("932000", force_refresh=True)
        check("L1", "中证映射-三类官方特殊代码",
              _m300["total_return_code"] == "H00300"
              and _mlv["total_return_code"] == "H20269"
              and _m2000["total_return_code"] == "932000CNY010"
              and all(x["status"] == "mapped" for x in (_m300, _mlv, _m2000)))
        check("L1", "中证映射-本身即全收益",
              fd.csindex_resolve_total_return("932047", force_refresh=True)["status"] == "already_total")
        _no_tri = fd.csindex_resolve_total_return("930002", force_refresh=True)
        _no_rt = fd.csindex_resolve_total_return("930001", force_refresh=True)
        check("L1", "中证映射-无TRI与未知口径分离",
              _no_tri["status"] == "no_total_return"
              and _no_rt["status"] == "return_type_unknown"
              and _no_tri["return_type"] == "price_return"   # V0.8.1 修：无 TR 衍生的股票
              and _no_rt["return_type"] == "unknown"         # 指数是价格指数，V0.8.0 误标
              and _no_tri["total_return_code"] is None)      # total_return 会误导消费方
        # V0.8.1：V0.8.0 落盘的旧错值缓存（no_total_return+total_return）视为 miss 自愈
        fd._cache_write("csindex/mappings/930002.json",
                        {"base_index_code": "930002", "total_return_code": None,
                         "return_type": "total_return", "status": "no_total_return",
                         "source": "csindex/derivative+basic-info"})
        _healed = fd.csindex_resolve_total_return("930002")
        check("L1", "中证映射-V0.8.0旧错值缓存自愈",
              _healed["return_type"] == "price_return"
              and _healed["status"] == "no_total_return")
        check("L1", "中证映射-多候选不猜",
              fd.csindex_resolve_total_return("930003", force_refresh=True)["status"] == "ambiguous")

        _valid = ["20240102", "20240103", "20240104"]

        def _perf_row(day, close, change=None, pct=None):
            return {"tradeDate": day, "indexCode": "H00300",
                    "indexNameCnAll": "沪深300全收益指数",
                    "indexNameEnAll": "CSI 300 Total Return Index",
                    "close": close, "change": change, "changePct": pct,
                    "tradingVol": 100, "tradingValue": 20.5, "consNumber": 300, "peg": 10.57}

        def _history_fake(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True, "data": _valid}
            if path == "/perf/index-perf":
                rows = [_perf_row("20240101", 100, 0, 0),
                        _perf_row("20240102", 100, 0, 0),
                        _perf_row("20240103", 101, 1, 1),
                        _perf_row("20240104", 99.99, -1.01, -1)]
                return {"code": "200", "success": True, "data": rows}
            return _mapping_fake(path, params, payload, timeout)

        fd._csindex_request = _history_fake
        _hist = fd.csindex_total_return_daily("000300", "20240101", "20240110",
                                               force_refresh=True)
        check("L1", "中证历史-占位日清洗与字段归一",
              len(_hist) == 3 and _hist[0]["date"] == "2024-01-02"
              and _hist[-1]["date"] == "2024-01-04"
              and _hist[0]["pe_ttm"] == 10.57
              and _hist[0]["return_type"] == "total_return")
        _before = fd.CSINDEX_REQUEST_COUNT[0]
        _hist2 = fd.csindex_total_return_daily("000300", "20240102", "20240103")
        check("L1", "中证历史-年度缓存二次零请求",
              len(_hist2) == 2 and fd.CSINDEX_REQUEST_COUNT[0] == _before)
        # V0.8.1：共享日线缓存按调用方视角重写 base_index_code（修串台元数据）
        _tr_view = fd.csindex_total_return_daily("H00300", "20240101", "20240110")
        check("L1", "中证历史-共享缓存按调用方重写base",
              len(_tr_view) == 3 and all(x["base_index_code"] == "H00300" for x in _tr_view)
              and _tr_view[0]["index_code"] == "H00300",
              str([(x["date"], x["base_index_code"]) for x in _tr_view]))
        # V0.8.1：损坏年度缓存（close 非数值）视为 miss 重取修复，不静默复用
        fd._cache_write("csindex/daily/H00300_2024.json",
                        [{"date": "2024-01-02", "close": "坏值", "index_code": "H00300"}])
        _corrupt_calls = [0]

        def _corrupt_fake(path, params=None, payload=None, timeout=30):
            if path in ("/perf/index-perf-tradeDate", "/perf/index-perf"):
                _corrupt_calls[0] += 1
            return _history_fake(path, params, payload, timeout)

        fd._csindex_request = _corrupt_fake
        _repaired = fd.csindex_total_return_daily("000300", "20240101", "20240110")
        check("L1", "中证历史-损坏年度缓存视为miss重取",
              len(_repaired) == 3 and _corrupt_calls[0] >= 1
              and _repaired[0]["close"] == 100.0
              and all(x["base_index_code"] == "000300" for x in _repaired),
              f"calls={_corrupt_calls[0]} rows={len(_repaired)}")
        fd._csindex_request = _history_fake

        _mapping = _m300

        _missing_calls = [0]

        def _missing_fake(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True, "data": _valid}
            if path == "/perf/index-perf":
                _missing_calls[0] += 1
                rows = [_perf_row("20240102", 100, 0, 0),
                        _perf_row("20240103", 101, 1, 1),
                        _perf_row("20240104", 99.99, -1.01, -1)]
                if _missing_calls[0] == 1:
                    rows.pop(1)
                return {"code": "200", "success": True, "data": rows}
            raise AssertionError(path)

        fd._csindex_request = _missing_fake
        _filled, _report = fd._csindex_download_history(
            _mapping, "2024-01-01", "2024-01-10", force_refresh=True)
        check("L1", "中证历史-缺失自动按年重取",
              len(_filled) == 3 and _report["chunked"] and not _report["missing_dates"])

        # V0.8.1：当日行情盘后延迟发布（tradeDate 清单先行含今天）→ 不计缺失不炸，
        # pending_today 如实入报告；行内已带回时自然入库
        _today_iso = fd.datetime.now(fd._BJ_TZ).date().isoformat()
        _today_compact = _today_iso.replace("-", "")

        def _premarket_fake(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True,
                        "data": ["20240102", "20240103", _today_compact]}
            if path == "/perf/index-perf":
                return {"code": "200", "success": True, "data": [
                    _perf_row("20240102", 100, 0, 0),
                    _perf_row("20240103", 101, 1, 1)]}    # 当日 perf 未发布
            raise AssertionError(path)

        fd._csindex_request = _premarket_fake
        _pm_rows, _pm_report = fd._csindex_download_history(
            _mapping, "2024-01-01", _today_iso, force_refresh=True)
        check("L1", "中证历史-当日未发布不计缺失",
              len(_pm_rows) == 2 and _pm_report["pending_today"] == _today_iso
              and not _pm_report["missing_dates"] and not _pm_report["chunked"],
              str(_pm_report))

        # V0.8.3：V0.8.2 遗留的空清单缓存不得绕过非空守卫
        fd._cache_write("csindex/trade_dates/H00300.json", [])
        fd._csindex_request = _history_fake
        _old_empty_cache83 = fd._csindex_trade_dates("H00300")
        check("L1", "V0.8.3中证-旧空交易日缓存自动重取",
              _old_empty_cache83 == ["2024-01-02", "2024-01-03", "2024-01-04"],
              str(_old_empty_cache83))

        # V0.8.3：陈旧交易日清单先强制刷新；仍陈旧则显式记录，不能静默吞当日行
        (fd.FUND_CACHE_DIR / "csindex/trade_dates/H00300.json").unlink(missing_ok=True)
        _trade_date_calls83 = [0]
        def _stale_dates_fake83(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                _trade_date_calls83[0] += 1
                return {"code": "200", "success": True, "data": ["20240102", "20240103"]}
            if path == "/perf/index-perf":
                return {"code": "200", "success": True, "data": [
                    _perf_row("20240102", 100, 0, 0),
                    _perf_row("20240103", 101, 1, 1),
                    _perf_row(_today_compact, 102, 1, 1)]}
            raise AssertionError(path)
        fd._csindex_request = _stale_dates_fake83
        _stale_rows83, _stale_report83 = fd._csindex_download_history(
            _mapping, "2024-01-01", _today_iso)
        check("L1", "V0.8.3中证-交易日清单陈旧触发刷新并留痕",
              _trade_date_calls83[0] == 2 and _stale_report83["trade_dates_stale"]
              and all(x["date"] != _today_iso for x in _stale_rows83),
              f"calls={_trade_date_calls83[0]} report={_stale_report83}")

        def _bad_numeric(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True, "data": _valid[:2]}
            rows = [_perf_row("20240102", 100, 0, 0), _perf_row("20240103", 101, 9, 9)]
            return {"code": "200", "success": True, "data": rows}

        fd._csindex_request = _bad_numeric
        expect_raise("L1", "中证历史-数值矛盾拒绝缓存",
                     lambda: fd._csindex_download_history(
                         _mapping, "2024-01-01", "2024-01-03", force_refresh=True),
                     "矛盾")

        fd._csindex_request = _history_fake
        expect_raise("L1", "中证同步-全量必须显式",
                     lambda: fd.csindex_sync_total_return(), "二选一")
        _sync = fd.csindex_sync_total_return(codes=["000300"], start_date="20240101",
                                             end_date="20240110", resume=False)
        check("L1", "中证同步-限定代码与报告",
              _sync["total"] == 1 and _sync["ok"] == 1 and _sync["requests"] == 0)
        _sync2 = fd.csindex_sync_total_return(codes=["000300"], start_date="20240101",
                                              end_date="20240110", resume=True)
        check("L1", "中证同步-新鲜成功记录跳过",
              _sync2["skipped"] == 1 and _sync2["requests"] == 0)

        def _cross_year_fake(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True, "data": ["20231229", "20240102"]}
            if params["startDate"].startswith("2023"):
                rows = [_perf_row("20231229", 100, 0, 0)]
            else:
                rows = [_perf_row("20240102", 101, 1, 9)]
            if params["startDate"] == "20231229" and params["endDate"] == "20240110":
                rows = rows[:0]
            return {"code": "200", "success": True, "data": rows}

        fd._csindex_request = _cross_year_fake
        expect_raise("L1", "中证历史-分年边界首日仍验数值",
                     lambda: fd._csindex_download_history(
                         _mapping, "2023-12-01", "2024-01-10", force_refresh=True),
                     "矛盾")

        _year = date.today().year
        (fd.FUND_CACHE_DIR / "csindex/trade_dates/H00300.json").unlink(missing_ok=True)
        _current_rel = f"csindex/daily/H00300_{_year}.json"
        _seed = dict(_hist[0], date=f"{_year}-01-02")
        _removed_seed = dict(_hist[0], date=f"{_year}-01-01")
        fd._cache_write(_current_rel, [_removed_seed, _seed])
        _current_path = fd.FUND_CACHE_DIR / _current_rel
        _old_mtime = time.time() - 21 * 3600
        os.utime(_current_path, (_old_mtime, _old_mtime))
        _increment_params = []

        def _increment_fake(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True,
                        "data": [f"{_year}0102", f"{_year}0105"]}
            if path == "/perf/index-perf":
                _increment_params.append(params)
                return {"code": "200", "success": True, "data": [
                    _perf_row(f"{_year}0102", 100, 0, 0),
                    _perf_row(f"{_year}0105", 101, 1, 1)]}
            return _mapping_fake(path, params, payload, timeout)

        fd._csindex_request = _increment_fake
        _current = fd.csindex_total_return_daily("000300", f"{_year}0101")
        _current_report = fd._cache_read(f"csindex/reports/H00300_latest.json", None)
        check("L1", "中证历史-当前年过期后末日增量缝合",
              len(_current) == 2 and _increment_params[0]["startDate"] == f"{_year}0102"
              and _current_report["incremental"] and _current[-1]["date"] == f"{_year}-01-05",
              f"rows={[(x['date'], x['close']) for x in _current]} params={_increment_params} report={_current_report}")

        # V0.8.1 收口审查：盘前落盘的当年文件缺当日行 → mtime 回拨 18h（≈2h 后
        # 过期走增量补当日行），不占满 20h TTL
        _pm_today = fd.datetime.now(fd._BJ_TZ).date().isoformat()
        _pm_compact = _pm_today.replace("-", "")

        def _pm_daily_fake(path, params=None, payload=None, timeout=30):
            if path == "/perf/index-perf-tradeDate":
                return {"code": "200", "success": True,
                        "data": [f"{_year}0102", _pm_compact]}
            if path == "/perf/index-perf":
                return {"code": "200", "success": True,
                        "data": [_perf_row(f"{_year}0102", 100, 0, 0)]}   # 缺当日
            return _mapping_fake(path, params, payload, timeout)

        os.utime(_current_path, (_old_mtime, _old_mtime))  # 当年文件回到过期态
        fd._cache_write(f"csindex/trade_dates/H00300.json",
                        [f"{_year}-01-02", _pm_today])     # 有效日期含今天（ISO——
        # 缓存命中路径原样返回，生产写入即 ISO；写紧凑格式会令 valid 集与行日期
        # 永不匹配、expected 静默为空——本测试首版即踩此坑，留注防回归）
        fd._csindex_request = _pm_daily_fake
        _pm_daily = fd.csindex_total_return_daily("000300", f"{_year}0101")
        _pm_mtime = os.path.getmtime(fd.FUND_CACHE_DIR / f"csindex/daily/H00300_{_year}.json")
        check("L1", "中证历史-盘前落盘当年文件mtime回拨",
              len(_pm_daily) == 1 and _pm_daily[0]["date"] == f"{_year}-01-02"
              and _pm_mtime < time.time() - 17 * 3600
              and fd._cache_read(f"csindex/reports/H00300_latest.json", None)["pending_today"]
              == _pm_today,
              f"rows={len(_pm_daily)} mtime 距今 {(time.time() - _pm_mtime) / 3600:.1f}h")

        _orig_resolve_sync, _orig_daily_sync = (fd.csindex_resolve_total_return,
                                                fd.csindex_total_return_daily)
        try:
            _sync_calls = []

            def _sync_resolve(code):
                return {"base_index_code": code, "total_return_code": code,
                        "status": "mapped", "currency": "CNY", "basic_date": "2024-01-01"}

            def _sync_daily(code, **kwargs):
                _sync_calls.append(code)
                if code == "SYNC01":
                    raise RuntimeError("普通失败")
                return [dict(_hist[0], index_code=code, base_index_code=code)]

            fd.csindex_resolve_total_return = _sync_resolve
            fd.csindex_total_return_daily = _sync_daily
            _sync3 = fd.csindex_sync_total_return(codes=["SYNC01", "SYNC02"], resume=False)
            check("L1", "中证同步-普通失败后续跑冷路径",
                  _sync_calls == ["SYNC01", "SYNC02"]
                  and _sync3["error"] == 1 and _sync3["ok"] == 1)

            _risk_calls = []

            def _sync_risk(code, **kwargs):
                _risk_calls.append(code)
                raise fd.RiskControlError("mock 风控停止")

            fd.csindex_total_return_daily = _sync_risk
            expect_raise("L1", "中证同步-风控写清单并停止后续",
                         lambda: fd.csindex_sync_total_return(
                             codes=["RISK01", "RISK02"], resume=False), "风控")
            _risk_manifest = fd._cache_read("csindex/sync_manifest.json", None)
            check("L1", "中证同步-风控零第二项且已落盘",
                  _risk_calls == ["RISK01"]
                  and _risk_manifest["jobs"]["RISK01"]["status"] == "error"
                  and "RISK02" not in _risk_manifest["jobs"])

            _sync_calls[:] = []
            expect_raise("L1", "中证同步-非法日期零业务调用",
                         lambda: fd.csindex_sync_total_return(
                             codes=["SYNC01"], start_date="20241231", end_date="20240101"),
                         "零网络")
            check("L1", "中证同步-非法日期不进入指数循环", not _sync_calls)
            expect_raise("L1", "中证同步-未来起始日默认终点也零网络",
                         lambda: fd.csindex_sync_total_return(
                             codes=["SYNC01"], start_date="20990101"), "有效结束日")
            check("L1", "中证同步-未来起始日仍不进入循环", not _sync_calls)

            _upstream_calls = []

            def _sync_upstream_down(code, **kwargs):
                _upstream_calls.append(code)
                raise fd.requests.RequestException("mock 持续断网")

            fd.csindex_total_return_daily = _sync_upstream_down
            expect_raise("L1", "中证同步-连续上游三败停止",
                         lambda: fd.csindex_sync_total_return(
                             codes=["UP0001", "UP0002", "UP0003", "UP0004"], resume=False),
                         "连续 3 次")
            _upstream_manifest = fd._cache_read("csindex/sync_manifest.json", None)
            check("L1", "中证同步-上游三败零第四项且落盘",
                  _upstream_calls == ["UP0001", "UP0002", "UP0003"]
                  and _upstream_manifest["jobs"]["UP0003"]["status"] == "error"
                  and "UP0004" not in _upstream_manifest["jobs"])

            _data_calls = []

            def _sync_data_errors(code, **kwargs):
                _data_calls.append(code)
                if code != "DA0004":
                    raise fd.UpstreamFormatError("单指数内容校验失败")
                return [dict(_hist[0], index_code=code, base_index_code=code)]

            fd.csindex_total_return_daily = _sync_data_errors
            _data_sync = fd.csindex_sync_total_return(
                codes=["DA0001", "DA0002", "DA0003", "DA0004"], resume=False)
            check("L1", "中证同步-单指数内容错误不误停",
                  _data_calls == ["DA0001", "DA0002", "DA0003", "DA0004"]
                  and _data_sync["error"] == 3 and _data_sync["ok"] == 1)

            _mixed_calls = []

            def _sync_mixed_errors(code, **kwargs):
                _mixed_calls.append(code)
                if code.startswith("UP"):
                    raise fd.requests.RequestException("上游失败")
                raise fd.UpstreamFormatError("本地内容失败")

            fd.csindex_total_return_daily = _sync_mixed_errors
            _mixed = fd.csindex_sync_total_return(
                codes=["UP1001", "LO0001", "UP1002", "UP1003"], resume=False)
            check("L1", "中证同步-本地错误重置上游连击",
                  len(_mixed_calls) == 4 and _mixed["error"] == 4)
            # V0.8.1：manifest 落盘锁 + 异窗口旧 job 清理（清单有界）
            fd.csindex_total_return_daily = _sync_daily   # 上面 mixed mock 对 SYNC02 全败，重指 ok mock
            fd._cache_write("csindex/sync_manifest.json",
                            {"jobs": {"OLD01": {"status": "ok", "start_date": "20200101",
                                                "end_date": "20201231",
                                                "fetched_at": "2020-01-01T00:00:00+08:00"}}})
            fd.csindex_sync_total_return(codes=["SYNC02"], resume=False)
            _pruned = fd._cache_read("csindex/sync_manifest.json", None)
            check("L1", "中证同步-清单异窗口旧job清理",
                  "OLD01" not in _pruned["jobs"] and "SYNC02" in _pruned["jobs"]
                  and _pruned["jobs"]["SYNC02"]["status"] == "ok")
        finally:
            fd.csindex_resolve_total_return = _orig_resolve_sync
            fd.csindex_total_return_daily = _orig_daily_sync
    finally:
        fd._csindex_request = _orig_request
        _fresh_coordination()

    class _CSIResponse:
        def __init__(self, status, body):
            self.status_code = status
            self.content = json.dumps(body).encode("utf-8")

        def raise_for_status(self):
            return None

    _orig_get = fd.CSINDEX_SESSION.get
    try:
        fd.CSINDEX_SESSION.get = lambda *a, **k: _CSIResponse(
            200, {"code": "400", "msg": "Parameter Errors", "success": False})
        expect_raise("L1", "中证请求-HTTP200业务400拒绝",
                     lambda: fd._csindex_request("/perf/index-perf", timeout=1),
                     "业务")
        _fresh_coordination()
        fd.CSINDEX_SESSION.get = lambda *a, **k: _CSIResponse(403, {})
        expect_raise("L1", "中证请求-403风控分流",
                     lambda: fd._csindex_request("/perf/index-perf", timeout=1),
                     "403")
        _fresh_coordination()
        _legacy_transport = [0]

        def _legacy_forbidden(*args, **kwargs):
            _legacy_transport[0] += 1
            raise AssertionError("legacy 不得发中证请求")

        fd.CSINDEX_SESSION.get = _legacy_forbidden
        fd._teardown_request_coordination()
        expect_raise("L1", "中证防封-teardown直连fail-closed",
                     lambda: fd._csindex_request("/perf/index-perf", timeout=1),
                     "禁止 legacy")
        check("L1", "中证防封-teardown零transport", _legacy_transport == [0])
    finally:
        fd.CSINDEX_SESSION.get = _orig_get
        _fresh_coordination()

    # mock 数据不入真缓存（V0.8.1 修：沙箱内也不留——在线层 test_csindex_online
    # 同进程读同一沙箱 FUND_CACHE_DIR，24h TTL 的 mock 清单/日线/映射会让在线
    # 断言读到 3 行假数据；沿「mock 不入真缓存」既有惯例，整个 csindex 子树清除）
    import shutil as _sh_cs
    _sh_cs.rmtree(fd.FUND_CACHE_DIR / "csindex", ignore_errors=True)

    # V0.8.1 复审：BENCH 抽查的 SKIP 豁免不得吞 RiskControlError（其是
    # RuntimeError 子类——在线函数先分流再豁免；此断言锁死该分流防回归）
    _orig_p2g_bench = fd._push2_get
    try:
        fd._push2_get = lambda *a, **k: (_ for _ in ()).throw(
            fd.RiskControlError("HTTP 403（push2）：疑似风控信号"))
        expect_raise("L1", "行业抽查-风控信号不被SKIP豁免",
                     test_bench_spot_online, "风控")
    finally:
        fd._push2_get = _orig_p2g_bench




# ════════════════════ V0.8.2 申万行业指数层（固定样本零网络）════════════
def test_sws_offline():
    check("L1", "申万-代码校验",
          fd._sws_code("801010") == "801010")
    expect_raise("L1", "申万-非法代码拒绝", lambda: fd._sws_code("80a010"), "6 位数字")
    # 策略双向锁（core 默认表 ↔ 主模块覆盖，V0.8.1 同款）
    fd._reset_request_coordination()               # 真实策略解析（沙箱可能处于零间隔态）
    _sws_real = fd._COORDINATOR.policy("sws")
    _fresh_coordination()
    from fund_data_core.policies import DEFAULT_POLICIES as _DP
    _sws_default = _DP["sws"]
    check("L1", "申万防封-core默认表与主模块覆盖一致",
          (_sws_default.max_requests, _sws_default.window_seconds, _sws_default.min_interval,
           _sws_default.max_in_flight, _sws_default.lease_seconds)
          == (_sws_real.max_requests, _sws_real.window_seconds, _sws_real.min_interval,
              _sws_real.max_in_flight, _sws_real.lease_seconds)
          == (120, 300, fd.SWS_MIN_INTERVAL, 1, 180),
          "单一来源两处一致（双向锁）")
    expect_raise("L1", "申万-非法indextype拒绝",
                 lambda: fd.sw_index_list("申万自营"), "indextype")

    class _SWSResp:
        def __init__(self, status, body):
            self.status_code = status
            self.content = json.dumps(body).encode("utf-8") if not isinstance(body, (bytes, str)) \
                else (body.encode("utf-8") if isinstance(body, str) else body)

        def raise_for_status(self):
            return None

    _orig_get = fd.SWS_SESSION.get
    try:
        # 软封三态：200 非 JSON / 业务 code≠200 / 清单空返回
        fd.SWS_SESSION.get = lambda *a, **k: _SWSResp(200, "not-json-{")
        expect_raise("L1", "申万-200非JSON拒绝",
                     lambda: fd.sw_index_list("一级行业", force_refresh=True), "非 JSON")
        _fresh_coordination()
        fd.SWS_SESSION.get = lambda *a, **k: _SWSResp(
            200, {"code": "400", "message": "Parameter Errors", "data": {}})
        expect_raise("L1", "申万-业务code拒绝",
                     lambda: fd.sw_index_list("一级行业", force_refresh=True), "业务")
        _fresh_coordination()
        fd.SWS_SESSION.get = lambda *a, **k: _SWSResp(
            200, {"code": "200", "data": {"count": 0, "results": []}})
        expect_raise("L1", "申万-清单空返回拒绝",
                     lambda: fd.sw_index_list("一级行业", force_refresh=True), "空返回")
        _fresh_coordination()
        # 403 风控分流
        fd.SWS_SESSION.get = lambda *a, **k: _SWSResp(403, {})
        expect_raise("L1", "申万-403风控分流",
                     lambda: fd.sw_index_list("一级行业", force_refresh=True), "403")
        _fresh_coordination()
        # teardown fail-closed（零 transport）
        _legacy_calls = [0]

        def _legacy_forbidden(*a, **k):
            _legacy_calls[0] += 1
            raise AssertionError("legacy 不得发申万请求")

        fd.SWS_SESSION.get = _legacy_forbidden
        fd._teardown_request_coordination()
        expect_raise("L1", "申万-teardown直连fail-closed",
                     lambda: fd._sws_request("/index_publish/current/"), "禁止 legacy")
        check("L1", "申万-teardown零transport", _legacy_calls == [0])
        _fresh_coordination()
        # SSL 证书失败分流：显式指引、不计连接级熔断（零标记）
        _cert_err = fd.requests.exceptions.SSLError("verify failed")
        _cert_err.__cause__ = fd.ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
        fd.SWS_SESSION.get = lambda *a, **k: _cert_err
        _fresh_coordination()
        try:
            fd.sw_index_list("一级行业", force_refresh=True)
            check("L1", "申万-证书失败应抛未抛", False)
        except RuntimeError as e:
            check("L1", "申万-证书失败显式指引零熔断标记",
                  "证书校验失败" in str(e) and not fd._sws_conn_file().exists(), str(e)[:60])
        _fresh_coordination()
        # 传输失败 → 连接级熔断 2 连败停机，第 3 次零网络
        _conn_err = fd.requests.exceptions.ConnectionError("connection reset")
        fd.SWS_SESSION.get = lambda *a, **k: _conn_err
        for _ in range(2):
            try:
                fd._sws_request("/index_publish/current/")
            except fd.requests.exceptions.ConnectionError:
                pass
        check("L1", "申万-2连败停机标记落盘", fd._sws_conn_file().exists())
        try:
            fd._sws_request("/index_publish/current/")
            check("L1", "申万-停机期应拒未拒", False)
        except RuntimeError as e:
            check("L1", "申万-停机期零网络直拒",
                  "连接级熔断" in str(e) and fd.SWS_REQUEST_COUNT[0] >= 0)
        fd._sws_conn_file().write_text("0 stale-test")   # 期满标记 → 自动复位
        fd._sws_conn_file().unlink()
        fd._sws_conn_fails[0] = 0
        _fresh_coordination()

        # 清单翻页合并 + 缓存二调 0 请求（mock 数据用风格指数避开一级行业锚）
        def _list_fake(url, **k):
            page = k["params"]["page"]
            rows = [{"swindexcode": "801950", "swindexname": "大市值", "l3": 1, "l4": 2,
                     "l5": 3, "l6": 4, "l7": 0.5, "l8": 2.5, "l11": 9}] if page == 1 else [
                {"swindexcode": "801951", "swindexname": "中市值", "l3": 1, "l4": 2,
                 "l5": 3, "l6": 4, "l7": 0.5, "l8": 2.5, "l11": 9}]
            return _SWSResp(200, {"code": "200", "data": {
                "count": 2, "results": rows if page == 1 else rows, "next": None}})

        fd.SWS_SESSION.get = _list_fake
        _lst = fd.sw_index_list("风格指数", force_refresh=True)
        _n1 = fd.SWS_REQUEST_COUNT[0]
        check("L1", "申万-清单翻页合并",
              len(_lst) == 2 and _lst[0]["index_code"] == "801950"
              and _lst[0]["latest"] == 2.5, str(_lst)[:80])
        _lst2 = fd.sw_index_list("风格指数")
        check("L1", "申万-清单缓存二调0请求",
              fd.SWS_REQUEST_COUNT[0] == _n1 and len(_lst2) == 2)
        # 自组 CA bundle（P34）：_sws_request 已刷新 verify=沙箱 bundle 路径（非 False）
        _bundle = fd._sws_ca_bundle()
        _bundle_text = Path(_bundle).read_text(encoding="utf-8")
        check("L1", "申万-自组CA bundle完整校验",
              str(fd.SWS_SESSION.verify) == _bundle
              and "BEGIN CERTIFICATE" in _bundle_text
              and "GeoTrust" in fd._sws_extra_cert_info()
              and "MIIFxjCCBK6gAwIBAgIQ" in fd._SWS_EXTRA_CERT_PEM,   # PEM 本体锚（base64 首 16 字节）
              f"verify={fd.SWS_SESSION.verify!r}")

        # 日线：全史单发 + 年切片 + day/week 键隔离 + mtime 回拨
        _today = fd.datetime.now(fd._BJ_TZ).date().isoformat()
        # P1（审查）：月 1 号 day-1=0 会钳回 1——必须 timedelta 回退（每月 1 号回归不炸）
        _yesterday = (fd.datetime.now(fd._BJ_TZ) - fd.timedelta(days=1)).date().isoformat()

        def _trend_fake(url, **k):
            code = k["params"]["swindexcode"]
            period = k["params"]["period"]
            factor = {"DAY": 1.0, "WEEK": 2.0, "MONTH": 3.0}[period]
            raw = [
                {"swindexcode": code, "bargaindate": "1999-12-30", "openindex": 1000.0,
                 "maxindex": 1001.0, "minindex": 999.0, "closeindex": 1000.0,
                 "hike": 0.0, "markup": 0.0, "bargainamount": 1.0, "bargainsum": 2.0},
                {"swindexcode": code, "bargaindate": "2026-11-20", "openindex": 2000.0,
                 "maxindex": 2010.0, "minindex": 1995.0, "closeindex": 2000.0,
                 "hike": 20.0, "markup": 1.01, "bargainamount": 3.0, "bargainsum": 4.0},
                {"swindexcode": code, "bargaindate": _yesterday, "openindex": 2000.0,
                 "maxindex": 2010.0, "minindex": 1995.0, "closeindex": 2020.2,
                 "hike": 20.2, "markup": 1.01, "bargainamount": 3.0, "bargainsum": 4.0},
            ]
            return _SWSResp(200, {"code": "200", "data": raw})

        fd.SWS_SESSION.get = _trend_fake
        _d = fd.sw_index_daily("801010", force_refresh=True)
        _n2 = fd.SWS_REQUEST_COUNT[0]
        check("L1", "申万-日线全史切片与字段",
              len(_d) == 3 and _d[0]["date"] == "1999-12-30"
              and _d[-1]["close"] == 2020.2 and _d[-1]["change_pct"] == 1.01
              and fd._cache_read("sws/daily/801010_day_1999.json", None) is not None)
        _d2 = fd.sw_index_daily("801010")
        check("L1", "申万-日线缓存二调0请求",
              fd.SWS_REQUEST_COUNT[0] == _n2 and len(_d2) == 3)
        _w = fd.sw_index_daily("801010", period="week", force_refresh=True)
        check("L1", "申万-day/week缓存键隔离",
              fd._cache_read("sws/daily/801010_week_2026.json", None) is not None
              and len(_w) == 3 and _w[0]["close"] == 1000.0
              and fd._cache_read("sws/daily/801010_day_1999.json", None)[0]["close"] == 1000.0)
        _rep = fd._cache_read("sws/reports/801010_day_latest.json", None)
        check("L1", "申万-日线盘后未发布mtime回拨",
              _rep is not None and _rep["last_date"] == _yesterday and _rep["last_date"] < _today
              and os.path.getmtime(fd.FUND_CACHE_DIR / f"sws/daily/801010_day_{_today[:4]}.json")
              < time.time() - 17 * 3600,
              f"last={_rep['last_date'] if _rep else None} today={_today}")
        expect_raise("L1", "申万-非法period拒绝",
                     lambda: fd.sw_index_daily("801010", period="hour"), "period")

        # V0.8.3：跨年缺当前年不得误判完整；历史估值区间不受该守护连坐
        fd._cache_write("sws/daily/801999_day_2024.json", [{"date": "2024-12-31"}])
        _cross_rows83, _cross_complete83 = fd._sws_year_rows(
            "sws/daily/801999_day", "801999", 2026, require_current=True)
        _, _historical_complete83 = fd._sws_year_rows(
            "sws/daily/801999_day", "801999", 2026)
        check("L1", "V0.8.3申万-跨年缺当前年强制刷新且历史查询不连坐",
              _cross_rows83 and not _cross_complete83 and _historical_complete83)

        # V0.8.3：周末末行落后于今天是正常形态，不得回拨制造 2h 重拉
        _datetime83, _utime83 = fd.datetime, fd.os.utime
        _utime_calls83 = []
        class _WeekendDatetime83(_datetime83):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 28, 12, 0, tzinfo=tz)
        try:
            fd.datetime = _WeekendDatetime83
            fd.os.utime = lambda *a: _utime_calls83.append(a)
            fd.SWS_SESSION.get = _trend_fake
            fd.sw_index_daily("801020", force_refresh=True)
            check("L1", "V0.8.3申万-周末不回拨当前年缓存", not _utime_calls83)
        finally:
            fd.datetime, fd.os.utime = _datetime83, _utime83

        # 估值：区间 mock + None PE 容错 + 越界拒缓存 + pending_today
        def _val_fake(url, **k):
            p = k["params"]
            assert p["start_date"].startswith("2"), p
            rows = [
                {"swindexcode": "801010", "bargaindate": f"{p['start_date'][:4]}-06-05T08:00:00+08:00",
                 "closeindex": "2500", "pe": "48.5", "pb": "2.7", "dp": "1.1",
                 "turnoverrate": "0.8", "negotiablessharesum1": "6050.08", "meanprice": "12.3"},
                {"swindexcode": "801010", "bargaindate": f"{p['start_date'][:4]}-06-06T08:00:00+08:00",
                 "closeindex": "2510", "pe": None, "pb": "2.71", "dp": "1.1",
                 "turnoverrate": "0.8", "negotiablessharesum1": "6060", "meanprice": "12.4"},
            ]
            return _SWSResp(200, {"code": "200", "data": {
                "count": 2, "results": rows, "next": None}})

        fd.SWS_SESSION.get = _val_fake
        _v = fd.sw_index_valuation("801010", "20240601", "20240630", force_refresh=True)
        _n3 = fd.SWS_REQUEST_COUNT[0]
        check("L1", "申万-估值字段与None容错",
              len(_v) == 2 and _v[0]["date"] == "2024-06-05" and _v[0]["pe"] == 48.5
              and _v[1]["pe"] is None and _v[1]["pb"] == 2.71
              and _v[0]["float_mv"] == 6050.08, str(_v)[:100])
        _v2 = fd.sw_index_valuation("801010", "20240601", "20240630")
        check("L1", "申万-估值缓存二调0请求",
              fd.SWS_REQUEST_COUNT[0] == _n3 and len(_v2) == 2)
        _short_report83 = fd._cache_read("sws/reports/801010_val_latest.json", None)
        check("L1", "V0.8.3申万-短区间不写全史起点",
              _short_report83.get("verified_earliest") is None, str(_short_report83))
        expect_raise("L1", "申万-估值end超今天拒绝",
                     lambda: fd.sw_index_valuation("801010", "", "20991231"), "不可超过今天")
        expect_raise("L1", "申万-估值start晚于end拒绝",
                     lambda: fd.sw_index_valuation("801010", "20240610", "20240601"), "晚于")

        def _val_bad_pe(url, **k):
            return _SWSResp(200, {"code": "200", "data": {
                "count": 1, "results": [{"swindexcode": "801010",
                                         "bargaindate": "2024-06-05", "closeindex": "2500",
                                         "pe": "99999", "pb": "2.7", "dp": "1.1"}], "next": None}})

        fd.SWS_SESSION.get = _val_bad_pe
        expect_raise("L1", "申万-估值PE越界拒缓存",
                     lambda: fd.sw_index_valuation("801030", "20240601", "20240630",
                                                   force_refresh=True), "越界")
        _fresh_coordination()

        # 当日未发布 pending_today：区间含今天但无今日行 → 豁免 + report
        def _val_today_fake(url, **k):
            return _SWSResp(200, {"code": "200", "data": {
                "count": 1, "results": [{"swindexcode": "801010",
                                         "bargaindate": f"{_yesterday}T08:00:00+08:00",
                                         "closeindex": "2520", "pe": "48.0", "pb": "2.7",
                                         "dp": "1.1"}], "next": None}})

        fd.SWS_SESSION.get = _val_today_fake
        _vt = fd.sw_index_valuation("801010", _today, force_refresh=True)
        _vrep = fd._cache_read("sws/reports/801010_val_latest.json", None)
        check("L1", "申万-估值当日未发布pending豁免",
              len(_vt) == 0 and _vrep is not None
              and _vrep["pending_today"] == _today
              and _vrep["rows"] == 1,              # 昨日行已入库缓存（区间外不返回）
              str(_vrep))

        # P1 回归锁：请求未缓存年份 → 显式取数（审查实测复现场景：先查 2025 再查 2024）
        fd.SWS_SESSION.get = _val_fake
        fd.sw_index_valuation("801010", "20250601", "20250630", force_refresh=True)
        (fd.FUND_CACHE_DIR / "sws/valuation/801010_2024.json").unlink(missing_ok=True)
        _n5 = fd.SWS_REQUEST_COUNT[0]
        _v_gap = fd.sw_index_valuation("801010", "20240601", "20240630")
        check("L1", "申万-估值缺年显式取数不静默空",
              len(_v_gap) == 2 and fd.SWS_REQUEST_COUNT[0] > _n5   # 缺年触发取数
                                                                        # （fetch 到 today，
                                                                        # 近 3 年翻页 ≥1 发）
              and _v_gap[0]["date"] == "2024-06-05",
              f"{len(_v_gap)} 行（修复前 0 静默空）")

        def _val_empty83(url, **k):
            return _SWSResp(200, {"code": "200", "data": {"count": 0, "results": []}})

        fd.SWS_SESSION.get = _val_empty83
        expect_raise("L1", "V0.8.3申万-估值冷缓存空信封拒绝",
                     lambda: fd.sw_index_valuation("801998", "20240101", "20241231",
                                                   force_refresh=True), "空返回")
        fd._cache_write("sws/valuation/801997_2024.json", [
            {"index_code": "801997", "date": "2024-06-05", "close": 2500.0}])
        fd.sw_index_valuation("801997", "20240101", _today)
        _hot_report83 = fd._cache_read("sws/reports/801997_val_latest.json", None)
        check("L1", "V0.8.3申万-热缓存空信封保留pending语义",
              _hot_report83 and _hot_report83["pending_today"] == _today,
              str(_hot_report83))

        # 已验证全史起点可把缺年对账下界从 2003 收紧到真实起始年
        fd._cache_write("sws/valuation/801996_2024.json", [
            {"index_code": "801996", "date": "2024-06-05", "close": 2500.0}])
        fd._cache_write("sws/reports/801996_val_latest.json", {
            "verified_earliest": "2014-01-02"})
        _fetch_starts83 = []
        def _val_start_spy83(url, **k):
            _fetch_starts83.append(k["params"]["start_date"])
            return _val_fake(url, **k)
        fd.SWS_SESSION.get = _val_start_spy83
        fd.sw_index_valuation("801996", "20030101", "20241231")
        check("L1", "V0.8.3申万-全史起点避免重复2003重拉",
              _fetch_starts83 == ["2014-01-01"], str(_fetch_starts83))

        # 成分：单发 + 权重和警告 + 空 results 显式报错 + 缓存
        def _comp_fake(url, **k):
            return _SWSResp(200, {"code": "200", "data": {"count": 2, "results": [
                {"stockcode": "000510", "stockname": "新金路", "newweight": "50.0",
                 "beginningdate": "2021-12-13T08:00:00+08:00"},
                {"stockcode": "600000", "stockname": "浦发银行", "newweight": "49.98",
                 "beginningdate": "2021-12-13T08:00:00+08:00"}]}})

        fd.SWS_SESSION.get = _comp_fake
        _c = fd.sw_index_components("801030", force_refresh=True)
        _n4 = fd.SWS_REQUEST_COUNT[0]
        check("L1", "申万-成分字段与权重和",
              len(_c) == 2 and _c[0]["stock_code"] == "000510"
              and _c[0]["since"] == "2021-12-13"
              and abs(sum(x["weight"] for x in _c) - 99.98) < 0.01)
        _c2 = fd.sw_index_components("801030")
        check("L1", "申万-成分缓存二调0请求",
              fd.SWS_REQUEST_COUNT[0] == _n4 and len(_c2) == 2)

        def _comp_empty(url, **k):
            return _SWSResp(200, {"code": "200", "data": {"count": 0, "results": []}})

        fd.SWS_SESSION.get = _comp_empty
        expect_raise("L1", "申万-成分空返回显式报错",
                     lambda: fd.sw_index_components("801980", force_refresh=True), "空返回")
    finally:
        fd.SWS_SESSION.get = _orig_get
        _fresh_coordination()
        # mock 数据不入真缓存（V0.8.1 教训：同进程在线层会读沙箱缓存）
        import shutil as _sh_sws
        _sh_sws.rmtree(fd.FUND_CACHE_DIR / "sws", ignore_errors=True)
        fd._sws_conn_file().unlink(missing_ok=True)
        fd._sws_conn_fails[0] = 0
        fd._sws_ca_bundle_cache[:] = []            # 进程内状态卫生（审查 P3）
        fd.SWS_SESSION.verify = fd._sws_ca_bundle()




def test_sws_online():
    """申万层在线锚（sws 线预算 ≤6 发：清单 1 + 日线 1 + 估值 1~3 + 成分 1）。"""
    lst = fd.sw_index_list("一级行业")
    codes = {x["index_code"] for x in lst}
    check("L2", "申万清单-一级行业官方锚",
          len(lst) >= 31 and {"801010", "801030", "801780"} <= codes
          and lst[0]["latest"] is not None,
          f"{len(lst)} 行，含 801010/801030/801780")
    d = fd.sw_index_daily("801010")
    _today = fd.datetime.now(fd._BJ_TZ).date()
    _last_gap = (_today - fd.datetime.strptime(d[-1]["date"], "%Y-%m-%d").date()).days
    check("L2", "申万日线-801010全史锚",
          d[0]["date"] < "2000-01-01" and len(d) > 6000 and _last_gap <= 7
          and 0 < d[-1]["close"] < 100000,
          f"{len(d)} 行 {d[0]['date']}→{d[-1]['date']}（距今 {_last_gap} 天）")
    v = fd.sw_index_valuation("801010", "20240101", "20241231")
    check("L2", "申万估值-801010 2024完整区间",
          len(v) == 242 and v[0]["date"] == "2024-01-02" and v[-1]["date"] == "2024-12-31"
          and 0 < v[0]["pe"] < 200 and 0 < v[0]["pb"] < 50,
          f"{len(v)} 行，PE {v[0]['pe']}/PB {v[0]['pb']} → PE {v[-1]['pe']}")
    _n = fd.SWS_REQUEST_COUNT[0]
    v2 = fd.sw_index_valuation("801010", "20240601", "20240630")
    check("L2", "申万估值-年度缓存切片复用0请求",
          len(v2) == 19 and fd.SWS_REQUEST_COUNT[0] == _n,   # 2024-06 实锚 19 交易日（6/10 端午休）
          f"{len(v2)} 行")
    c = fd.sw_index_components("801030")
    ws = sum(x["weight"] or 0 for x in c)
    check("L2", "申万成分-801030权重和",
          len(c) >= 300 and 95 <= ws <= 105 and c[0]["weight"] > 0,
          f"{len(c)} 只，权重和 {ws:.2f}%")


# ════════════════════ L2 端点 smoke（真数据结构断言）════════════════════
def test_l2_smoke():
    # 在线首测强制真实抓取：覆盖沙箱里的离线密闭性合成种子（E1 端点真验证）
    fl = fd.fund_list(force_refresh=True)
    check("L2", "E1 清单规模", len(fl) > 5000, f"{len(fl)} 只")
    check("L2", "E1 清单含货基000198",
          any(f["code"] == "000198" for f in fl))

    snap = fd.fund_snapshot("161725")
    check("L2", "E2 快照-名称", "白酒" in (snap["name"] or ""), snap["name"])
    check("L2", "E2 快照-净值全史", len(snap["nav_history"]) > 500,
          f"{len(snap['nav_history'])} 条")
    check("L2", "E2 快照-结构断言",
          snap["top_stocks"] and snap["manager"]["name"]
          and isinstance(snap["missing_datasets"], list)
          and snap["_meta"]["source"].startswith("eastmoney"),
          f"重仓{len(snap['top_stocks'])} 经理{snap['manager']['name']} "
          f"缺失{len(snap['missing_datasets'])}项")
    check("L2", "E2 快照-基准三曲线(V0.4)",
          len(snap["benchmark_curves"]) == 3
          and {"同类平均", "沪深300"} <= {c["name"] for c in snap["benchmark_curves"]}
          and all(len(c["points"]) > 60 for c in snap["benchmark_curves"]),
          str([(c["name"], len(c["points"])) for c in snap["benchmark_curves"]]))
    check("L2", "E2 快照-仓位史/同类百分位修复(V0.4)",
          len(snap["stock_position_history"]) >= 4
          and isinstance(snap["stock_position_history"][-1]["position_pct"], float)
          and len(snap["peer_rank_history"]) > 100,
          f"仓位{len(snap['stock_position_history'])}期 "
          f"同类{len(snap['peer_rank_history'])}点")
    pl = snap.get("purchase_limit")
    check("L2", "PL 限额-快照数据集在位(V0.7.7)",
          (pl is None and any("purchase_limit" in m for m in snap["missing_datasets"]))
          or (isinstance(pl, dict)
              and pl["kind"] in ("limited", "suspended", "unlimited", "unknown")
              and isinstance(pl["status_text"], str) and pl["status_text"]),
          f"kind={pl['kind'] if pl else None} "
          f"状态={(pl['status_text'][:30] if pl else 'N/A')}")
    pl2 = fd.fund_purchase_limit("000369")   # 真实限大额 QDII 样本（首查 1 请求，同日重查 0）
    check("L2", "PL 限额-真实解析/缓存往返(V0.7.7)",
          pl2["code"] == "000369" and pl2["kind"] in ("limited", "unknown")
          and pl2["name"] and ("限大额" in pl2["status_text"] or pl2["kind"] == "unknown"),
          f"{pl2['status_text'][:36]} kind={pl2['kind']}")
    # V0.7.8 报告：交银趋势 519702 场外基金——巨潮无披露、东财清单+直链全量（坑P32）
    rp = fd.fund_reports("519702")
    check("L2", "RP 报告-清单/分类/直链(V0.7.8)",
          rp["total"] >= 50 and len(rp["reports"]) == 20
          and rp["reports"][0]["year"] >= 2026
          and rp["reports"][0]["kind"] in ("Q1", "Q2", "Q3", "Q4", "interim", "annual")
          and rp["reports"][0]["pdf_url"].startswith("https://pdf.dfcfw.com/pdf/H2_AN")
          and {"Q1", "Q3", "interim", "annual"} <= {x["kind"] for x in rp["reports"]},
          f"total={rp['total']} 首行{rp['reports'][0]['kind']}{rp['reports'][0]['year']}")
    with tempfile.TemporaryDirectory() as _rpdir:
        _rpp = fd.fund_report_pdf(rp["reports"][0], dest_dir=_rpdir)   # 1~2 请求（含挑战）
        _rpb = Path(_rpp).read_bytes()
        check("L2", "RP 报告-PDF下载挑战自动解(V0.7.8)",
              _rpb[:4] == b"%PDF" and len(_rpb) > 100000
              and Path(_rpp).name.startswith("519702_"),
              f"{Path(_rpp).name} {len(_rpb)}B")
    check("L2", "E2 快照-规模史修复(V0.4)",
          snap["scale_history"]
          and isinstance(snap["scale_history"][-1]["scale_yi"], float),
          str(snap["scale_history"][-1]))
    _fund_curve = next((c for c in snap["benchmark_curves"]
                        if c["name"] not in ("同类平均", "沪深300")), None)
    _rp = fd.relative_performance(snap)
    check("L2", "相对表现-基金窗口收益=grandTotal基金曲线末点",
          _fund_curve is not None
          and abs(_rp["fund_return_pct"]
                  - _fund_curve["points"][-1]["return_pct"]) < 0.05
          and _rp["beta"] is not None and abs(_rp["beta"]) < 3,
          f"相对{_rp['relative_return_pct']}% β={_rp['beta']} corr={_rp['correlation']}"
          f"（板块基金可与大盘日频脱钩，实测白酒 corr 0.06——只锚窗口收益一致性）"
          f"窗口{_rp['window']['start']}~{_rp['window']['end']}")

    hist = fd.fund_nav_history("161725", start="2026-09-01", end="2026-11-05")
    check("L2", "E3 净值-区间+申赎状态",
          len(hist) > 30 and all(h["buy_status"] for h in hist)
          and all(h["date"] >= "2026-09-01" for h in hist),
          f"{len(hist)} 条（TotalCount 校验内置）")

    hold = fd.fund_holdings("161725")
    check("L2", "E4a 持仓-季度前十大",
          len(hold) >= 10 and hold[0]["period"] and hold[0]["nav_pct"] > 0,
          f"{len(hold)} 行，最新报告期 {hold[0]['period']}，首重仓 "
          f"{hold[0]['stock_code']}{hold[0]['stock_name']} {hold[0]['nav_pct']}%")

    dj = fd.fund_detail_danjuan("161725")
    check("L2", "D1 蛋卷-档案", dj["manager"] and dj["risk_level"] and dj["source"] == "danjuan",
          f"经理{dj['manager']} 风险R{dj['risk_level']}")

    rt = fd.fund_realtime_nav(["161725", "110022"])
    check("L2", "E6 批量净值-2只1请求", len(rt) == 2 and all(x["nav"] and x["nav"] > 0 for x in rt),
          f"{[x['code'] + '@' + str(x['nav']) for x in rt]}")

    pen = fd.penetrate("161725")                      # L6 穿透链路（板块当日缓存命中）
    check("L2", "L6 穿透-主题聚合白话地图",
          pen["themes"] and pen["themes"][0][0] == "白酒" and pen["topn_weight_pct"] > 50
          and len(pen["detail"]) == 10 and not pen["failed_stocks"],
          f"Top1主题={pen['themes'][0]} 前十大权重{pen['topn_weight_pct']}%")

    # ── L7 宏观利率层 smoke ──
    curve = fd.cb_yield_curve("treasury", year=2024)
    latest_d = max(p["date"] for p in curve["points"])
    y10 = [p["yield_pct"] for p in curve["points"] if p["term"] == "10y"]
    check("L2", "L7 中债国债曲线年文件",
          len(curve["points"]) > 200 and len(y10) > 100 and 0.5 < y10[-1] < 5,
          f"{len(curve['points'])}点 最新{latest_d} 10Y={y10[-1]}%")
    ncd = fd.cb_curve_series("ncd_aaa", "1y")
    check("L2", "L7 存单AAA曲线1Y", len(ncd) > 100 and 0.5 < ncd[-1]["yield_pct"] < 5,
          f"{len(ncd)}点 最新 {ncd[-1]['date']} {ncd[-1]['yield_pct']}%")
    sp = fd.credit_spread("AA", "3y")
    check("L2", "L7 信用利差AA-3Y", len(sp) > 100 and -1 < sp[-1]["spread_pct"] < 5,
          f"{len(sp)}点 最新利差 {sp[-1]['spread_pct']}%")
    sh = fd.shibor_history("1年(1Y)", days=5)
    check("L2", "L7 SHIBOR 1Y(锁定上海市场)",
          len(sh) >= 1 and 0.5 < sh[0]["rate_pct"] < 5
          and all(r["market"] == "上海银行同业拆借市场" for r in sh)
          and max(r["rate_pct"] for r in sh) - min(r["rate_pct"] for r in sh) < 0.5,
          f"{sh[0]['date']} {sh[0]['rate_pct']}%（5日极差<50bp，防Chibor混入跳变24bp）")
    bm = fd.benchmark_rate_history()
    latest = bm[0]                                     # 新→旧，最新=2015-10-24 末次调整
    check("L2", "L7 基准利率史(数值锚定+单位自洽)",
          len(bm) >= 10 and latest["report_date"] == "2015-10-24"
          and latest["deposit_1y"] == 1.50 and latest["deposit_1y_prev"] == 1.75
          and abs(latest["deposit_change_pp"]
                  - (latest["deposit_1y"] - latest["deposit_1y_prev"])) < 1e-9,
          f"{len(bm)}次 {latest['report_date']} 定存{latest['deposit_1y_prev']}→"
          f"{latest['deposit_1y']}（Δ{latest['deposit_change_pp']}pp=25bp 自洽）")
    gc = fd.repo_rate_gc()
    check("L2", "L7 GC007回购代理", 0.5 < gc["rate_pct"] < 8 and gc["proxy_for"] == "DR007",
          f"GC007={gc['rate_pct']}% ({gc['source'][:20]})")
    rt = fd.cn_treasury_realtime()
    check("L2", "L7 国债收益率四期限(P26两形态)",
          all(t in rt for t in ("2y", "5y", "10y", "30y"))
          and rt["2y"]["yield_pct"] < rt["10y"]["yield_pct"] < rt["30y"]["yield_pct"]
          and (rt["as_of"] == "盘中(push2)" or "收盘(降级" in rt.get("as_of", "")),
          f"2Y={rt['2y']['yield_pct']} 10Y={rt['10y']['yield_pct']} "
          f"30Y={rt['30y']['yield_pct']} [{rt['as_of']}]")

    # ── V0.5 全市场层（rankhandler 全量底座 + cninfo 第四线）──
    _n5 = fd.TT_REQUEST_COUNT[0]
    rk = fd.fund_rank()
    _rk_first = fd.TT_REQUEST_COUNT[0] - _n5
    check("L2", "N1 全市场排行-规模(全量1请求)",
          len(rk) > 20000 and _rk_first <= 1,
          f"{len(rk)} 行（首调实发 {_rk_first} 请求，缓存内=0）")
    _me = next((r for r in rk if r["code"] == "161725"), None)
    check("L2", "N1 全市场排行-161725在册",
          _me is not None and "白酒" in (_me["name"] or "") and _me["nav"]
          and isinstance(_me["fund_type"], str),
          f"{_me['name']} nav={_me['nav']} type={_me['fund_type']}")
    _vals = [r["ret_ytd_pct"] for r in rk]
    _nn = [v for v in _vals if v is not None]
    check("L2", "N1 排序契约-ytd降序且None恒末尾",
          _nn == sorted(_nn, reverse=True)
          and _vals[len(_nn):] == [None] * (len(_vals) - len(_nn)),
          f"Top1 {_nn[0]}%，{len(_vals) - len(_nn)} 只无 ytd 排尾")
    _n6 = fd.TT_REQUEST_COUNT[0]
    rk_gp = fd.fund_rank(fund_type="gp")
    check("L2", "N1 类型过滤=本地0请求",
          fd.TT_REQUEST_COUNT[0] == _n6 and len(rk_gp) > 500
          and all(fd._bucket_of(r.get("fund_type")) == "gp" for r in rk_gp),
          f"gp 型 {len(rk_gp)} 只，实发 {fd.TT_REQUEST_COUNT[0] - _n6} 请求")
    ni = fd.fund_new_issues()
    check("L2", "N2 新发基金-全量+公司ID映射",
          len(ni) >= 300 and all(len(it["company_id"]) == 8 for it in ni)
          and ni[0]["company"] and ni[0]["code"],
          f"{len(ni)} 只，示例 {ni[0]['name']} / {ni[0]['company']}")
    co = fd.fund_company("80065113")
    check("L2", "N3a 公司画像-ID路径",
          "中欧" in (co["name"] or "") and (co["scope_yi"] or 0) > 1000
          and (co["fund_count"] or 0) >= 100 and len(co["funds"]) >= 100,
          f"{co['name']} 规模{co['scope_yi']}亿 基金{co['fund_count']} "
          f"经理{co['manager_count']} 表格{len(co['funds'])}只")
    co2 = fd.fund_company("161725")
    check("L2", "N3b 公司画像-基金码路径",
          "招商" in (co2["name"] or "") and co2["company_id"] != "161725"
          and len(co2["company_id"]) == 8,
          f"161725→{co2['name']}（ID {co2['company_id']}）")
    cd = fd.fund_crowding()
    check("L2", "N4a 抱团度-规模与排序",
          len(cd) > 1000 and cd[0]["fund_count"] > 1000
          and all(cd[i]["fund_count"] >= cd[i + 1]["fund_count"]
                  for i in range(len(cd) - 1)),
          f"{len(cd)} 只重仓股，Top1 {cd[0]['name']} {cd[0]['fund_count']} 家")
    ia = fd.fund_industry_allocation()
    check("L2", "N4b 行业配置-门类数与头部",
          len(ia) >= 18 and ia[0]["industry"] == "制造业"
          and ia[0]["weight_pct"] > 10 and ia[0]["market_value_yi"] > 10000,
          f"{len(ia)} 门类，Top1 {ia[0]['industry']} {ia[0]['market_value_yi']}亿/"
          f"{ia[0]['weight_pct']}%")

    # ── V0.6 经理画像与专项组装层 ──
    rp2 = fd.rank_persistence(top=5)
    check("L2", "V6 持续性矩阵-真数据结构",
          len(rp2) == 5 and rp2[0]["score_pct"] >= rp2[-1]["score_pct"]
          and rp2[0]["covered"] >= 4 and "horizon_pct" in rp2[0],
          f"Top1 {rp2[0]['name']} {rp2[0]['score_pct']}分 consistent={rp2[0]['consistent']}")
    fs2 = fd.fund_scale("161725")
    check("L2", "V6 单基金规模-实测锚",
          abs(fs2["scale_yi"] - 209.47) < 0.01 and fs2["company"] == "招商基金",
          f"{fs2['scale_yi']}亿（ENDNAV 元精确值）")
    bp3 = fd.bond_plus_profile("161725")
    check("L2", "V6 固收+画像-股票基金形态",
          bp3["grade"] == "偏股" and bp3["stock_pct"] > 60
          and bp3["max_drawdown_pct"] < 0 and bp3["drawdown_band"],
          f"{bp3['name']} 股{bp3['stock_pct']}% 回撤{bp3['max_drawdown_pct']}%→{bp3['drawdown_band']}")
    qp3 = fd.qdii_premium(["513100"])
    check("L2", "V6 QDII溢价-真数据",
          qp3[0]["premium_pct"] is not None and -10 < qp3[0]["premium_pct"] < 60
          and qp3[0]["nav_date"] and "T-1/T-2" in qp3[0]["note"],
          f"{qp3[0]['name']} 溢价{qp3[0]['premium_pct']}%（净值{qp3[0]['nav_date']}）")
    nd2 = fd.nav_divergence("161725")
    check("L2", "V6 重仓探测-真数据",
          nd2["stocks_used"] >= 5 and -30 <= nd2["divergence_pp"] <= 30
          and nd2["coverage_pct"] > 30 and nd2["verdict"] and nd2["window"]["days"] < 120,
          f"背离{nd2['divergence_pp']}pp {nd2['verdict']}（覆盖{nd2['coverage_pct']}%，"
          f"{nd2['window']['days']}天窗口）")
    fm4 = fd.fund_manager("161725")
    check("L2", "V6 经理画像-基金码路径",
          fm4["manager_id"] and fm4["name"] and fm4["fund_count"] >= 1
          and fm4["tenure"] and len(fm4["funds"]) == fm4["fund_count"],
          f"{fm4['name']}（{fm4['manager_id']}）任职{fm4['tenure']} "
          f"在管{fm4['fund_count']}只 规模{fm4['scale_yi']}亿")
    mr2 = fd.manager_rank(pages=1)
    check("L2", "V6 经理排行-50位完整",
          len(mr2) == 50 and mr2[0]["manager_id"] and mr2[0]["company"]
          and mr2[0]["fund_codes"],
          f"首行 {mr2[0]['name']}/{mr2[0]['company']} 规模{mr2[0]['scale_yi']}亿")

    # ── V0.6.1 基准指数层 ──
    idx300 = fd.kline_daily("沪深300")
    _n_tt, _n_tx = fd.TT_REQUEST_COUNT[0], fd.TENCENT_REQUEST_COUNT[0]
    _n_em = fd.EM_REQUEST_COUNT[0]
    fd.kline_daily("沪深300")                       # 二次必命中当日缓存
    _src300 = idx300[0].get("source", "")           # V0.7.1：行内 source 标注（旧缓存无键容错）
    check("L2", "B1 沪深300-全史+缓存0请求(P26双形态+source)",
          len(idx300) >= 800            # 主源≈2600 / 缓释桶空期腾讯备胎=800（P25上限）
          and (_src300 == "tencent"                 # 备胎形态：800 根窗口如实标注
               or (_src300 == "push2his" and len(idx300) >= 2000)   # 主源形态收紧=十年全史回归
               or _src300 == "")                    # ≤20h 旧缓存行无 source（升级过渡期）
          and idx300[0]["date"] < idx300[-1]["date"]
          and idx300[-1]["close"] > 1000
          and fd.TT_REQUEST_COUNT[0] == _n_tt and fd.TENCENT_REQUEST_COUNT[0] == _n_tx
          and fd.EM_REQUEST_COUNT[0] == _n_em,   # P26 后主源走 em 线（三账本全 0）
          f"{len(idx300)} 根 source={_src300 or '旧缓存'} {idx300[0]['date']}→{idx300[-1]['date']} "
          f"最新收盘 {idx300[-1]['close']}（二调 tt/tx/em 0/0/0）")
    idxdvd = fd.kline_daily("中证红利", lmt=300)
    check("L2", "B1 中证红利-secid市场1通道",
          len(idxdvd) >= 250 and idxdvd[-1]["close"] > 1000,
          f"{len(idxdvd)} 根，最新 {idxdvd[-1]['close']}")
    k519 = fd.kline_daily("600519", lmt=120)
    check("L2", "B1 股票日K-复用腾讯通道",
          len(k519) >= 100 and k519[-1]["close"] > 100 and k519[0]["date"] < k519[-1]["date"],
          f"{len(k519)} 根，最新收盘 {k519[-1]['close']}")

    # ── V0.6.2 规模全历史（真数据 + 官方锚）──
    shist = fd.fund_scale_history("000312")
    _sh_n = fd.TT_REQUEST_COUNT[0]   # 快门在一调之后——冷启首调的抓取不计入「二调」
    # 容前不锁尾（全仓 L2 惯例）：2026-Q4 披露后 shist[-1] 会前移——官方锚按日期定位
    _anchor = next((x for x in shist if x["date"] == "2026-09-30"), None)
    check("L2", "B2 规模全历史-全史跨度+官方锚",
          len(shist) >= 40 and shist[0]["date"] == "2013-09-27"
          and shist[-1]["date"] >= "2026-09-30"
          and _anchor is not None and abs(_anchor["nav_yi"] - 4.30) < 0.01
          and shist[0]["subscribe_yi"] is None
          and all(x["shares_yi"] for x in shist),
          f"{len(shist)} 期 {shist[0]['date']}→{shist[-1]['date']}（募集期→最新），"
          f"2026-09-30 锚净资产 {_anchor['nav_yi'] if _anchor else '?'}亿")
    fd.fund_scale_history("000312")
    check("L2", "B2 规模全历史-缓存0请求",
          fd.TT_REQUEST_COUNT[0] == _sh_n, f"二调实发 {fd.TT_REQUEST_COUNT[0] - _sh_n} 次")

    # ── V0.7.0 费率全表 + manager 语义命名（官方锚：005001 何帅/F10 费率表）──
    ff2 = fd.fund_fees("005001")
    check("L2", "B3 费率全表-官方锚",
          ff2["management_fee_pct"] == 1.2 and ff2["custody_fee_pct"] == 0.2
          and ff2["purchase_tiers"][0]["origin_pct"] == 1.5
          and ff2["purchase_tiers"][0]["discount_pct"] == 0.15
          and len(ff2["redemption_tiers"]) == 5
          and ff2["redemption_tiers"][-1]["fee_pct"] == 0.0
          and "730" in ff2["redemption_tiers"][-1]["holding"],
          f"管理{ff2['management_fee_pct']}% 托管{ff2['custody_fee_pct']}% "
          f"申购{ff2['purchase_tiers'][0]['origin_pct']}|{ff2['purchase_tiers'][0]['discount_pct']}% "
          f"赎回{len(ff2['redemption_tiers'])}档至免")
    snap7 = fd.fund_snapshot("005001")
    mgr7 = snap7["manager"]
    check("L2", "V7 manager语义命名-源端点锚",
          mgr7["name"] == "何帅" and "年又" in mgr7["total_tenure"]
          and "亿(" in mgr7["current_scale_text"]
          and isinstance(mgr7["return_chart"], dict)
          and "categories" in mgr7["return_chart"]
          and all(k not in mgr7 for k in ("tenure_start", "fund_size_yi", "return_pct")),
          f"{mgr7['name']} {mgr7['total_tenure']} {mgr7['current_scale_text']} "
          f"return_chart=图表对象；旧键已除名（V0.7.0 破坏性改名）")
    check("L2", "V7 metrics-现申购费字段(改名后)",
          fd.fund_metrics(snap7)["subscription_fee_pct"] == 0.15,
          "005001 现申购费 0.15%（注意非管理费——管理费走 fund_fees）")


# ════════════════════ L3 交叉验证（三源互证）════════════════════
def test_l3_cross():
    from datetime import date, timedelta
    snap = fd.fund_snapshot("161725")
    e2_last = snap["nav_history"][-1]
    # 窗口锚定 E2 末条净值日期（曾是硬编码日期 → 周末/隔日即炸的时间炸弹，已改动态）
    _end = e2_last["date"]
    _start = (date.fromisoformat(_end) - timedelta(days=35)).isoformat()
    hist = fd.fund_nav_history("161725", start=_start, end=_end)
    check("L3", "净值互证 E2 vs E3",
          hist[0]["date"] == e2_last["date"]
          and abs(hist[0]["nav"] - e2_last["nav"]) < 1e-4,
          f"E2末值{e2_last['date']}:{e2_last['nav']} / E3首行{hist[0]['date']}:{hist[0]['nav']}")

    rt = {x["code"]: x for x in fd.fund_realtime_nav(["161725"])}
    check("L3", "净值互证 E2 vs E6",
          rt["161725"]["nav"] and abs(rt["161725"]["nav"] - e2_last["nav"]) < 1e-4,
          f"E6:{rt['161725']['nav']} @{rt['161725']['nav_date']}")

    hold = fd.fund_holdings("161725")
    latest = hold[0]["period"]
    e4a_codes = {h["stock_code"] for h in hold if h["period"] == latest}
    e2_codes = set(snap["top_stocks"])
    overlap = e2_codes & e4a_codes
    check("L3", "持仓互证 E2 vs E4a", len(overlap) >= 8,
          f"最新报告期{latest} 交集{len(overlap)}/E4a{len(e4a_codes)}/E2{len(e2_codes)}")

    # 本地指标 vs 官方同期收益（E2 自带 ground truth）
    # EM"近1年"口径实证（2026-11-09/10 双日校准）：周年日锚定——end 减 1 年同日，
    # 落在非交易日时回退到 ≤该日的最后一个净值日。残差稳定 0.07pct。
    # （曾踩两坑：①date.today() 回溯周末漂 3pct+；②末净值日−365 自然日在周年日落
    #  周末时起点跳到下一交易日，急跌段差 3.5pct——2026-11-10 实测 -27.29 vs -23.77）
    _end_d = date.fromisoformat(e2_last["date"])
    try:
        _anniv = _end_d.replace(year=_end_d.year - 1)
    except ValueError:                               # 2/29 周年 → 2/28
        _anniv = _end_d.replace(year=_end_d.year - 1, day=28)
    _adj_all = fd.adjusted_nav(snap["nav_history"])
    _base = max((a for a in _adj_all if a["date"] <= _anniv.isoformat()),
                key=lambda a: a["date"], default=_adj_all[0])
    m = fd.fund_metrics(snap, start=_base["date"])
    official = fd._to_float(snap["period_returns_pct"].get("1year"))
    mine = m["total_return_pct"]
    check("L3", "指标互证 复权收益 vs 官方syl_1n",
          official is not None and mine is not None and abs(mine - official) <= 0.3,
          f"本地{mine}%（{_base['date']}起）vs 官方{official}%")

    # 十年期国债收益率三源互证：中债年文件 vs 东财日频 vs push2 实时
    # （东财 zmgzsyl 页本身即中债口径 → 两源应近乎一致；push2 为盘中实时，宽容差）
    # 三源互证必须保持同一时点；L2 的点数检查才使用固定历史年
    cb = [p for p in fd.cb_yield_curve("treasury", year=date.today().year)["points"]
          if p["term"] == "10y"]
    cb_latest_date = max(p["date"] for p in cb)
    cb_v = [p["yield_pct"] for p in cb if p["date"] == cb_latest_date][0]
    em_v = fd.treasury_yield_daily(days=1)[0]["cn_10y"]
    rt_v = fd.cn_treasury_realtime()["10y"]["yield_pct"]
    check("L3", "10Y国债三源互证(中债↔东财↔push2)",
          abs(cb_v - em_v) <= 0.05 and abs(rt_v - cb_v) <= 0.25,
          f"中债{cb_v}({cb_latest_date}) / 东财{em_v} / 实时{rt_v}")


# ════════════════════ L4 请求预算审计 ═════════════════════
def test_l4_budget():
    fd.TT_REQUEST_COUNT[0] = 0
    fd.TT_REQUEST_COUNT[1].clear()
    fd.fund_snapshot("161725", force_refresh=True)
    n1, hosts = fd.TT_REQUEST_COUNT[0], dict(fd.TT_REQUEST_COUNT[1])
    # V0.7.7：快照 = E2 + 主页面限额页（同域）。force_refresh 只重拉 E2；限额页独立
    # 当日缓存键——真冷 2 发，限额已被 L2 预热则 1 发，两种顺序形态都合法。
    check("L4", "流A-首次诊断≤3请求", n1 <= 3 and hosts.get("fund.eastmoney.com") in (1, 2),
          f"共{n1}次 {hosts}")

    fd.TT_REQUEST_COUNT[0] = 0
    fd.TT_REQUEST_COUNT[1].clear()
    fd.fund_snapshot("161725")
    check("L4", "流A-缓存命中0请求", fd.TT_REQUEST_COUNT[0] == 0,
          f"实发{fd.TT_REQUEST_COUNT[0]}次")

    fd.TT_REQUEST_COUNT[0] = 0
    fd.TT_REQUEST_COUNT[1].clear()
    fd.fund_reports("161725", force_refresh=True)     # V0.7.8：清单 1 请求（pageSize=100）
    n2, hosts2 = fd.TT_REQUEST_COUNT[0], dict(fd.TT_REQUEST_COUNT[1])
    check("L4", "流A-报告清单1请求", n2 == 1 and "api.fund.eastmoney.com" in hosts2,
          f"共{n2}次 {hosts2}")


# ════════════════════ L5 降级演练 + 坑回归 ═════════════════════
def test_l5_degrade():
    # 坑P4：股票代码误入
    expect_raise("L5", "坑P4-股票代码拦截(600519)", lambda: fd.norm_fund_code("600519"),
                 "a-stock-data")
    # A/C 份额消歧：不猜
    r = fd.norm_fund_code("华夏成长")
    check("L5", "坑-份额族消歧(华夏成长)", r["resolved"] is None and len(r["matches"]) >= 2,
          f"{len(r['matches'])}候选: {[f['code'] for f in r['matches'][:4]]}")
    # 名称模糊命中
    r = fd.norm_fund_code("161725.OF")
    check("L5", "归一化-.OF后缀", r["resolved"] == "161725", str(r["resolved"]))

    # E2 失败 → 降级快照（mock：pingzhongdata URL 抛网络异常，其余端点透传）
    orig = fd.ttfund_get

    def fake_get(url, *a, **kw):
        if "pingzhongdata" in url:
            raise fd.requests.ConnectionError("mock: E2 down")
        return orig(url, *a, **kw)

    fd.ttfund_get = fake_get
    try:
        (fd.FUND_CACHE_DIR / "snapshots" / "110022.json").unlink(missing_ok=True)
        deg = fd.fund_snapshot("110022", force_refresh=True)
        nav = deg["nav_history"]
        check("L5", "降级-E2挂→分拆出数",
              deg["_meta"]["source"].startswith("degraded")
              and len(nav) > 150 and len(nav) < 400 and deg["name"],
              f"source={deg['_meta']['source']} 净值{len(nav)}条 "
              f"经理{deg['manager'].get('name')}")
        # 坑P7 钉子：降级快照必须已反转为升序（E3 原生新→旧），指标可跑且窗口为正
        check("L5", "降级-净值已反转为升序(坑P7/窗口1年)",
              nav[0]["date"] < nav[-1]["date"],
              f"{nav[0]['date']} → {nav[-1]['date']}")
        # V0.7.7 一致性：降级快照不拉主页面，purchase_limit 显式缺失（键存在+missing 标注）
        check("L5", "降级-purchase_limit显式缺失",
              deg.get("purchase_limit") is None
              and "purchase_limit(degraded-skip)" in deg["missing_datasets"])
        dm = fd.fund_metrics(deg)
        check("L5", "降级-指标方向自洽",
              dm["window"]["days"] > 200 and dm["window"]["days"] < 420 and abs(dm["total_return_pct"]) < 500,
              f"{dm['window']['days']}天 收益{dm['total_return_pct']}%")
        check("L5", "降级-不写缓存", not (fd.FUND_CACHE_DIR / "snapshots" / "110022.json").exists(),
              "下次调用重试主端点")
    finally:
        fd.ttfund_get = orig



def test_f_guard_band():                             # 防封专项（F1/F2/F6，零网络为主）
    import subprocess, sys as _sys
    # F6: 403 ≠ 网络故障 → RiskControlError 直接上抛，绝不触发降级
    class _Resp403:
        status_code = 403
        text = ""
        def raise_for_status(self):
            import requests as _rq
            raise _rq.HTTPError("403")
    _orig_get = fd.ttfund_get
    fd.ttfund_get = lambda *a, **k: _Resp403()
    fd._cbreak_state["tt"] = [0, 0.0]   # 新格式 [连击, 上次时刻]
    try:
        try:
            fd.fund_snapshot("161725", force_refresh=True)
            check("F6", "403不触发降级", False, "未抛出")
        except fd.RiskControlError:
            check("F6", "403不触发降级", True, "RiskControlError 上抛（非 degraded source）")
        except Exception as e:
            check("F6", "403不触发降级", False, f"错误类型: {type(e).__name__}")
    finally:
        fd.ttfund_get = _orig_get
    (fd.FUND_CACHE_DIR / "snapshots" / "161725.json").unlink(missing_ok=True)

    # F2: 熔断冷却期内 ttfund_get 直接拒绝（不发网络请求）
    # core 路由：冷却闸门由协调器三连击开启（legacy 落盘文件已退位为镜像）；
    # 拒绝发生在网关 acquire，transport 与请求账本都不会被触碰。
    for _sig in range(3):
        try:
            fd._cbreak_signal("tt", "F2 冷却演练")
        except fd.RiskControlError:
            pass
    n_before = fd.TT_REQUEST_COUNT[0]
    try:
        fd.ttfund_get("https://fund.eastmoney.com/js/fundcode_search.js")
        check("F2", "冷却期拒绝请求", False, "未拒绝")
    except RuntimeError as e:
        zero_net = fd.TT_REQUEST_COUNT[0] == n_before
        check("F2", "冷却期拒绝请求", "熔断" in str(e) and zero_net,
              f"拒绝且零网络请求 ✓ {str(e)[:30]}")
    fd._cbreak_file("tt").unlink(missing_ok=True)
    _fresh_coordination()               # 闸门复位，后续在线用例不受演练影响

    # F1: 双进程并发节流——合并间隔不得小于单线阈值×0.8
    code = (
        "import sys, time; sys.path.insert(0, '.'); import fund_data as fd\n"
        "fd._cross_proc_throttle('xproc_test', 1.0)\n"
        "print(time.time())"
    )
    p1 = subprocess.Popen([_sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    p2 = subprocess.Popen([_sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    t1 = float(p1.communicate()[0].strip())
    t2 = float(p2.communicate()[0].strip())
    gap = abs(t2 - t1)
    (fd.FUND_CACHE_DIR / ".throttle_xproc_test").unlink(missing_ok=True)
    check("F1", "跨进程节流(双进程合并间隔)", gap >= 0.8,
          f"两进程时间差 {gap:.2f}s（阈值1.0s，实测合并未超频）")


def test_f_cache_band():                             # F4/F7 缓存专项（在线，便宜）
    fd.TT_REQUEST_COUNT[0] = 0
    fd.treasury_yield_daily(days=30)                 # 首次（可能命中昨日缓存）
    n1 = fd.TT_REQUEST_COUNT[0]
    fd.treasury_yield_daily(days=30)                 # 二次必命中当日缓存
    n2 = fd.TT_REQUEST_COUNT[0] - n1
    check("F7", "宏观层二次调用0请求", n2 == 0, f"实发 {n2} 次")
    fd.fund_holdings("161725")
    n3 = fd.TT_REQUEST_COUNT[0]
    fd.fund_holdings("161725")
    n4 = fd.TT_REQUEST_COUNT[0] - n3
    check("F4", "持仓二次调用0请求", n4 == 0, f"实发 {n4} 次")
    # V0.5 全市场层：排行/公司（tt 线账本）与抱团度（cninfo 线账本）二次 0 请求
    n5 = fd.TT_REQUEST_COUNT[0]
    fd.fund_rank()
    n6 = fd.TT_REQUEST_COUNT[0] - n5
    check("F4", "排行全量二次调用0请求", n6 == 0, f"实发 {n6} 次")
    fd.fund_company("80065113")
    n7 = fd.TT_REQUEST_COUNT[0]
    fd.fund_company("80065113")
    n8 = fd.TT_REQUEST_COUNT[0] - n7
    check("F4", "公司画像二次调用0请求", n8 == 0, f"实发 {n8} 次")
    cn5 = fd.CNINFO_REQUEST_COUNT[0]
    fd.fund_crowding()
    cn6 = fd.CNINFO_REQUEST_COUNT[0] - cn5
    check("F4", "抱团度二次调用0请求(cninfo账本)", cn6 == 0, f"实发 {cn6} 次")
    # V0.6 专项层缓存带
    n9 = fd.TT_REQUEST_COUNT[0]
    fd.fund_scale("161725")
    n10 = fd.TT_REQUEST_COUNT[0] - n9
    check("F4", "规模二次调用0请求", n10 == 0, f"实发 {n10} 次")
    fd.fund_manager("161725")
    n11 = fd.TT_REQUEST_COUNT[0]
    fd.fund_manager("161725")
    n12 = fd.TT_REQUEST_COUNT[0] - n11
    check("F4", "经理画像二次调用0请求", n12 == 0, f"实发 {n12} 次")
    tc1 = fd.TENCENT_REQUEST_COUNT[0]
    fd.nav_divergence("161725")
    tc2 = fd.TENCENT_REQUEST_COUNT[0] - tc1
    check("F4", "重仓探测当日K线缓存0腾讯请求", tc2 == 0, f"实发 {tc2} 次（当日K线缓存）")
    fd.TT_REQUEST_COUNT[1].clear()          # F 段复位清零后同步清 host 明细，账本打印自洽


def test_sprint2_integration_offline():
    """真实业务函数 + fake transport 的协调器集成回归（零上游、临时状态）。"""
    from fund_data_core import RiskControlError as CoreRisk, CircuitOpenError as CoreCircuit

    class _Clock:
        def __init__(self):
            self.now = 1000.0

        def time(self):
            return self.now

        def sleep(self, seconds):
            self.now += max(0.0, seconds)

    class _Resp:
        def __init__(self, status=200, text="", payload=None):
            self.status_code = status
            self.text = text
            self.content = text.encode("utf-8")
            self._payload = payload

        def json(self):
            if self._payload is not None:
                return self._payload
            return json.loads(self.text)

        def raise_for_status(self):
            return None

    temps = []
    _clock_box = {}

    def _reset():
        tmp = tempfile.TemporaryDirectory(prefix="fund-s2-integration-")
        temps.append(tmp)
        fd.FUND_CACHE_DIR = Path(tmp.name)
        clock = _Clock()
        _clock_box["clock"] = clock
        coordinator = fd._reset_request_coordination(clock=clock)
        fd._FUND_LIST_MEM[:] = []
        for line in ("tt", "em", "cninfo", "danjuan"):
            fd._cbreak_state[line] = [0, 0.0]
        return coordinator

    def _list_body(count=5000):
        rows = [[f"{i:06d}", f"基金{i}", f"fund{i}", "混合型-偏股"]
                for i in range(count)]
        return "var fundcode_search = " + json.dumps(rows, ensure_ascii=False)

    def _counts(coordinator, provider):
        snap = coordinator.snapshot(provider)
        return (snap["reserved_slots"], snap["network_started"], snap["responses"],
                snap["active_in_flight"])

    orig_get, orig_post = fd.TT_SESSION.get, fd.TT_SESSION.post
    orig_session = fd.requests.Session
    orig_build_opener = fd.urllib.request.build_opener
    orig_push2, orig_key = fd._push2_get, fd._cninfo_enckey
    orig_cache_dir = fd.FUND_CACHE_DIR
    try:
        coordinator = _reset()
        check("S2", "异常类型与core统一",
              fd.RiskControlError is CoreRisk and fd.CircuitOpenError is CoreCircuit)
        check("S2", "默认预算东财240/300且cninfo间隔1.5",
              coordinator.policies["eastmoney"].max_requests == 240
              and coordinator.policies["eastmoney"].window_seconds == 300
              and coordinator.policies["cninfo"].min_interval == 1.5)
        # 策略字段覆盖必须在模块预算之上合并（240 余量绝不能回落 DEFAULT 的 300）。
        coordinator = _reset()
        overridden = fd._reset_request_coordination(
            clock=_Clock(), policies={"eastmoney": {"min_interval": 0}})
        check("S2", "策略覆盖不丢防封预算余量",
              overridden.policies["eastmoney"].max_requests == 240
              and overridden.policies["eastmoney"].min_interval == 0)

        coordinator = _reset()
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body())
        fd.fund_list(force_refresh=True)
        fd.fund_list(force_refresh=True)
        check("S2", "同进程两次真实业务调用全分账且无许可泄漏",
              _counts(coordinator, "eastmoney") == (2, 2, 2, 0)
              and fd.TT_REQUEST_COUNT[0] >= 2, str(_counts(coordinator, "eastmoney")))

        for status, body in ((403, "forbidden"), (429, "limited"), (200, "无访问权限")):
            coordinator = _reset()
            fd.TT_SESSION.get = lambda *a, s=status, b=body, **k: _Resp(s, b)
            try:
                fd.ttfund_get("https://fund.eastmoney.com/x")
                check("S2", f"真实入口风控{status}/{body}", False, "未抛错")
            except fd.RiskControlError:
                check("S2", f"真实入口风控{status}/{body}", True)
            snap = coordinator.snapshot("eastmoney")
            check("S2", f"风控{status}/{body}响应分账",
                  (snap["reserved_slots"], snap["network_started"], snap["responses"],
                   snap["consecutive_risk"], snap["active_in_flight"]) == (1, 1, 1, 1, 0))

        coordinator = _reset()
        calls = []
        fd.TT_SESSION.get = lambda *a, **k: calls.append(1) or _Resp(403, "forbidden")
        for _ in range(3):
            try:
                fd.ttfund_get("https://fund.eastmoney.com/x")
            except fd.RiskControlError:
                pass
        try:
            fd.ttfund_get("https://fund.eastmoney.com/x")
            check("S2", "OPEN阻止真实transport", False, "未抛CircuitOpenError")
        except fd.CircuitOpenError:
            check("S2", "OPEN阻止真实transport", len(calls) == 3,
                  f"transport={len(calls)}")

        # 半开恢复闭环：开闸 → 冷却期满 → 健康探针 + 业务校验成功 → 闸门 CLOSED。
        # （若探针中性结算被误判为失败重开、或业务清零不关闸，此处必然卡死在 OPEN。）
        _clock_box["clock"].now += 601
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body())
        fd.fund_list(force_refresh=True)
        snap = coordinator.snapshot("eastmoney")
        check("S2", "半开探针+业务成功后闸门恢复",
              snap["circuit"] == "CLOSED" and snap["consecutive_risk"] == 0,
              f"circuit={snap['circuit']} streak={snap['consecutive_risk']}")

        coordinator = _reset()
        fd.TT_SESSION.get = lambda *a, **k: _Resp(500, "server error")
        response = fd.ttfund_get("https://fund.eastmoney.com/x")
        snap = coordinator.snapshot("eastmoney")
        check("S2", "HTTP500计响应但非业务成功且释放许可",
              response.status_code == 500 and snap["responses"] == 1
              and snap["consecutive_risk"] == 0 and snap["active_in_flight"] == 0)

        coordinator = _reset()
        fd._cbreak_signal("tt", "seed")
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body(12))
        try:
            fd.fund_list(force_refresh=True)
        except RuntimeError:
            pass
        check("S2", "解析失败不清业务连击",
              coordinator.snapshot("eastmoney")["consecutive_risk"] == 1
              and coordinator.snapshot("eastmoney")["active_in_flight"] == 0)
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body())
        fd.fund_list(force_refresh=True)
        check("S2", "业务完整性成功后才清零",
              coordinator.snapshot("eastmoney")["consecutive_risk"] == 0)

        coordinator = _reset()
        outcomes = [fd.requests.RequestException("reset"), _Resp(200, "ok")]
        fd.TT_SESSION.get = lambda *a, **k: outcomes.pop(0)
        before_tt = fd.TT_REQUEST_COUNT[0]
        fd.ttfund_get("https://fund.eastmoney.com/x")
        check("S2", "网络重试独立预留和计数",
              _counts(coordinator, "eastmoney") == (2, 2, 1, 0)
              and fd.TT_REQUEST_COUNT[0] == before_tt + 2,
              str(_counts(coordinator, "eastmoney")))

        coordinator = _reset()
        fd._cbreak_signal("em", "seed")
        fd._push2_get = lambda *a, **k: {"data": {"klines": ["2026-01-02,3000.5"]}}
        payload = {"data": {"sz000905": {"qfqday": [
            ["2026-01-02", "1", "3.0"], ["2026-01-03", "1", "3.1"],
            ["2026-01-06", "1", "3.2"], ["2026-01-07", "1", "3.3"],
            ["2026-01-08", "1", "3.4"], ["2026-01-09", "1", "3.5"],
            ["2026-01-10", "1", "3.6"], ["2026-01-13", "1", "3.7"],
            ["2026-01-14", "1", "3.8"], ["2026-01-15", "1", "3.9"]]}}}
        fd.urllib.request.build_opener = lambda *a, **k: type("Opener", (), {
            "open": lambda self, req, timeout=None: type("R", (), {
                "read": lambda self: json.dumps(payload).encode("utf-8")})()})()
        rows = fd.kline_daily("0.000905", lmt=10, force_refresh=True)
        check("S2", "腾讯备胎成功不清东财成功语义",
              len(rows) == 10 and coordinator.snapshot("eastmoney")["consecutive_risk"] == 1
              and _counts(coordinator, "tencent") == (1, 1, 1, 0))

        coordinator = _reset()
        seq = [_Resp(200, payload={"resultcode": 451, "resultmsg": "token null"}),
               _Resp(200, payload={"resultcode": 200, "records": [{"SECCODE": "300308"}]})]
        fd.TT_SESSION.post = lambda *a, **k: seq.pop(0)
        fd._cninfo_enckey = lambda ts=None: "mock="
        fd._cninfo_post("p_sysapi1112", {}, "S2")
        check("S2", "cninfo451两次独立配额和响应",
              _counts(coordinator, "cninfo") == (2, 2, 2, 0))

        coordinator = _reset()
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body())
        fd.fund_list(force_refresh=True)
        fd._cbreak_signal("tt", "cache-seed")
        before = _counts(coordinator, "eastmoney")
        fd.fund_list()
        check("S2", "纯缓存0配额且不清零",
              _counts(coordinator, "eastmoney") == before
              and coordinator.snapshot("eastmoney")["consecutive_risk"] == 1)

        coordinator = _reset()
        calls = []
        fd.TT_SESSION.get = lambda *a, **k: calls.append(1) or _Resp(200, "ok")
        try:
            fd.ttfund_get("https://example.com/x")
            check("S2", "未知host拒绝且零transport", False, "未抛错")
        except Exception as exc:
            check("S2", "未知host拒绝且零transport",
                  type(exc).__name__ == "UnknownHostError" and not calls)

        coordinator = _reset()
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, "{}")
        fd.ttfund_get("https://danjuanfunds.com/djapi/fund/161725")
        check("S2", "蛋卷独立provider不消耗东财配额",
              _counts(coordinator, "danjuan") == (1, 1, 1, 0)
              and _counts(coordinator, "eastmoney") == (0, 0, 0, 0))
    finally:
        fd.TT_SESSION.get, fd.TT_SESSION.post = orig_get, orig_post
        fd.requests.Session = orig_session
        fd.urllib.request.build_opener = orig_build_opener
        fd._push2_get, fd._cninfo_enckey = orig_push2, orig_key
        fd.FUND_CACHE_DIR = orig_cache_dir
        # 在线分层测试同样走 core 路径：重建真实时钟/真实状态目录的协调器，
        # 不携带 FakeClock 脏状态。
        fd._reset_request_coordination()
        for tmp in temps:
            tmp.cleanup()


def test_sprint3_integration_offline():
    """Sprint 3 缓存核心接入回归：平移层字节兼容 + fund_list get_or_load 榜样。"""
    from fund_data_core import StaleDataError as CoreStale

    class _Resp:
        def __init__(self, status=200, text=""):
            self.status_code = status
            self.text = text
            self.content = text.encode("utf-8")

        def raise_for_status(self):
            return None

    def _list_body(count=5000):
        rows = [[f"{i:06d}", f"基金{i}", f"fund{i}", "混合型-偏股"]
                for i in range(count)]
        return "var fundcode_search = " + json.dumps(rows, ensure_ascii=False)

    temps = []

    def _reset():
        tmp = tempfile.TemporaryDirectory(prefix="fund-s3-integration-")
        temps.append(tmp)
        fd.FUND_CACHE_DIR = Path(tmp.name)
        fd._reset_request_coordination(policies=_ZERO_POLICY)
        fd._FUND_LIST_MEM[:] = []
        return fd._CACHE_REPO

    check("S3", "异常类型与core统一", fd.StaleDataError is CoreStale)

    # ── 平移层：_cache_read/_cache_write 与 legacy JSON 语义等价 ──
    _reset()
    fd._cache_write("unit/t1.json", {"a": 1, "中文": True})
    raw = (fd.FUND_CACHE_DIR / "unit" / "t1.json").read_text(encoding="utf-8")
    check("S3", "平移-写路径JSON语义等价（紧凑序列化、无schema标记）",
          json.loads(raw) == {"a": 1, "中文": True} and "_meta" not in raw)
    check("S3", "平移-新鲜命中", fd._cache_read("unit/t1.json", 1) == {"a": 1, "中文": True})
    check("S3", "平移-miss返回None", fd._cache_read("unit/none.json", 1) is None)
    p = fd.FUND_CACHE_DIR / "unit" / "t1.json"
    old = time.time() - 7200
    os.utime(p, (old, old))
    check("S3", "平移-过期返回None", fd._cache_read("unit/t1.json", 1) is None)
    check("S3", "平移-ttl不过期", fd._cache_read("unit/t1.json", None) == {"a": 1, "中文": True})
    p.write_text("{broken", encoding="utf-8")
    check("S3", "平移-损坏返回None不抛", fd._cache_read("unit/t1.json", 1) is None)

    # ── fund_list get_or_load 榜样：冷启装载/命中零请求/失败不写缓存/异常保真 ──
    orig_get = fd.TT_SESSION.get                       # 先存原值再打补丁（S2 同款写法）
    fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body())
    try:
        repo = _reset()
        before = fd.TT_REQUEST_COUNT[0]
        rows = fd.fund_list(force_refresh=True)
        check("S3", "榜样板-冷启装载",
              len(rows) == 5000 and fd.TT_REQUEST_COUNT[0] == before + 1
              and (fd.FUND_CACHE_DIR / "fund_list.json").exists())
        fd._FUND_LIST_MEM[:] = []                      # 清进程内存，逼走文件缓存
        before = fd.TT_REQUEST_COUNT[0]
        rows2 = fd.fund_list()
        check("S3", "榜样板-文件命中零请求零配额",
              fd.TT_REQUEST_COUNT[0] == before
              and fd._COORDINATOR.snapshot("eastmoney")["reserved_slots"] == 1
              and rows2[0]["code"] == "000000")

        repo = _reset()
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body(12))
        expect_raise("S3", "榜样板-结构异常上抛",
                     lambda: fd.fund_list(force_refresh=True), "疑似上游结构变更")
        check("S3", "榜样板-失败不写缓存",
              not (fd.FUND_CACHE_DIR / "fund_list.json").exists())
        fd.TT_SESSION.get = lambda *a, **k: _Resp(200, _list_body())
        rows3 = fd.fund_list(force_refresh=True)       # retry_after=0：立刻重试可成功
        check("S3", "榜样板-失败后立即重试成功", len(rows3) == 5000)

        repo = _reset()
        fd._cache_write("fund_list.json", [{"code": "旧", "pinyin": "j",
                                            "name": "旧清单", "type": "x"}])
        stale = fd.FUND_CACHE_DIR / "fund_list.json"
        past = time.time() - 40 * 24 * 3600
        os.utime(stale, (past, past))
        fd.TT_SESSION.get = lambda *a, **k: _Resp(403, "forbidden")
        try:
            fd.fund_list()
            check("S3", "榜样板-旧缓存不吞风控异常", False, "未抛错")
        except fd.RiskControlError:
            check("S3", "榜样板-旧缓存不吞风控异常",
                  True, "StaleDataError 保真上抛诱因 RiskControlError")
        except Exception as e:
            check("S3", "榜样板-旧缓存不吞风控异常", False,
                  f"错误类型: {type(e).__name__}")

        # 并发冷启收敛：4 线程同时首调，装载器只执行一次（跨线程锁）
        repo = _reset()
        hits = []

        def _slow_load(*a, **k):
            hits.append(1)
            time.sleep(0.25)
            return _Resp(200, _list_body())

        fd.TT_SESSION.get = _slow_load
        import threading
        barrier = threading.Barrier(4)
        results = [None] * 4

        def _worker(i):
            barrier.wait(5)
            results[i] = len(fd.fund_list())

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        check("S3", "榜样板-并发冷启单一装载",
              len(hits) == 1 and all(r == 5000 for r in results),
              f"装载 {len(hits)} 次，结果一致 {all(r == 5000 for r in results)}")

        # teardown 回退：repo 置 None 后 legacy 直读写路径仍可用
        fd._teardown_request_coordination()
        fd._cache_write("unit/legacy.json", [1, 2])
        check("S3", "teardown回退-legacy直读写",
              fd._cache_read("unit/legacy.json", 1) == [1, 2]
              and fd._CACHE_REPO is None and fd._REQUEST_GATEWAY is None)
    finally:
        fd.TT_SESSION.get = orig_get
        fd._FUND_LIST_MEM[:] = []          # 假清单不得残留进程内存（在线层在后面跑）
        fd._teardown_request_coordination()
        fd.FUND_CACHE_DIR = Path(_TEST_CACHE_TMP.name)
        if OFFLINE_ONLY:
            fd._reset_request_coordination(policies=_ZERO_POLICY)
        else:                              # 在线层保持真实策略（S2 finally 同口径，防封节流不裸奔）
            fd._reset_request_coordination()
        for tmp in temps:
            tmp.cleanup()


def test_csindex_online():
    uni = fd.csindex_universe()
    uni_all = fd.csindex_universe(currency=None)
    check("L2", "中证清单-全系列真实覆盖",
          len(uni) >= 2700 and len(uni_all) >= 3000
          and len({x["index_series"] for x in uni_all}) == 7,
          f"人民币 {len(uni)} 个，全币种 {len(uni_all)} 个")
    expected = {"000300": "H00300", "H30269": "H20269", "932000": "932000CNY010"}
    for base, tr_code in expected.items():
        mapping = fd.csindex_resolve_total_return(base)
        check("L2", f"中证映射-{base}", mapping.get("total_return_code") == tr_code
              and mapping.get("status") == "mapped")
        sample = fd.csindex_total_return_daily(base, "20240101", "20240110")
        check("L2", f"中证历史-{base}真实样本",
              len(sample) == 7 and sample[0]["index_code"] == tr_code
              and sample[0]["date"] == "2024-01-02" and sample[-1]["date"] == "2024-01-10")
    rows = fd.csindex_total_return_daily("000300", "20240101", "20241231")
    check("L2", "中证历史-沪深300全收益2024完整区间",
          len(rows) == 242 and rows[0]["date"] == "2024-01-02"
          and rows[-1]["date"] == "2024-12-31"
          and rows[0]["index_code"] == "H00300"
          and all(x["date"] != "2024-01-01" for x in rows),
          f"{len(rows)} 行，{rows[0]['date']}→{rows[-1]['date']}")
    check("L2", "中证历史-pe_ttm在线锚点",          # V0.8.1：上游 peg 字段改名预警
          any(r.get("pe_ttm") is not None for r in rows),
          f"全列 pe_ttm=None（共 {len(rows)} 行）——上游字段漂移嫌疑")
    annual_return = rows[-1]["close"] / rows[0]["close"] - 1
    check("L3", "中证历史-沪深300全收益年度锚点",
          abs(rows[0]["close"] - 4709.92) < 1e-9
          and abs(rows[-1]["close"] - 5642.60) < 1e-9
          and abs(annual_return - 0.198025) < 0.00001,
          f"收益 {annual_return * 100:.4f}%")
    before = fd.CSINDEX_REQUEST_COUNT[0]
    fd.csindex_total_return_daily("000300", "20240102", "20240630")
    check("L4", "中证历史-缓存切片零请求", fd.CSINDEX_REQUEST_COUNT[0] == before)
    sync = fd.csindex_sync_total_return(codes=["000300"], start_date="20240101",
                                        end_date="20241231", resume=False)
    check("L4", "中证同步-真实限定样本复用缓存",
          sync["ok"] == 1 and sync["requests"] == 0, f"{sync['rows'] if 'rows' in sync else 'ok'}")


def test_bench_spot_online():
    """V0.8.1：BENCH_SECID 100 个 BK 行业映射在线抽查（P3 审查项）。

    映射主体单源于 clist 快照（仅 BK0475/BK1036 双样本验证过）——用 kline 接口
    回读 name 交叉验证抽查 3 个（每映射 1 请求，em 线预算内；映射错=静默拿错
    行业基准的数值风险）。"""
    for name, secid in (("银行Ⅱ", "90.BK0475"), ("半导体", "90.BK1036"),
                        ("房地产开发", "90.BK0451")):
        try:
            d = (fd._push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                               {"secid": secid, "klt": 101, "fqt": 1, "lmt": 5,
                                "end": "20500101", "fields1": "f1,f2,f3",
                                "fields2": "f51,f53"}, f"行业基准抽查 {secid}")
                 .get("data") or {})
        except fd.RiskControlError:
            raise                                 # 风控信号绝不 SKIP 豁免（F6 纪律；
                                                 # 注意其是 RuntimeError 子类，必须
                                                 # 先于下方 except 分流——复审抓的坑）
        except RuntimeError as e:
            # 辅助溯源抽查不判死整套（P26 惩罚期桶空/连接级熔断时零网络跳过）
            check("SKIP", f"行业基准映射抽查-{name}", True,
                  f"push2 不可用跳过（{str(e)[:60]}）")
            continue
        check("L2", f"行业基准映射抽查-{name}",
              (d.get("name") or "").strip() == name,
              f"secid={secid} 实回 name={d.get('name')!r}")


def main():
    print(f"fund-data {fd.__version__} 分层测试  offline_only={OFFLINE_ONLY}\n")
    test_l1_offline()
    test_csindex_offline()
    test_sws_offline()
    test_sprint2_integration_offline()
    test_sprint3_integration_offline()
    if not OFFLINE_ONLY:
        # 逐函数隔离（冷跑教训：单函数 transient 逃逸曾连带跳过 L3/L4/L5/F 全部信号）；
        # 栈全文走 stdout（print_exc 走 stderr 会与 check 的 stdout 在管道下错序截断）
        for fn in (test_l2_smoke, test_csindex_online, test_sws_online, test_bench_spot_online,
                   test_l3_cross, test_l4_budget,
                   test_l5_degrade, test_f_guard_band, test_f_cache_band):
            try:
                fn()
            except Exception:
                tb = traceback.format_exc()
                print(tb)
                R["fail"] += 1
                R["results"].append(("ONLINE", f"{fn.__name__} 异常中止", "FAIL",
                                     tb[-250:] if len(tb) > 250 else tb))
    print("\n──── 汇总 ────")
    by_layer = {}
    for layer, name, st, detail in R["results"]:
        by_layer.setdefault(layer, [0, 0])
        by_layer[layer][0 if st == "PASS" else 1] += 1
    for layer, (p, f) in by_layer.items():
        print(f"  {layer}: {p} pass / {f} fail")
    print(f"  合计: {R['pass']} pass / {R['fail']} fail")
    if not OFFLINE_ONLY:
        print(f"  四线请求账本: 基金域东财 {fd.TT_REQUEST_COUNT[0]} {fd.TT_REQUEST_COUNT[1]}"
              f" | 股票域东财 {fd.EM_REQUEST_COUNT[0]} | 腾讯 {fd.TENCENT_REQUEST_COUNT[0]}"
              f" | 巨潮 {fd.CNINFO_REQUEST_COUNT[0]}")
    sys.exit(1 if R["fail"] else 0)


if __name__ == "__main__":
    main()
