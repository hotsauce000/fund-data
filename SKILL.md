---
name: fund-data
slug: fund-data
displayName: 基金数据底座 FundBase
display_name: 基金数据底座 FundBase
display_name_en: FundBase · The Data Foundation for China Funds
description: 写代码实际获取基金领域底层数据时使用——零key零登录，八层能力：【主键与端点】2.8万只清单消歧(列出不猜)、单基金诊断快照(pingzhongdata 20+数据集:净值全史/规模史/持有人/经理/重仓股债/仓位史/基准三曲线/申赎/费率/申购限额)、区间净值+申赎状态、批量实时净值、季度前十大、蛋卷备胎，快照挂自动降级；【本地计算】复权/回撤/波动/夏普/卡玛/费率标注、定投XIRR、仓位解读(加减仓)、同类百分位(一年前对照)、相对表现(vs沪深300/同类平均)；【持仓穿透】腾讯行情×板块归属→主题聚合→白酒约86%白话地图；【宏观利率】中债曲线族40+条、信用利差、国债/SHIBOR/基准利率史、GC007、国债盘中；【全市场】24792只排行底座(过滤排序全本地)、新发基金全量、公司画像、抱团度(巨潮)、19门类行业配置；【经理与专项】经理画像(在管清单/任期/回报)、排行、持续性矩阵、固收+画像、费率全表(管理/托管/赎回阶梯)、单基金规模(ENDNAV)、规模全历史(53期含申赎流量)、QDII溢价、重仓探测、定期报告清单/PDF下载(f10/JJGG+pdf直链,场外上市全覆盖)；【基准指数】指数/行业板块/股票日线(宽基+风格+东财行业板块共119指数,十年全史,1请求)。请求预算制防封(铁律见下)。仅在需要调用数据接口取数时使用：基金概念解释、投资观点讨论等无需取数的话题不要加载本skill。
description_zh: 基金域的全景数据引擎：一站式快照 20+ 数据集、白话持仓穿透地图、24792 只全市场排行、经理画像与抱团度、十年指数日线与 53 期规模全史——零 key 零登录，请求预算制防封，分层断言全绿。为一切基金应用打地基。
description_en: Full-spectrum data engine for China funds — one-shot snapshots (20+ datasets), holdings maps, 24,792-fund rankings, manager profiles, 10-year klines, 53-period scale history, report PDFs. Zero keys/login, budget-guarded, assertions green.
author: hotsauce000
origin: custom
version: 0.7.8
---

# fund-data 路由层 V0.7.8

本文件是**路由层**（≈4K token，激活只加载本文件）。坑区/内嵌代码→**reference.md**、菜谱/FAQ→**examples.md**（均按需读取）；`fund_data.py` import 用勿通读：

```python
import sys
sys.path.insert(0, str(__import__("pathlib").Path.home() / ".agents" / "skills" / "fund-data"))
from fund_data import fund_snapshot, fund_metrics   # 按场景路由表选函数
```

## 防封铁律（先读，违反=封禁20+小时）

东财风控阈值：>5 次/秒、并发 ≥10、5 分钟 ≥300 次。五条铁律：

1. **请求预算**：单基金深度诊断 ≤3 请求（1×pingzhongdata+0~1×lsjz+0~1×主页面限额，当日缓存）；缓存命中 0 请求
2. **明令禁止** `for fund in all: fund_snapshot(fund)`（全量循环=封禁路径）；全市场排行已内置全量单请求
3. **聚合优先**：先问 pingzhongdata（1 请求=90% 静态数据），再考虑窄端点
4. **东财/天天/蛋卷请求必须走 `ttfund_get()`**（F1锁+F2熔断+F6分流+P12直连）；批量≥10只把 TT_MIN_INTERVAL 调 2~3s；巨潮走 _cninfo_post（1.5s节流）
5. 缓存 TTL：fund_list 30天 / snapshot·限额·持仓·宏观·排行·新发·规模·规模全史·K线·报告清单 当日 / 公司·经理画像 24h / 抱团度·行业配置季末后120天内1h·其后90天 / lsjz 不缓存

## 场景路由（我想做 X → 调什么）

| 场景 | 调用链 | 请求 |
|---|---|---|
| 诊断一只基金 | `fund_snapshot`→`fund_metrics`+评测三件套 | 2（E2+限额页） |
| 申购限额/交易状态 | 快照 `purchase_limit` 数据集 / 单查 `fund_purchase_limit` | 1（缓存0） |
| 定期报告/PDF | `fund_reports`（7 类目）→ `fund_report_pdf`（EdgeOne 自动解） | 1（缓存0）·PDF 1~2 |
| 全市场排行/初筛 | `fund_rank()` 全量1请求，过滤排序本地 | 1（缓存0） |
| 定投测算 | `fund_snapshot`→`fund_dca_backtest`（XIRR） | 2（缓存0） |
| 这基金买了什么 | `penetrate` → `plain_text_map`（白话地图） | 冷启≤13/重跑1 |
| 名称/拼音找代码 | `norm_fund_code`（列候选不猜）/ `resolve_fund_code` | 0 |
| 净值核对/每日申赎状态 | `fund_nav_history`（新→旧需反转） | 1~N页 |
| 批量最新净值 | `fund_realtime_nav`（输入去重，≤50/请求） | N/50 |
| 新发/在发基金 | `fund_new_issues()`（全量） | ~7（缓存0） |
| 基金公司画像 | `fund_company(基金码或8位公司ID)` | 1（基金码+1） |
| 抱团度/行业配置 | `fund_crowding()` / `fund_industry_allocation()`（巨潮） | 季末后120天内1h/其后90天 |
| 业绩持续性/固收+画像 | `rank_persistence()` / `bond_plus_profile(q)` | 0 |
| 规模·全史/费率/溢价/重仓探测 | `fund_scale`·`fund_scale_history`·`fund_fees`·`qdii_premium`·`nav_divergence` | 各1·2·冷启≤12 |
| 经理画像/排行 | `fund_manager(基金码或经理ID)` · `manager_rank(pages)`（仅姓名序） | 0~1·50/页 |
| 指数日线 | `kline_daily("沪深300")`（指数名/板块名/股票码） | 1（缓存0） |
| 债基利率敏感度 | `cb_yield_curve` + `credit_spread` / `treasury_yield_daily` | 1~2 |
| 重仓股深度 | `holding_map` → **a-stock-data skill** | 0 |

## 失效与备源路由（失败时怎么办）

| 故障 | 行为 | 硬边界 |
|---|---|---|
| E2 网络失败/真正空体 | **自动降级** E3+E4a+蛋卷（source=degraded），不污染缓存 | 窗口默认 1 年；风控页/结构异常显式上抛，不降级 |
| 历史净值兜底 | E3 lsjz 逐页（TotalCount 强校验） | **跨域无备胎（P17 三候选全死）** |
| 个股行情 | 腾讯通道（不封 IP；挂→`sina_quote` 互备，sh/sz 前缀必带） | — |
| 利率曲线 | 中债 curl 通道（TLS 指纹墙 P11） | 东财 datacenter 互证 |
| rankhandler 被拦 | 200+「无访问权限」（**无 403**）→ `RiskControlError`+熔断（P19） | 查 Referer 头 |
| cninfo 451/token null | enckey 时效**非风控**，刷新重试一次（P20） | 仍拒=时钟漂移 |
| 403/429 | `RiskControlError` 上抛+熔断10min（不触发降级） | 冷却期零网络，等勿重试 |

## 函数速查（53 个，构建时自动生成勿手改）

| 函数 | 说明 |
|---|---|
| `assert_not_risk_control(resp, line='tt', context='')` | F6 防线：调用方 raise_for_status  |
| `ttfund_get(url, params=None, headers=None, timeout=20, **kwargs)` | 天天/东财/蛋卷统一请求入口：跨进程串行限流(F1) + |
| `fund_list(force_refresh=False)` | 全量基金清单 ~2.8万只：[{code, pinyin |
| `norm_fund_code(query)` | 基金代码/名称/拼音 → 归一化结果 |
| `resolve_fund_code(query)` | norm_fund_code 的便捷包装：唯一命中返回 |
| `parse_purchase_limit(status_text, fx=None)` | 交易状态文本 → 结构化限额（纯函数，零网络） |
| `fund_purchase_limit(query, force_refresh=False)` | 单基金申购限额/交易状态（主页面 {code}.html |
| `fund_snapshot(query, force_refresh=False)` | 单基金一站式快照（E2 pingzhongdata，4 |
| `fund_nav_history(query, start='', end='', max_pages=80)` | 历史净值逐页（E3 lsjz）。定位为补充端点——E2 |
| `fund_holdings(query, year=None, force_refresh=False)` | 季度股票持仓前十大（E4a FundArchivesD |
| `fund_detail_danjuan(query)` | 蛋卷基金详情（D1 djapi/fund/{code}） |
| `fund_realtime_nav(codes)` | 批量最新净值（E6 fundmobapi FCodes |
| `adjusted_nav(nav_records)` | 净值序列 → 复权净值（分红再投口径，零网络） |
| `fund_metrics(snapshot, start='', end='', risk_free_annual=0.015)` | 区间收益/年化/最大回撤/年化波动/夏普/卡玛（复权口径 |
| `fund_dca_backtest(snapshot, amount=1000.0, freq='monthly', start='', end='')` | 定投回测（真实复权净值口径，零网络） |
| `position_trend(snapshot, recent=4)` | 股票仓位史规则解读（零网络，V0.4.0 B2）。输入 |
| `peer_rank(snapshot, lookback_days=365)` | 同类百分位解读（零网络，V0.4.0 B2）。输入 s |
| `relative_performance(snapshot, benchmark='沪深300', min_overlap=60)` | 基金 vs E2 内置基准曲线的区间相对表现（零网络， |
| `holding_map(snapshot, topn=10)` | 持仓穿透桥（零网络）：把重仓股代码整理成可直接喂 a- |
| `em_get(url, params=None, headers=None, timeout=15, **kwargs)` | 股票域东财统一入口（push2 系）：跨进程串行限流( |
| `tencent_quote(codes)` | 腾讯批量实时行情（通道内嵌自 a-stock-data |
| `sina_quote(codes)` | 新浪批量实时行情（V0.7.5 备胎通道，定位=腾讯互 |
| `em_market_code(code)` | 东财 secid 市场号：沪=1，深/北=0（规则照抄 |
| `concept_blocks(stock_code, use_cache=True)` | 个股板块标签（通道内嵌自 a-stock-data § |
| `penetrate(query, topn=10)` | 重仓穿透主入口（R9 场景 04）：基金 → 重仓股权重 |
| `plain_text_map(p)` | 穿透结果 → 白话地图文本（"这只基金主要是白酒约86 |
| `cb_curve_tree(force_refresh=False)` | 中债曲线树全族 name→ycDefId（40+ 条： |
| `cb_yield_curve(curve='treasury', year=None, force_refresh=False)` | 中债收益率曲线·年文件（坑P8：只走 downYear |
| `cb_curve_series(curve, term='10y', year=None)` | 单期限时序：[{date, yield_pct}]（日 |
| `credit_spread(rating='AA', term='3y', year=None)` | 信用利差 = 中债企业债(rating) − 国债，同 |
| `treasury_yield_daily(days=30)` | 国债收益率·日频（东财 RPTA_WEB_TREASU |
| `shibor_history(period=None, days=30)` | SHIBOR 历史（东财 RPT_IMP_INTRES |
| `benchmark_rate_history()` | 一年期存/贷款基准利率历次调整（东财 RPT_ECON |
| `repo_rate_gc()` | GC007 交易所质押式回购利率（腾讯通道，1 请求） |
| `cn_treasury_realtime()` | 国债收益率盘中（push2 全球债券行情 CN2Y/5Y |
| `assert_rankhandler(resp, context='rankhandler')` | rankhandler 家族内容级风控断言；业务成功由 |
| `fund_rank(top=None, fund_type='all', sort='ytd', ascending=False, force_refresh=False)` | 全市场基金排行 / 初筛底座（坑P19；全量 = 1  |
| `fund_new_issues(force_refresh=False)` | 在发/新成立基金全量（FundNewIssue.aspx |
| `fund_company(query, force_refresh=False)` | 基金公司画像（company/{id}.html 服务 |
| `fund_crowding(rdate='', force_refresh=False)` | 季度基金重仓股聚合（cninfo 官方专题统计 p_s |
| `fund_industry_allocation(rdate='', force_refresh=False)` | 季度全市场基金行业配置聚合（cninfo p_sysa |
| `rank_persistence(top=20, fund_type='all', force_refresh=False)` | 业绩持续性矩阵（零请求）：排行缓存上算各周期同类分位与 |
| `fund_scale(query, force_refresh=False)` | 单基金最新规模（1 请求）：移动端 ENDNAV 精确值 |
| `bond_plus_profile(query, force_refresh=False)` | 固收+画像（零新请求）：权益仓位带 + 回撤控制带 + |
| `qdii_premium(codes)` | QDII/ETF 场内溢价（N 只=2 请求）：腾讯市价 |
| `nav_divergence(query, force_refresh=False)` | 重仓探测：净值收益 vs 上季重仓加权收益的背离度（疑 |
| `fund_manager(query, force_refresh=False)` | 基金经理画像（1 请求，24h 缓存）：ID 直查或基 |
| `manager_rank(pages=1, force_refresh=False)` | 经理排行（坑P23：服务端仅姓名序，50 条/页，re |
| `kline_daily(query, lmt=2600, use_tencent_fallback=True, force_refresh=False)` | 指数/股票日线收盘序列 [{date, close,  |
| `fund_scale_history(query, force_refresh=False)` | 基金规模全历史（F10 规模变动 gmbd 通道，1  |
| `fund_fees(query, force_refresh=False)` | 基金费率全表（F10 购买信息 jjfl 页，1 请求 |
| `fund_reports(query, category='periodic', limit=20, force_refresh=False)` | 基金公告/定期报告清单（f10/JJGG，1 请求，当 |
| `fund_report_pdf(report, dest_dir=None, force_refresh=False)` | 下载单份基金报告 PDF（1~2 请求；本地同名文件已 |

## When to Activate

基金净值/复权/回撤/夏普、诊断评测、穿透地图、定投回测、档案、批量净值、利率。关键词：基金净值、复权、回撤、定投、持仓穿透、白话地图、利差、全市场排行、抱团度、公司画像、新发基金、经理画像、持续性、固收+、溢价、重仓探测、指数日线、规模全史。

## 深读与边界（按需）

- 复制即用示例（12 场景菜谱）+ 按现象查 FAQ → 同目录 `examples.md`（写调用代码/排错前读）
- 实现细节/坑区 P1~P31/示例/版本记录 → 同目录 `reference.md`（调试/扩展/排坑时读）
- 不覆盖：账户持仓/交易下单（ttskill）、盘中实时估值、货基万份收益、单基金行业史（E2停供）、语义选基、FOF底层明细与经理姓名索引（P23/P24）
- 个股深度 → a-stock-data；holding_map() 是官方桥
