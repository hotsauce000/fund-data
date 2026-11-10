---
name: fund-data
description: 当任务需要写代码实际获取基金领域底层数据时使用——拉取基金全量清单(~2.8万只)、净值全史(单位/累计/复权/分红事件)、单基金一站式快照(pingzhongdata 436KB→20+数据集:规模变动/持有人结构/现任经理/重仓股+债/资产配置/仓位史/同类百分位史/基准三曲线(本基金-同类平均-沪深300)/申赎状态/费率/货基标志)、季度前十大持仓、区间净值+每日申赎状态、批量实时净值(N只1请求)、蛋卷跨域备胎，零网络的本地计算:复权净值(分红再投)/区间收益/最大回撤/波动/夏普/卡玛/管理费率标注/定投回测(XIRR分期真年化+朴素对照)/仓位史解读(加减仓信号)/同类百分位解读(一年前对照)/相对表现(vs沪深300或同类平均:相对收益+β+相关性)，以及L6跨域穿透:重仓股批量实时行情(腾讯N只1请求)+个股板块归属(东财slist当日缓存)+共现加权主题聚合,一键输出'这只基金主要是白酒约86%'式白话地图；L7宏观利率层:中债收益率曲线族40+条(国债/企业债AAA~AA-/同业存单全评级/城投,1请求=1曲线1年日频)、信用利差本地算差、国债收益率日频(中美)、SHIBOR多期限(单页500行上限)、存贷款基准利率调整史、GC007回购代理、国债盘中实时。请求预算制防封:单基金诊断≤2请求、当日磁盘缓存命中0请求、全市场筛选只走列表端点。仅在需要调用数据接口取数时使用：基金概念解释、投资观点讨论等无需取数的话题不要加载本skill。
origin: custom
version: 0.4.2
---

# fund-data 路由层 V0.4.2

本文件是**路由层**（瘦入口，≈3K token）——skill 激活只加载本文件。实现细节/完整坑区/内嵌代码在同目录 **reference.md（按需读取，勿预加载）**；运行时代码在同目录 `fund_data.py`（**import 执行用，不要通读**）：

```python
import sys
sys.path.insert(0, str(__import__("pathlib").Path.home() / ".agents" / "skills" / "fund-data"))
from fund_data import fund_snapshot, fund_metrics   # 按场景路由表选函数
```

## 防封铁律（先读，违反=IP 封禁 20+ 小时风险）

东财风控阈值：>5 次/秒、并发 ≥10、5 分钟 ≥300 次。五条铁律：

1. **请求预算**：单基金深度诊断 ≤2 请求（1×pingzhongdata 当日缓存 + 0~1×lsjz）；当日缓存命中 0 请求
2. **明令禁止** `for fund in all: fund_snapshot(fund)`（全量循环=封禁路径）
3. **聚合优先**：先问 pingzhongdata（1 请求=90% 静态数据），再考虑窄端点
4. **东财/天天/蛋卷请求必须走 `ttfund_get()`**（跨进程锁 F1+熔断 F2+风控分流 F6+直连 P12 已内建）；批量 ≥10 只把 `TT_MIN_INTERVAL` 调 2~3s
5. 缓存 TTL：fund_list 30 天 / snapshot 与持仓与宏观 20h 当日 / lsjz 不缓存

## 场景路由（我想做 X → 调什么）

| 场景 | 调用链 | 请求 |
|---|---|---|
| 诊断一只基金 | `fund_snapshot` → `fund_metrics` + `position_trend`/`peer_rank`/`relative_performance` | 1 |
| 定投测算 | `fund_snapshot` → `fund_dca_backtest`（XIRR 真年化） | 1 |
| 这基金买了什么 | `penetrate` → `plain_text_map`（白话地图） | 冷启≤13/重跑1 |
| 名称/拼音找代码 | `norm_fund_code`（多候选列出不猜）/ `resolve_fund_code`（强报错） | 0 |
| 净值核对/每日申赎状态 | `fund_nav_history`（新→旧序，消费前反转） | 1~N 页 |
| 批量最新净值 | `fund_realtime_nav`（≤50/请求） | N/50 |
| 债基利率敏感度 | `cb_yield_curve` + `credit_spread` / `treasury_yield_daily` | 1~2 |
| 重仓股深度（龙虎榜/估值史） | `holding_map` → **a-stock-data skill** | 0 |

## 失效与备源路由（失败时怎么办）

| 故障 | 行为 | 硬边界 |
|---|---|---|
| E2 快照失败 | **自动降级** E3+E4a+蛋卷，`source="degraded"` 标注 | 降级窗口默认 1 年 |
| E2 返回风控页/空体 | 自动识别走降级，不污染缓存 | — |
| 历史净值需要兜底 | E3 lsjz 逐页（TotalCount 强校验） | **跨域无备胎（P17，三候选实测全死）**——别试蛋卷 growth/新浪 |
| 个股行情 | 腾讯通道（不封 IP） | — |
| 利率曲线 | 中债 curl 通道（TLS 指纹墙 P11） | 东财 datacenter 互证 |
| 403/429 | `RiskControlError` 上抛+熔断 10min（**不触发降级风暴**） | 冷却期零网络，等勿重试 |

## 函数速查（32 个公开函数，构建时自动生成，勿手改）

| 函数 | 说明 |
|---|---|
| `assert_not_risk_control(resp, line='tt', context='')` | F6 防线：调用方 raise_for_status **之前**先过这一道——403/429 转 RiskControlError |
| `ttfund_get(url, params=None, headers=None, timeout=20)` | 天天/东财/蛋卷统一请求入口：跨进程串行限流(F1) + 熔断(F2) + 强制 HTTPS（坑P1） |
| `fund_list(force_refresh=False)` | 全量基金清单 ~2万只：[{code, pinyin, name, type}, ...] |
| `norm_fund_code(query)` | 基金代码/名称/拼音 → 归一化结果 |
| `resolve_fund_code(query)` | norm_fund_code 的便捷包装：唯一命中返回代码，多候选/未命中直接抛错 |
| `fund_snapshot(query, force_refresh=False)` | 单基金一站式快照（E2 pingzhongdata，436KB → 20+ 数据集） |
| `fund_nav_history(query, start='', end='', max_pages=80)` | 历史净值逐页（E3 lsjz）。定位为补充端点——E2 已含净值全史， |
| `fund_holdings(query, year=None)` | 季度股票持仓前十大（E4a FundArchivesDatas type=jjcc） |
| `fund_detail_danjuan(query)` | 蛋卷基金详情（D1 djapi/fund/{code}）：总份额/经理/托管/风险等级/申赎状态 |
| `fund_realtime_nav(codes)` | 批量最新净值（E6 fundmobapi FCodes 逗号分隔）。轻量探测（1KB vs E2 的 436KB）， |
| `adjusted_nav(nav_records)` | 净值序列 → 复权净值（分红再投口径，零网络） |
| `fund_metrics(snapshot, start='', end='', risk_free_annual=0.015)` | 区间收益/年化/最大回撤/年化波动/夏普/卡玛（复权口径，零网络） |
| `fund_dca_backtest(snapshot, amount=1000.0, freq='monthly', start='', end='')` | 定投回测（真实复权净值口径，零网络） |
| `position_trend(snapshot, recent=4)` | 股票仓位史规则解读（零网络，V0.4.0 B2）。输入 snapshot.stock_position_history |
| `peer_rank(snapshot, lookback_days=365)` | 同类百分位解读（零网络，V0.4.0 B2）。输入 snapshot.peer_rank_history（日频） |
| `relative_performance(snapshot, benchmark='沪深300', min_overlap=60)` | 基金 vs E2 内置基准曲线的区间相对表现（零网络，V0.4.0 B3） |
| `holding_map(snapshot, topn=10)` | 持仓穿透桥（零网络）：把重仓股代码整理成可直接喂 a-stock-data 的形态 |
| `em_get(url, params=None, headers=None, timeout=15)` | 股票域东财统一入口（push2 系）：跨进程串行限流(F1) + 熔断(F2) + 会话复用 |
| `tencent_quote(codes)` | 腾讯批量实时行情（通道内嵌自 a-stock-data §1.2）：价/涨跌/换手/PE/PB/市值 |
| `em_market_code(code)` | 东财 secid 市场号：沪=1，深/北=0（规则照抄 a-stock-data，勿用 startswith('6')） |
| `concept_blocks(stock_code, use_cache=True)` | 个股板块标签（通道内嵌自 a-stock-data §3.3 东财 slist，一次请求拿全） |
| `penetrate(query, topn=10)` | 重仓穿透主入口（R9 场景 04）：基金 → 重仓股权重 → 股票域行情+板块 → 白话地图 |
| `plain_text_map(p)` | 穿透结果 → 白话地图文本（"这只基金主要是白酒约86%"） |
| `cb_curve_tree(force_refresh=False)` | 中债曲线树全族 name→ycDefId（40+ 条：国债/国开/企业债AAA~AA-/城投/存单/ABS…） |
| `cb_yield_curve(curve='treasury', year=None, force_refresh=False)` | 中债收益率曲线·年文件（坑P8：只走 downYearBzqx GET，不碰 searchYc） |
| `cb_curve_series(curve, term='10y', year=None)` | 单期限时序：[{date, yield_pct}]（日期升序）。term 用中债期限码：'1y'/'3y'/'10y'… |
| `credit_spread(rating='AA', term='3y', year=None)` | 信用利差 = 中债企业债(rating) − 国债，同日期同期限对齐后本地算差（2 请求/年/日缓存） |
| `treasury_yield_daily(days=30)` | 国债收益率·日频（东财 RPTA_WEB_TREASURYYIELD，中债口径，**新→旧序**）：中美 2/5/10/30Y+利差 |
| `shibor_history(period=None, days=30)` | SHIBOR 历史（东财 RPT_IMP_INTRESTRATEN，恒定过滤「上海银行同业拆借市场」） |
| `benchmark_rate_history()` | 一年期存/贷款基准利率历次调整（东财 RPT_ECONOMY_DEPOSIT_RATE，2004 至今） |
| `repo_rate_gc()` | GC007 交易所质押式回购利率（腾讯通道，1 请求）——DR007 的代理指标 |
| `cn_treasury_realtime()` | 国债收益率实时盘中（东财 push2 全球债券行情：CN2Y/5Y/10Y/30Y，走 em_get 股票域线） |

## When to Activate

查基金净值/复权/分红/回撤/夏普/卡玛；基金诊断评测（收益·风险·同类排名·仓位·相对基准）；持仓穿透白话地图；定投回测；基金档案（经理/规模/持有人/费率/申赎）；批量净值；国债收益率/信用利差/SHIBOR。关键词：基金净值、复权、回撤、定投、重仓、持仓穿透、白话地图、持有人结构、信用利差、GC007、中债曲线。

## 深读与边界（按需）

- **实现细节/完整坑区 P1~P18/使用示例/版本记录 → 读同目录 `reference.md`**（只在调试、扩展、排查坑时读）
- 不覆盖：账户持仓/交易下单（ttskill）、盘中实时估值（官方已下线）、货基万份收益（P15 语义漂移勿当净值）、行业配置史（E2 已停供）、语义主题/事件选基（ttskill）
- 个股深度分析 → a-stock-data；`holding_map()` 是官方桥
