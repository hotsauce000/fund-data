# -*- coding: utf-8 -*-
"""
fund-data V0.2 分层测试运行器（按测试计划 V1.0）
  L1 单元（零网络）· L2 端点 smoke（结构断言）· L3 交叉验证（三源互证）
  L4 请求预算审计 · L5 降级演练与坑回归 · L6 穿透层（确定性/降级/路由/清洗）
用法: python3 test_fund_data.py [--offline-only]
"""
import json
import sys
import time
import traceback
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fund_data as fd

_unknown = [a for a in sys.argv[1:] if a != "--offline-only"]
if _unknown:
    sys.exit(f"未知参数 {_unknown}（仅支持 --offline-only）——防封项目工具自身不能意外发真实请求")
OFFLINE_ONLY = "--offline-only" in sys.argv
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

    # 指标：构造序列 [1.0,1.2,0.6,0.9] → 最大回撤 -50%
    snap = {"nav_history": [{"date": f"2026-01-0{i}", "nav": v, "dividend": 0}
                            for i, v in enumerate([1.0, 1.2, 0.6, 0.9], start=1)]}
    m = fd.fund_metrics(snap)
    check("L1", "指标-最大回撤构造验证", abs(m["max_drawdown_pct"] + 50.0) < 1e-6,
          f"dd={m['max_drawdown_pct']}% 窗口={m['max_drawdown_window']}")
    check("L1", "指标-字段完备",
          all(m.get(k) is not None for k in
              ["total_return_pct", "annualized_return_pct", "sharpe", "calmar"]))

    # 缓存损坏容忍（读完即清理，不留垃圾在用户缓存目录）
    bad = fd.FUND_CACHE_DIR / "snapshots" / "_corrupt_test.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    try:
        check("L1", "缓存-损坏文件视为miss", fd._cache_read("snapshots/_corrupt_test.json", None) is None)
    finally:
        bad.unlink(missing_ok=True)

    # ── L6 穿透层单元（零网络）：路由 / 标签清洗 / 注入拦截 ──
    cases = {"920982": "bj920982", "832982": "bj832982", "430047": "bj430047",
             "600519": "sh600519", "000858": "sz000858", "000300": "sh000300",
             "204007": "sh204007"}                       # 沪市回购 GC 族（repo_rate_gc 依赖）
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
    _orig = (fd.fund_holdings, fd.fund_snapshot, fd.tencent_quote, fd.concept_blocks)
    _mk_hold = lambda: [
        {"period": "2026-09-30", "seq": i + 1, "stock_code": c, "stock_name": f"股{i}",
         "nav_pct": 10.0, "shares_wan": 1, "value_wan": 1}
        for i, c in enumerate(["600001", "600002", "600003", "600004"])]
    fd.fund_holdings = lambda q, **k: _mk_hold()
    fd.fund_snapshot = lambda q, **k: {"name": "测试基金"}
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
        (fd.fund_holdings, fd.fund_snapshot, fd.tencent_quote, fd.concept_blocks) = _orig

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
    check("L1", "metrics-费率标注", fd.fund_metrics(
        {"nav_history": _nav, "fees": {"current_rate": "0.12"}})["mgmt_fee_pct"] == 0.12
        and fd.fund_metrics({"nav_history": _nav})["mgmt_fee_pct"] is None)

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
    fd._cache_read = lambda rel, max_age_hours=None: None
    fd._cache_write = lambda rel, payload: None
    try:
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
    check("L1", "架构-瘦SKILL体积上限", _thin_b < 12000,
          f"{_thin_b}B（超重=有人往路由层塞长文，移 reference.md）")
    _pub = [n.name for n in _ast.parse((_repo / "fund_data.py").read_text(encoding="utf-8")).body
            if isinstance(n, _ast.FunctionDef) and not n.name.startswith("_")]
    check("L1", "架构-签名速查全覆盖", len(_pub) >= 32 and all(f"| `{n}(" in _thin for n in _pub),
          f"{len(_pub)} 个公开函数")
    check("L1", "架构-reference内嵌与坑区",
          "def fund_snapshot(query: str, force_refresh: bool = False) -> dict:" in _ref
          and "P18" in _ref and "使用示例" in _ref and "不覆盖" in _ref,
          f"{len(_ref.encode('utf-8'))}B")
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

    fd._cbreak_state["tt"] = [0, 0.0]
    expect_raise("L1", "排行-内容级风控断言(P19)",
                 lambda: fd.assert_rankhandler(_FR(200, 'var r="无访问权限"'), "t"),
                 "无访问权限")
    check("L1", "排行-风控信号计入熔断计数(未达阈值不落盘)",
          fd._cbreak_state["tt"][0] == 1 and not fd._cbreak_file("tt").exists(),
          str(fd._cbreak_state["tt"]))
    fd.assert_rankhandler(_FR(200, 'var records=["161725,x"];'), "t")   # 正常文案不抛
    check("L1", "排行-正常响应清零计数",
          fd._cbreak_state["tt"] == [0, 0.0], str(fd._cbreak_state["tt"]))

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
    finally:
        fd.ttfund_get, fd.fund_list = _orig_get, _orig_fl
        for rel in ("rank/top_gp_20.json", "rank/top_gp_50.json"):
            (fd.FUND_CACHE_DIR / rel).unlink(missing_ok=True)   # mock 数据不入真缓存

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
          and fd._bucket_of(None) is None and fd._bucket_of("商品型") is None,
          "fund_rank 本地过滤的映射依据")


# ════════════════════ L2 端点 smoke（真数据结构断言）════════════════════
def test_l2_smoke():
    fl = fd.fund_list()
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
    curve = fd.cb_yield_curve("treasury", year=date.today().year)
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
    check("L2", "L7 国债实时四期限",
          all(t in rt for t in ("2y", "5y", "10y", "30y"))
          and rt["2y"]["yield_pct"] < rt["10y"]["yield_pct"] < rt["30y"]["yield_pct"],
          f"2Y={rt['2y']['yield_pct']} 10Y={rt['10y']['yield_pct']} 30Y={rt['30y']['yield_pct']}")

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
    check("L4", "流A-首次诊断≤2请求", n1 <= 2 and hosts.get("fund.eastmoney.com") == 1,
          f"共{n1}次 {hosts}")

    fd.TT_REQUEST_COUNT[0] = 0
    fd.TT_REQUEST_COUNT[1].clear()
    fd.fund_snapshot("161725")
    check("L4", "流A-缓存命中0请求", fd.TT_REQUEST_COUNT[0] == 0,
          f"实发{fd.TT_REQUEST_COUNT[0]}次")


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
    fd._cbreak_file("tt").parent.mkdir(parents=True, exist_ok=True)
    fd._cbreak_file("tt").write_text(str(time.time() + 5))
    n_before = fd.TT_REQUEST_COUNT[0]
    try:
        fd.ttfund_get("https://fund.eastmoney.com/js/fundcode_search.js")
        check("F2", "冷却期拒绝请求", False, "未拒绝")
    except RuntimeError as e:
        zero_net = fd.TT_REQUEST_COUNT[0] == n_before
        check("F2", "冷却期拒绝请求", "熔断" in str(e) and zero_net,
              f"拒绝且零网络请求 ✓ {str(e)[:30]}")
    fd._cbreak_file("tt").unlink(missing_ok=True)

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
    fd.TT_REQUEST_COUNT[1].clear()          # F 段复位清零后同步清 host 明细，账本打印自洽


def main():
    print(f"fund-data {fd.__version__} 分层测试  offline_only={OFFLINE_ONLY}\n")
    test_l1_offline()
    if not OFFLINE_ONLY:
        try:
            test_l2_smoke()
            test_l3_cross()
            test_l4_budget()
            test_l5_degrade()
            test_f_guard_band()
            test_f_cache_band()
        except Exception:
            traceback.print_exc()
            R["fail"] += 1
            R["results"].append(("ONLINE", "在线测试异常中止", "FAIL", traceback.format_exc()[:200]))
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
