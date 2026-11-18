# fund-data 复制即用示例（examples.md · 按需读取层）V0.7.8

本文件解决两个问题：**「具体怎么调用」**（13 个高频场景菜谱，复制即跑）与
**「出问题了怎么办」**（FAQ 按现象直达解法）。每条菜谱 = 意图 + 完整代码 +
预期输出形态 + 一行坑提醒。实现细节/坑区全档 → `reference.md`；
场景路由总表 → `SKILL.md`。

## 环境准备（所有示例的前置，跑一次）

```python
import sys
sys.path.insert(0, str(__import__("pathlib").Path.home() / ".agents" / "skills" / "fund-data"))
import fund_data
```

依赖：`pip install requests`；系统 `curl`（仅中债曲线族需要）；系统 `node`
（仅巨潮抱团度/行业配置需要）；pandas 可选。以下示例均假设已执行本块。
防封铁律（请求预算）见 SKILL.md——**任何场景都不要 `for fund in 全量: fund_snapshot(fund)`**。

---

## 场景 1 · 名称/拼音找基金代码（0 请求，当日缓存后）

```python
r = fund_data.norm_fund_code("交银优择")        # 名称包含匹配；拼音如 "jyzh"
print(r["resolved"])                            # 唯一命中→代码；None→看 matches 自己挑
for m in r["matches"]:
    print(m["code"], m["name"], m["type"])
```

输出形态：`{query, matches: [{code,name,type,pinyin}], resolved, note}`。
坑提醒：多候选（A/C 份额）**列出不猜**；`resolved=None` 时必须人工选定。
6 位代码不在清单 → 会提示可能是股票代码（走 a-stock-data skill）。

## 场景 2 · 诊断一只基金（2 请求：E2+限额页；当日缓存命中 0 请求）

```python
snap = fund_data.fund_snapshot("519770")        # 支持代码/名称/拼音
m = fund_data.fund_metrics(snap)                # 零网络，区间可传 start/end
print(snap["name"], "净值记录:", len(snap["nav_history"]))
print(f"成立以来: 年化 {m['annualized_return_pct']}%  最大回撤 {m['max_drawdown_pct']}%")
print(f"夏普 {m['sharpe']}  卡玛 {m['calmar']}  年化波动 {m['annualized_volatility_pct']}%")
pl = snap["purchase_limit"]                     # V0.7.7 申购限额（QDII 限额监控刚需）
if pl:                                          # None=降级快照或限额页拉取失败（见 missing_datasets）
    print(f"状态: {pl['status_text']}  kind={pl['kind']}  限额 {pl['limit_cny']} 元")
```

输出形态：快照 20+ 数据集（净值全史/规模史/持有人/经理/重仓股债/仓位史/
基准三曲线/申赎/申购限额…，缺失项列入 `missing_datasets` 绝不静默空）；
指标 `{total_return_pct, annualized_return_pct, max_drawdown_pct,
max_drawdown_window, annualized_volatility_pct, sharpe, calmar, …}`；
`purchase_limit = {status_text 原文, kind 四态, limit_cny 折算金额,
currency, limit_raw, not_for_sale 不代销}`——单查走 `fund_purchase_limit(码)`。
坑提醒：**夏普键名就是 `sharpe`**（无 `_ratio` 后缀）；无风险利率默认
1.5%/年，改传 `fund_metrics(snap, risk_free_annual=0.02)`；
区间年化 `fund_metrics(snap, start="2024-01-01")`。

## 场景 3 · 查净值（单只区间 / 批量最新）

```python
# A. 单只区间净值（E3 逐页；⚠️ 返回新→旧序）
rows = fund_data.fund_nav_history("519770", start="2026-01-01")
for h in rows[:3]:                              # 原生新→旧：前 3 条即最新
    print(h["date"], h["nav"], h["daily_return_pct"])
# ⚠️ 喂给指标/画图前先反转成旧→新：rows[::-1]

# B. 批量最新净值（N 只 = 1 请求，≤50 只；输入自动去重）
for r in fund_data.fund_realtime_nav(["519770", "161725", "007300"]):
    print(r["code"], r["name"], r["nav"], r["nav_date"])
```

输出形态：A 行 `{date, nav, daily_return_pct, …}`（新→旧）；
B 行 `{code, name, nav, nav_date}`。
坑提醒：E2 快照已含**全史净值**（场景 2 拿到的 `nav_history` 是旧→新升序）——
优先用快照，E3 只作区间核对/申赎状态补充。

## 场景 4 · 查费率全表（1 请求，当日缓存）

```python
f = fund_data.fund_fees("519770")
print(f["name"], f"管理 {f['management_fee_pct']}% 托管 {f['custody_fee_pct']}%")
for t in f["redemption_tiers"]:
    print(t["holding"], t["fee_pct"], "%")
```

输出形态：`{management_fee_pct, custody_fee_pct, sales_service_fee_pct,
founding_tiers, purchase_tiers(origin_pct/discount_pct), redemption_tiers}`。
坑提醒：快照里的 `fees.current_rate` 是**申购费**不是管理费——管理/托管/赎回
阶梯以本函数为准。

## 场景 5 · 全市场排行与初筛（全量 1 请求，过滤排序全本地）

```python
rows = fund_data.fund_rank()                    # 全量 ~2.5 万只，ytd 降序
hits = [r for r in rows
        if r["fund_type"] == "混合型-灵活"
        and (r["ret_3y_pct"] or -99) > 100      # 近3年翻倍
        and (r["ret_ytd_pct"] or -99) > 20]     # 今年>20%
for r in hits[:10]:
    print(r["code"], r["name"], r["ret_3y_pct"], r["ret_ytd_pct"])
```

输出形态：行 `{code, name, nav, daily_pct, ret_1w/1m/3m/6m/1y/2y/3y/ytd/since_pct,
founded, fee_pct, fund_type}`；`fund_type` 传 `zs/gp/hh/zq/qdii/fof`，
`sort` 传 `ytd/1m/3m/6m/1y/2y/3y/since`，`top=1~100` 取头部。
坑提醒：**绝不要**拿排行结果循环 `fund_snapshot`——那是封禁路径；
初筛收敛到 ≤3 只再逐只诊断。

## 场景 6 · 定投回测（2 请求 + 零网络计算）

```python
snap = fund_data.fund_snapshot("161725")
r = fund_data.fund_dca_backtest(snap, amount=1000, freq="monthly",
                                start="2021-01-01")
print(f"投入 {r['total_invested']} → 期末 {r['final_value']}（收益 {r['return_pct']}%）")
print(f"XIRR 真年化 {r['irr_xirr_pct']}%")      # 分期投入口径
```

输出形态：`{periods, total_invested, final_value, profit, return_pct,
naive_annualized_pct, irr_xirr_pct, xirr_note, short_window_note}`。
坑提醒：年化看 `irr_xirr_pct`（XIRR 分期真年化）；`naive_annualized_pct`
忽略资金分期投入，仅供参考。`irr_xirr_pct=None` 见 FAQ。

## 场景 7 · 这基金买了什么（持仓白话地图）

```python
p = fund_data.penetrate("161725", topn=10)      # 冷启 ≤13 请求，重跑当日缓存 1
print(fund_data.plain_text_map(p))              # "这只基金主要是白酒约86%…"
```

输出形态：`penetrate` 返回主题聚合结构；`plain_text_map` 直接输出白话文本。
坑提醒：单股行情失败会跳过并标注，不中断；A 股重仓股专用（港股码跳过）。

## 场景 8 · 债市利率（中债曲线 / 信用利差 / 资金面 / 盘中）

```python
# 中债国债 10Y 时序（年文件 1 请求/曲线/年，当日缓存）
for p in fund_data.cb_curve_series("treasury", "10y")[-3:]:
    print(p["date"], p["yield_pct"])

# 信用利差 = 中债企业债 − 国债（同日期同期限，2 请求/年）
for p in fund_data.credit_spread("AA", "3y")[-3:]:
    print(p["date"], p["spread_pct"])

# 中美对照日频 / SHIBOR / GC007 / 国债盘中
print(fund_data.treasury_yield_daily(5))        # {date, cn_2y..30y, us_*, cn_10y_2y}
print(fund_data.shibor_history("3月(3M)", 5))   # {date, period, rate_pct}
print(fund_data.repo_rate_gc())                 # GC007（DR007 交易所代理）
print(fund_data.cn_treasury_realtime())         # {2y/5y/10y/30y: {yield_pct}, as_of}

# 交易所债券实时快照（V0.7.5 新浪备胎：腾讯行情挂时的互备；须 sh/sz 前缀）
q = fund_data.sina_quote(["sh118078", "sz127120"])   # 返回键为 6 位裸码（如 "118078"）：
                                                      # {name, last, prev_close, bid1, date, time}
```

输出形态：曲线点 `{date, term, term_years, yield_pct}`；全曲线族走
`cb_curve_tree()`（30 天缓存）；常用键直通：`treasury / enterprise_aaa /
enterprise_aa / ncd_aaa ~ ncd_a`。
坑提醒：中债通道依赖**系统 curl**（TLS 指纹墙 P11）；曲线名传种子键或曲线树
全名，模糊多命中会报错不猜；LPR 与 DR007 本尊无公开接口（FAQ）。

## 场景 9 · 基金经理画像（基金码转接，1 请求，24h 缓存）

```python
mgr = fund_data.fund_manager("161725")          # 也支持 8 位经理 ID 直查
print(mgr["name"], f"现任 {mgr['fund_count']} 只 / 总规模 {mgr['scale_yi']}亿")
for f in mgr["funds"]:
    print(f["code"], f["name"])
```

输出形态：`{manager_id, name, tenure, scale_yi, return_since_pct,
funds 在管清单, fund_count}`。
坑提醒：**不支持经理姓名查询**（无公开索引，P23）——用基金码转接或经理 ID；
80 开头 8 位是公司 ID，走 `fund_company`。

## 场景 10 · 规模全历史（1 请求，当日缓存）

```python
for r in fund_data.fund_scale_history("519770")[-4:]:   # 升序，取最近 4 期
    print(r["date"], f"净资产 {r['nav_yi']}亿  申购 {r['subscribe_yi']}亿份 / 赎回 {r['redeem_yi']}亿份")
```

输出形态：行 `{date, subscribe_yi, redeem_yi, shares_yi, nav_yi,
nav_change_pct}`（**升序**；份额字段单位亿份、净资产亿元），含申赎流量可判断净申购/净赎回趋势。

## 场景 11 · QDII/ETF 场内溢价（N 只 = 2 请求）

```python
for r in fund_data.qdii_premium(["513100", "513500"]):
    print(r["code"], r["name"], f"溢价 {r['premium_pct']}%（净值日 {r['nav_date']}）")
```

输出形态：行 `{code, name, price, nav, nav_date, premium_pct, note}`，按溢价降序。
坑提醒：净值是 T-1/T-2 口径，行情剧烈时段溢价会被高估——连同 `nav_date` 一起解读。

## 场景 12 · 指数/股票日线（1 请求，当日缓存）

```python
k = fund_data.kline_daily("沪深300")            # 指数名/行业板块名/股票代码；也可 "上证指数" "半导体" "600519"
print(k[0]["date"], "→", k[-1]["date"], f"共 {len(k)} 根")
```

输出形态：`[{date, close, source?}]` 升序；默认 `lmt=2600`（约十年）。
坑提醒：指数名查表 119 个（宽基/风格/东财行业板块 100，板块为东财口径非申万）；
行业板块指数无腾讯备胎（EM 单源）；跟踪指数名/股票码都可，前缀形态 `sh000016`
会保市场信息防错路由；缓存键含 `lmt`——窗口拉大不会吃到旧短缓存。

## 场景 13 · 基金定期报告清单与 PDF 下载（清单 1 请求 + PDF 1~2 请求/份）

```python
rp = fund_data.fund_reports("交银趋势")            # 默认 periodic：季度/中期/年报
latest = rp["reports"][0]
print(f"{latest['kind']} {latest['year']}  {latest['date']}")   # Q3 2026  2026-10-27
annuals = [r for r in rp["reports"] if r["kind"] == "annual"]   # 只看年报（本地过滤）

path = fund_data.fund_report_pdf(latest)           # 下载最新季报 PDF 到当前目录
print(path)                                        # 519702_2026-10-27_Q3_986993.pdf
```

输出形态：清单 `{code, category, total, reports:[{code, report_id, title,
short_title, date, year, kind, pdf_url}]}`（新→旧；kind=Q1~Q4/interim/annual/
other）；`fund_report_pdf` 返回本地路径 str（文件名 {code}_{date}_{kind}_{ID尾6}.pdf
防年报/摘要同日撞名；已存在同名文件 0 请求直返）。
坑提醒：category 七类目 periodic/all/issuing/dividend/staff/sales/other（=公告
页 tab）；PDF 通道有腾讯 EdgeOne 反爬——**勿裸 requests 直链**，必须走
`fund_report_pdf`（需系统 node，同巨潮 enckey 依赖）；场外基金全覆盖
（巨潮仅上市基金，P32）。

---

# FAQ · 按现象找解法（每条 ≤5 行，深链 reference.md 坑号）

**Q1 抛 `RiskControlError` / 提示熔断？**
被风控了。**等 10 分钟勿重试**——冷却期零网络是设计行为（连击≥3 开闸），
期间任何请求直接抛 `CircuitOpenError`。→ reference.md F2/P14

**Q2 HTTP 403/429？**
同 Q1，属显式风控信号，上抛+熔断 10 分钟，不触发降级。检查是否有代码绕过
`ttfund_get()` 直连东财域。→ F6

**Q3 `fund_rank` 报「无访问权限」但状态码 200？**
rankhandler 风控形态就是 200+无访问权限（无 403），已被内容级断言拦截并计入
熔断。等冷却即可。→ P19

**Q4 净值和 App 对不上？**
口径差：E6 实时净值多为 T-1（QDII 可达 T-2）；当日净值晚间才更新。
另外本模块给的是单位净值——累计口径看 `ac_nav_history`。→ P16

**Q5 指标方向全反 / 收益算成负的？**
E3 `fund_nav_history` 原生**新→旧**序，喂指标前必须反转；E2 快照的
`nav_history` 已是旧→新，优先用它。→ P7

**Q6 中债曲线拉不到 / 报 curl 缺失？**
中债站有 TLS 指纹墙：Python requests 稳定 SSL EOF，通道走**系统 curl**。
macOS/Linux 自带 curl；无 curl 环境该族函数显式报错。→ P11

**Q7 曲线 ID 报「模糊命中 N 条不猜」？**
曲线参数用种子键（`treasury`/`enterprise_aaa`/`ncd_aaa`…）或 `cb_curve_tree()`
里的完整中文名；本模块不猜。→ cb_curve_tree docstring

**Q8 巨潮（抱团度/行业配置）报 451 或 token null？**
enckey 时效问题，**非风控**——已内置自动刷新重试一次；仍拒=本机时钟漂移，
校时即可。缺 node 会显式报错（enckey 计算需要系统 node）。→ P20

**Q9 6 位代码查基金报「不在清单」？**
可能是股票代码（基金清单 2.8 万只没有它）——个股数据走 a-stock-data skill。→ P4

**Q10 `qdii_premium` 报「单次 ≤50 只」？**
qdii_premium 单次上限 50 只（N 只=2 请求），分批调用再自行合并；E6
`fund_realtime_nav` 超 50 只**自动分块**无需干预，输入自动去重。→ 两函数 docstring

**Q11 数据多久更新一次？缓存怎么强制刷新？**
TTL：fund_list 30 天；快照/持仓/宏观/排行/新发/规模/K线/费率 当日（滚动 20h）；
公司/经理画像 24h。强制刷新传 `force_refresh=True`（快照族共用该参数）。
过期=干净重拉，断档期数据自动补齐（全量端点）。

**Q12 快照 `source=degraded`？**
E2 主端点挂了，自动降级 E3+蛋卷（净值窗口默认近 1 年），**不写缓存**、下次
自动重试主端点。风控页/结构异常不会降级而是显式上抛。→ F3

**Q13 定投 `irr_xirr_pct=None`？**
XIRR 无解的诚实返回：期末市值≈0（全亏）、窗口过短、或真实年化超求解域
（>1000%）。看 `xirr_note` 说明，勿把 `naive_annualized_pct` 当真年化。→ A1

**Q14 国债盘中 `as_of=中债最新收盘(降级)`？**
push2 通道连接级熔断触发自动降级（盘中口径拿不到，给收盘价兜底且不写缓存）。
稍后重试或直接用 `cb_yield_curve` 日频。→ P26

**Q15 LPR / DR007 报价拉不到？**
诚实边界：东财无 LPR 报表（实测 9501）；DR007 本尊在中国货币网（网络不可达），
`repo_rate_gc()` 给的是 GC007 交易所口径代理。→ L7 边界

**Q16 为什么名称查询不直接给我一个代码？**
多候选（如 A/C 份额、同名不同公司）时列全部候选让你选——**绝不猜**，
猜错的代价比多看一眼高。用 `matches` 里的 type 字段辅助判断。

**Q18 报告 PDF 下载报 node 缺失 / EdgeOne 挑战失败？**
pdf.dfcfw.com 前有腾讯 EdgeOne 反爬（200+伪装成 pdf 的 988B 脚本挑战），
本模块用系统 node vm 沙箱自动解挑战重放（同巨潮 enckey 的 node 依赖）——
装 node 即可；「挑战 cookie 不全/仍非 PDF」= 上游改版预警。→ P32

**Q17 `purchase_limit` 的 limit_cny 是外币限额怎么折的？**
静态近似汇率（美元 7.2/港币 0.92/欧元 7.8/英镑 9.2/日元 0.05），仅供
跨币种比较排序——精确额度看 `limit_raw` 原文；`kind=limited` 但
`limit_cny=None` 是「限大额但金额形态未识别」（上游改版预警），不是无限额。
→ P31
