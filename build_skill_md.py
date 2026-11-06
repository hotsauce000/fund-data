# -*- coding: utf-8 -*-
"""从实测通过的 fund_data.py 自动组装 SKILL.md（内嵌代码逐字节一致，不手抄）。"""
import re
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "fund_data.py").read_text(encoding="utf-8")
SKILL_DIR = Path.home() / ".agents" / "skills" / "fund-data"
SKILL_DIR.mkdir(parents=True, exist_ok=True)

# ── 按「═ / 标题 / ═」三行标记对切块，映射到 SKILL.md 章节 ──
pat = re.compile(r"# ═══+\n# (.+?)\n# ═══+\n")
marks = list(pat.finditer(SRC))
sections = {}
for i, m in enumerate(marks):
    body = SRC[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(SRC)]
    sections[m.group(1).strip()] = body
header_code = SRC[:marks[0].start()]                     # 文件头(docstring+imports+常量)

def code(body: str) -> str:
    return "```python\n" + body.rstrip() + "\n```\n"

S = []
S.append("""---
name: fund-data
description: 当任务需要写代码实际获取基金领域底层数据时使用——拉取基金全量清单(~2.8万只)、净值全史(单位/累计/复权/分红事件)、单基金一站式快照(pingzhongdata 436KB→20+数据集:规模变动/持有人结构/现任经理/重仓股+债/资产配置/同类排名/申赎状态/费率/货基标志)、季度前十大持仓、区间净值+每日申赎状态、批量实时净值(N只1请求)、蛋卷跨域备胎，零网络的本地计算:复权净值(分红再投)/区间收益/最大回撤/波动/夏普/卡玛/定投回测，以及L6跨域穿透:重仓股批量实时行情(腾讯N只1请求)+个股板块归属(东财slist当日缓存)+共现加权主题聚合,一键输出'这只基金主要是白酒约86%'式白话地图；L7宏观利率层:中债收益率曲线族40+条(国债/企业债AAA~AA-/同业存单全评级/城投,1请求=1曲线1年日频)、信用利差本地算差、国债收益率日频(中美)、SHIBOR全史、存贷款基准利率调整史、GC007回购代理、国债盘中实时。请求预算制防封:单基金诊断≤2请求、当日磁盘缓存命中0请求、全市场筛选只走列表端点。仅在需要调用数据接口取数时使用：基金概念解释、投资观点讨论等无需取数的话题不要加载本skill。
origin: custom
version: 0.3.1
---

# fund-data 基金底层数据工具包 V0.3.1

天天基金主源 + 蛋卷跨域备胎 + 本地计算层 + L6 跨域穿透 + L7 宏观利率层。**V0.3.1（2026-11-06）：70/70 断言实测通过**（含防封专项 F1/F2/F4/F6/F7 五条新断言）（L1单元34 · L2端点smoke 17 · L3三源交叉验证5 · L4请求预算审计2 · L5降级演练7），覆盖 11 个上游接口。同目录 `fund_data.py`（可直接 import 的同款模块）与 `test_fund_data.py`（回归测试）与本文件内嵌代码逐字节一致。


**V0.3.1 防封加固（两轮审计 F1~F7 修复）：**
- 🔴 **F1 跨进程节流锁**：限流时间戳从进程内存改磁盘文件+flock——licai-map 服务/AI会话/手跑脚本多进程并发时合并速率不再越线（原实现双进程批量即可达封禁阈值的 2/3）
- 🔴 **F6 风控信号与网络故障分流**：`assert_not_risk_control()` 防线接入全部 8 个东财调用点——403/429 抛 `RiskControlError`（非 RequestException 子类），**绝不触发降级风暴**（原实现 403 被误当网络故障→自动降级→37页连发打风控枪口）
- 🔴 **F2 熔断器**：连续 3 次风控信号→落盘熔断 10 分钟→冷却期内所有请求直接拒绝（零网络）；成功响应清零连击（孤立 403 不误熔断）；tt/em 两线独立熔断
- 🟡 F3 降级窗口 3年→1年（13页，原 37 页连发）；F4 fund_holdings 当日缓存；F7 L7 宏观层全套当日缓存（原每次现查）；G2 缓存原子写 tmp+rename
- 测试 65→70：双进程合并间隔/403不触降级/冷却期拒绝且零网络/宏观与持仓二调0请求

**V0.3.0 新增：L7 宏观利率层（债基/理财叙事的数据地基）**
- 中债官网曲线族（`cb_yield_curve`，40+ 条曲线：国债/企业债AAA~AA-/存单全评级/城投…，1请求=1曲线1年日频全期限，当日缓存）+ `credit_spread` 本地算差 + `cb_curve_tree` 全族 ID
- 东财利率报表（坑P10 报表名全部从页面 bundle 逆向实测）：`treasury_yield_daily`（中美国债日频+期限利差）/ `shibor_history`（全期限全史）/ `benchmark_rate_history`（2004起基准利率调整+次日沪深涨跌）
- `repo_rate_gc`（GC007 腾讯通道，DR007 交易所口径代理）+ `cn_treasury_realtime`（push2 国债 2/5/10/30Y 盘中）
- 坑P8~P11 入册：中债 searchYc 参数黑洞/inlineStr xlsx/EM 报表名逆向/**中债 TLS 指纹墙（requests 稳挂 curl 稳过，通道=subprocess curl）**
- 实测三源互证：中债 10Y 1.6038 ↔ 东财 1.6038（同口径分毫不差）↔ push2 盘中 1.5986

**V0.2.0 新增：L6 跨域穿透层（R9 场景 04 就位）**
- `penetrate(query)` 一键穿透：重仓股权重 × 腾讯批量行情（N只1请求不封IP）× 东财板块归属（当日缓存）→ 共现加权主题聚合 → `plain_text_map()` 白话输出
- 防封：push2 走独立限流线 `em_get`（1.0s，与 ttfund_get 分管不同子域，合计峰值 ≈1.7 req/s）
- 穿透纪律：单股失败跳过不中断（failed_stocks 名单+权重标注）；每股标签先去重再累计（Ⅱ/Ⅲ 归并，否则权重翻倍）；92 北交所号段先于 9x 判断（错路由=静默空）
- 复审遗留 P2 修复：failed_stocks 权重口径统一、em_market_code 死分支清除

**V0.1.2 修复记录（code-review 独立审查驱动，2×P0 + 5×P1）：**
- 🔴 **P0 降级快照净值序反转**：E3 原生新→旧、E2 旧→新——降级序列直接喂指标会收益方向全反（审查实测复现 +100% 基金算出 -50%）。已反转 + 指标/定投入口加升序断言双防线
- 🔴 **P0 垃圾响应写缓存**：HTTP 200 + ≥1KB 风控页会产出全空快照并污染缓存 20 小时。已加关键变量强校验（fS_name/netWorthTrend），异常即走降级
- 🔴 **unitMoney 金额被上游剥离**（"分红：每10份派现金元"无金额）且拆分事件混入——复权引擎改为**链接官方 equityReturn**（已处理分红/拆分），unitMoney 解析仅作回退；对照官方 syl_1n 误差 0.4pct
- 🟡 空查询/降序输入/start>end/空区间均显式报错；时间戳按北京时区解析；E6 批量缺只报错；jjcc 表头文案校验防列序漂移；定投年化改名 naive_annualized_pct（原算法忽略分期投入）

**V0.1.1 修复记录（实测驱动，3 个文档外格式坑）：**
- 🔴 `stockCodesNew` 是 `["1.600519",...]` **secid 数组**而非逗号串——不剥市场前缀，喂给 a-stock-data 或与 F10 持仓对比会交集为 0（静默错，不是报错）
- 🔴 `var syl_1y="9.95";/*注释*/var ...` 值后挂**块注释**——按相邻 var 位置切片+剥注释，否则字符串值吞掉后文一整段
- 🔴 lsjz 的 `TotalCount` 在响应**顶层**不在 Data 内，读错位置=恒0=分页循环第一页就断，只回 20 条还以为拉全了
""")

S.append("""## 第一章 防封铁律（请求预算契约，先读）

本 skill 的设计约束不是"接口能不能通"，而是"请求额度是稀缺资源"。天天基金/东财系与 a-stock-data 的东财同一风控面（社区实测：>5次/秒、并发≥10、1小时4.5万请求 → IP 级封禁 20+ 小时）。

**五条铁律：**

1. **请求预算**：单基金深度诊断 ≤2 请求（1×pingzhongdata 当日缓存 + 0~1×lsjz）；全市场筛选只允许列表端点（rankhandler，1请求=50只全字段）
2. **明令禁止** `for fund in all_funds: fund_snapshot(fund)` —— 这是 a-stock-data #36 案例 20 小时封禁的复刻路径
3. **聚合优先**：任何数据先问"pingzhongdata 里有没有"（一次 436KB 覆盖 90% 静态数据），再考虑 F10 窄端点
4. **所有东财/天天/蛋卷请求必须走 `ttfund_get()`**：强制 HTTPS + 跨进程串行限流（F1）+ 熔断（F2）+ 会话复用 + 瞬态重试1次 + 请求计数；调用方 `raise_for_status()` 前必过 `assert_not_risk_control()`（F6）
5. **批量场景降速**：诊断 ≥10 只基金时把 `TT_MIN_INTERVAL` 调到 2~3s——1.5s 下 50 只 ≈ 10 分钟贴着 300req/5min 线的 67% 持续跑，批量就该再保守一档；多进程并发已被 F1 文件锁强制串行，无需额外处理

**缓存分层（同基金当日反复诊断 = 齐网络）：**

| 数据 | TTL | 说明 |
|---|---|---|
| fund_list（2.8万只主键表） | 30天 | miss 单点回源在线查一只，不为1只重下2MB |
| fund_snapshot | 当日20h | pingzhongdata 每日更新一次 |
| lsjz | 不缓存 | 补充端点，按区间现查 |
""")

S.append("""## 端点路由速查（按需定位，不必通读）

| 编号 | 函数 | 拿什么 | 源 | 请求数 |
|---|---|---|---|---|
| E1 | `fund_list()` | 全量清单 2.8万只（代码/简称/类型/拼音） | fund.eastmoney.com | 首载1，缓存0 |
| 前置 | `norm_fund_code(q)` | 代码/名称/拼音→归一化；股票代码拦截；A/C消歧 | 本地(E1缓存) | 0 |
| E2 ⭐ | `fund_snapshot(q)` | 一站式：净值全史/规模/持有人/经理/重仓股+债/资产配置/排名/申赎/费率/ishb | fund.eastmoney.com | 1 |
| E3 | `fund_nav_history(q,s,e)` | 区间净值+**每日申赎状态**（E2失败时的净值兜底） | api.fund.eastmoney.com | 1~N页 |
| E4a | `fund_holdings(q,year)` | 季度前十大重仓（含历史各季报，与E2互证） | fundf10.eastmoney.com | 1 |
| D1 | `fund_detail_danjuan(q)` | 蛋卷档案：规模/经理/托管/风险等级/申赎（跨域备胎） | danjuanfunds.com | 1 |
| E6 | `fund_realtime_nav(codes)` | 批量最新净值（N只1请求，轻量探测） | fundmobapi.eastmoney.com | 1 |
| 本地 | `adjusted_nav(recs)` | 复权净值（分红再投口径，链式） | 零网络 | 0 |
| 本地 | `fund_metrics(snap,s,e)` | 区间收益/年化/最大回撤/波动/夏普/卡玛 | 零网络 | 0 |
| 本地 | `fund_dca_backtest(snap,...)` | 定投回测（真实复权净值） | 零网络 | 0 |
| 本地 | `holding_map(snap)` | 重仓股穿透桥 → 喂 a-stock-data | 零网络 | 0 |
| L6 | `tencent_quote(codes)` | 重仓股批量实时行情（价/PE/PB/市值） | 腾讯(不封IP) | N只=1 |
| L6 | `concept_blocks(stock)` | 个股板块标签（行业/概念/地域混合） | 东财 push2 | 1/股,当日缓存 |
| L6 ⭐ | `penetrate(query)` | 一键穿透：权重×行情×板块→主题聚合→`plain_text_map()`白话地图 | 组合 | 冷启≤14,重跑2 |
| L7 | `cb_yield_curve(curve,year)` / `cb_curve_series` / `credit_spread` | 中债曲线族（国债/企业债/存单AAA~A/城投…）年文件·单期限序列·信用利差本地算差 | 中债(curl通道) | 1/曲线/年,当日缓存 |
| L7 | `treasury_yield_daily(days)` / `shibor_history(period)` / `benchmark_rate_history()` | 中美国债日频+利差 / SHIBOR全史 / 基准利率调整史(附次日沪深) | 东财 datacenter | 1 |
| L7 | `repo_rate_gc()` / `cn_treasury_realtime()` | GC007回购(DR007代理) / 国债四期限盘中 | 腾讯+push2 | 1+4 |
| 降级 | `fund_snapshot` 自动降级 | E2挂→E3+E4a+蛋卷分拆，source标注degraded | 组合 | ~N+2 |

**坑区速查（全部 2026-11-05 实测，修复已内嵌）：**

| 坑 | 症状 | 防线 |
|---|---|---|
| P1 协议 | pingzhongdata 走 HTTP **静默返 0 字节**（200照常，UA无关） | ttfund_get 强制升级 HTTPS |
| P2 lsjz三连 | pageSize 钳制20（49~200只回20，500静默0）；TotalCount在顶层；无区间行为不稳 | 固定20+顶层TotalCount+实收强校验 |
| P3 变量缺失 | 按基金类型选择性存在（白酒无 industryAllocation） | missing_datasets 显式列出 |
| P3b 注释陷阱 | 值后挂 `;/*注释*/var`；stockCodesNew 是 secid 数组 | 按相邻var切片+剥注释+剥secid前缀 |
| P4 同码碰撞 | 000001=华夏成长混合 vs 平安银行；600519 是股票 | 不在基金清单→报错引导 a-stock-data |
| P5 已死端点 | fundgz 盘中估值404；蛋卷 growth 服务器错误 | 净值无跨域备胎，诚实报缺 |
| P6 分红剥离 | unitMoney 事件在但金额被剥离（"分红：每10份派现金元"），拆分混入 | 复权主路径链接官方 equityReturn，unitMoney 仅回退 |
| P7 序反转 | E3 新→旧 vs E2 旧→新，降级序列喂指标收益方向全反 | 降级快照反转 + 指标/定投入口升序断言 |
| P8 searchYc黑洞 | 中债查询端点 POST 恒返[]，参数无文档 | 只走 downYearBzqx GET 直出年xlsx |
| P9 inlineStr | 中债 xlsx 无 sharedStrings，值全在行内 | zipfile+regex 解析，零 pandas 依赖 |
| P10 报表名 | 东财 datacenter 臆测名全 9501 | 从页面 bundle 逆向（国债/SHIBOR/基准利率三个名实测） |
| P11 TLS指纹墙 | 中债掐 Python TLS（requests SSL EOF，curl 稳过） | _cb_request 走 subprocess curl，同入限流账本 |

## Prerequisites

```bash
pip install requests
```

硬依赖 requests + 系统 curl（L7 中债通道需要——中债 TLS 指纹墙掐 Python 客户端，
curl 实测稳定，见坑 P11；macOS/Linux 系统自带）。pandas 可选，缺了不影响任何端点。L6 穿透自带内嵌股票域通道（腾讯行情+东财板块），无需 a-stock-data 即可跑通；个股深度分析（龙虎榜/估值历史分位/资金流等）再加载 a-stock-data。
""")

S.append("## L1 前置层：统一入口 + 主键表\n\n所有端点的前置依赖，先执行本节代码：\n")
S.append(code(header_code + sections["L1 前置层：限流入口 / 缓存目录 / 请求计数"]))
S.append("## L1 前置层：全量基金清单 + 归一化\n")
S.append(code(sections["L1 前置层：全量基金清单（主键表，缓存 30 天）"]))
S.append("""## 端点层

### fund_snapshot（E2 ⭐主端点 + 自动降级）

一只基金 90% 静态数据在这一个请求里；E2 失败自动分拆降级（E3+E4a+蛋卷），返回体 `source` 标注 `degraded`：
""")
S.append(code(sections["L4 解析层 + V0.1 端点：fund_snapshot（E2 主端点，1 请求 = 90% 静态数据）"]))
S.append("### fund_nav_history（E3 补充端点：区间 + 每日申赎状态）\n")
S.append(code(sections["V0.1 端点：fund_nav_history（E3 补充端点：区间 + 每日申赎状态）"]))
S.append("### fund_holdings（E4a 季度前十大，可按年份拉历史）\n")
S.append(code(sections["V0.1 端点：fund_holdings（E4a 季度前十大，可按年份拉历史）"]))
S.append("### fund_detail_danjuan（D1 蛋卷跨域备胎）\n")
S.append(code(sections["V0.1 端点：蛋卷备胎（D1 档案 / 唯一跨域活路）"]))
S.append("### fund_realtime_nav（E6 批量实时净值，N 只 = 1 请求）\n")
S.append(code(sections["V0.1 端点：fund_realtime_nav（E6 批量实时净值，N 只 = 1 请求）"]))
S.append("## 本地计算层（零网络）\n")
S.append(code(sections["L5 本地计算层（零网络）"]))
S.append("## L6 跨域穿透层（V0.2.0：重仓股 → 股票域 → 白话地图）\n")
_l6_key = next(k for k in sections if k.startswith("L6"))
S.append(code(sections[_l6_key]))
S.append("## L7 宏观利率层（V0.3.0：中债曲线族 + 东财利率报表 + 腾讯回购代理）\n")
S.append(code(sections[next(k for k in sections if k.startswith("L7"))]))

S.append("""## 使用示例

```python
# 流A：单基金深度诊断（常态 1 请求）
snap = fund_snapshot("161725")                    # 或传名称 "招商中证白酒"
m = fund_metrics(snap)                            # 复权口径全指标
dca = fund_dca_backtest(snap, amount=1000)        # 定投回测
print(m["total_return_pct"], m["max_drawdown_pct"], dca["return_pct"])

# 名字消歧（A/C 份额族 → 列候选不猜）
norm_fund_code("华夏成长")                         # matches: 000001/000002/... resolved=None

# 持仓穿透 → a-stock-data
hm = holding_map(snap)                            # 6位纯代码清单 + 调用说明

# 批量净值（N只1请求）
fund_realtime_nav(["161725", "110022", "000198"])

# L6 一键重仓穿透（R9 场景 04）
print(plain_text_map(penetrate("161725")))   # → "这只基金主要是白酒（约 86%）"+持仓明细表

# L7 宏观利率层（债基利率敏感度 / 理财波动地图）
cb_yield_curve("treasury", year=2026)        # 中债国债曲线全年日频（3536点）
credit_spread("AA", "3y")[-1]                # 3Y AA 信用利差（企业债−国债对齐算差）
shibor_history("1年(1Y)", days=5)            # SHIBOR 1Y
repo_rate_gc()                               # GC007（DR007 交易所口径代理）
cn_treasury_realtime()                       # 国债 2/5/10/30Y 盘中
```

## When to Activate

- 用户要查基金净值/累计净值/复权净值/分红再投收益
- 用户要做基金诊断/对比/筛选/尽调（收益·回撤·夏普·同类排名）
- 用户要看基金持仓（重仓股/重仓债/行业/资产配置）、穿透到股票层（实时行情/概念板块/主题聚合白话地图）
- 用户要定投回测/持有收益测算
- 用户要基金档案（经理/规模变动/持有人结构/费率/申赎状态/风险等级）
- 用户要批量基金净值刷新或全市场基金清单
- 关键词：基金净值、复权、分红、回撤、夏普、卡玛、定投、回测、重仓、持仓穿透、白话地图、主题聚合、基金经理、基金规模、持有人结构、基金排名、货币基金、LOF、FOF、场外基金、天天基金、蛋卷、国债收益率、收益率曲线、信用利差、SHIBOR、银行间利率、同业存单、回购利率、GC007、DR007、基准利率、LPR、中债曲线

**不覆盖（明确边界）**：账户持仓/交易下单（用 ttskill）、港基专项、盘中实时估值（官方已下线，后续版本提供本地估算）、基金经理历任切分、全市场排名筛选 rankhandler、货基万份收益、债券持仓明细 zqcc（均规划于下一版本）。
""")

out = SKILL_DIR / "SKILL.md"
out.write_text("\n".join(S), encoding="utf-8")
(SKILL_DIR / "fund_data.py").write_text(SRC, encoding="utf-8")
(SKILL_DIR / "test_fund_data.py").write_text(
    (HERE / "test_fund_data.py").read_text(encoding="utf-8"), encoding="utf-8")
(HERE / "SKILL.md").write_text("\n".join(S), encoding="utf-8")   # 仓库副本同步（防两处漂移）
print(f"OK: {out} ({out.stat().st_size} bytes) + 仓库副本 {HERE/'SKILL.md'}")
print(f"附带: {SKILL_DIR/'fund_data.py'} / {SKILL_DIR/'test_fund_data.py'}")
