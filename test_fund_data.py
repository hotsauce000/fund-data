# -*- coding: utf-8 -*-
"""
fund-data V0.2 分层测试运行器（按测试计划 V1.0）
  L1 单元（零网络）· L2 端点 smoke（结构断言）· L3 交叉验证（三源互证）
  L4 请求预算审计 · L5 降级演练与坑回归 · L6 穿透层（确定性/降级/路由/清洗）
用法: python3 test_fund_data.py [--offline-only]
"""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fund_data as fd

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
             "600519": "sh600519", "000858": "sz000858", "000300": "sh000300"}
    check("L1", "穿透-腾讯前缀路由(含92段先于9x)",
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


# ════════════════════ L3 交叉验证（三源互证）════════════════════
def test_l3_cross():
    snap = fd.fund_snapshot("161725")
    e2_last = snap["nav_history"][-1]
    hist = fd.fund_nav_history("161725", start="2026-10-01", end="2026-11-05")
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
    # 官方 syl_1n 窗口=近1年交易日口径，本地=自然日365天前起 → 边界差容差 0.6pct
    from datetime import date, timedelta
    m = fd.fund_metrics(snap, start=(date.today() - timedelta(days=365)).isoformat())
    official = fd._to_float(snap["period_returns_pct"].get("1year"))
    mine = m["total_return_pct"]
    check("L3", "指标互证 复权收益 vs 官方syl_1n",
          official is not None and mine is not None and abs(mine - official) <= 0.6,
          f"本地{mine}% vs 官方{official}%")


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
              and len(nav) > 100 and deg["name"],
              f"source={deg['_meta']['source']} 净值{len(nav)}条 "
              f"经理{deg['manager'].get('name')}")
        # 坑P7 钉子：降级快照必须已反转为升序（E3 原生新→旧），指标可跑且窗口为正
        check("L5", "降级-净值已反转为升序(坑P7)",
              nav[0]["date"] < nav[-1]["date"],
              f"{nav[0]['date']} → {nav[-1]['date']}")
        dm = fd.fund_metrics(deg)
        check("L5", "降级-指标方向自洽",
              dm["window"]["days"] > 300 and abs(dm["total_return_pct"]) < 500,
              f"{dm['window']['days']}天 收益{dm['total_return_pct']}%")
        check("L5", "降级-不写缓存", not (fd.FUND_CACHE_DIR / "snapshots" / "110022.json").exists(),
              "下次调用重试主端点")
    finally:
        fd.ttfund_get = orig


def main():
    print(f"fund-data {fd.__version__} 分层测试  offline_only={OFFLINE_ONLY}\n")
    test_l1_offline()
    if not OFFLINE_ONLY:
        try:
            test_l2_smoke()
            test_l3_cross()
            test_l4_budget()
            test_l5_degrade()
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
        print(f"  全程请求数: {fd.TT_REQUEST_COUNT[0]} → {fd.TT_REQUEST_COUNT[1]}")
    sys.exit(1 if R["fail"] else 0)


if __name__ == "__main__":
    main()
