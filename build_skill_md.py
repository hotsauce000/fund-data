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
description: 当任务需要写代码实际获取基金领域底层数据时使用——拉取基金全量清单(~2.8万只)、净值全史(单位/累计/复权/分红事件)、单基金一站式快照(pingzhongdata 436KB→20+数据集:规模变动/持有人结构/现任经理/重仓股+债/资产配置/同类排名/申赎状态/费率/货基标志)、季度前十大持仓、区间净值+每日申赎状态、批量实时净值(N只1请求)、蛋卷跨域备胎，以及零网络的本地计算:复权净值(分红再投)/区间收益/最大回撤/波动/夏普/卡玛/定投回测/持仓穿透对接a-stock-data。请求预算制防封:单基金诊断≤2请求、当日磁盘缓存命中0请求、全市场筛选只走列表端点。仅在需要调用数据接口取数时使用：基金概念解释、投资观点讨论等无需取数的话题不要加载本skill。
origin: custom
version: 0.1.2
---

# fund-data 基金底层数据工具包 V0.1.2

天天基金主源 + 蛋卷跨域备胎 + 本地计算层。**V0.1.2（2026-11-05）：38/38 断言实测通过**（L1单元16 · L2端点smoke 9 · L3三源交叉验证4 · L4请求预算审计2 · L5降级演练7），全程 39 请求覆盖 6 个上游接口。同目录 `fund_data.py`（可直接 import 的同款模块）与 `test_fund_data.py`（回归测试）与本文件内嵌代码逐字节一致。

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

**四条铁律：**

1. **请求预算**：单基金深度诊断 ≤2 请求（1×pingzhongdata 当日缓存 + 0~1×lsjz）；全市场筛选只允许列表端点（rankhandler，1请求=50只全字段）
2. **明令禁止** `for fund in all_funds: fund_snapshot(fund)` —— 这是 a-stock-data #36 案例 20 小时封禁的复刻路径
3. **聚合优先**：任何数据先问"pingzhongdata 里有没有"（一次 436KB 覆盖 90% 静态数据），再考虑 F10 窄端点
4. **所有东财/天天/蛋卷请求必须走 `ttfund_get()`**：强制 HTTPS + 串行限流（1.5s+抖动）+ 会话复用 + 瞬态重试1次 + 请求计数（预算审计靠它）

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

## Prerequisites

```bash
pip install requests
```

仅此一个硬依赖（pandas 可选，缺了不影响任何端点）。跨 skill 穿透（holding_map）需另加载 a-stock-data。
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
```

## When to Activate

- 用户要查基金净值/累计净值/复权净值/分红再投收益
- 用户要做基金诊断/对比/筛选/尽调（收益·回撤·夏普·同类排名）
- 用户要看基金持仓（重仓股/重仓债/行业/资产配置）或穿透到股票层
- 用户要定投回测/持有收益测算
- 用户要基金档案（经理/规模变动/持有人结构/费率/申赎状态/风险等级）
- 用户要批量基金净值刷新或全市场基金清单
- 关键词：基金净值、复权、分红、回撤、夏普、卡玛、定投、回测、重仓、持仓穿透、基金经理、基金规模、持有人结构、基金排名、货币基金、LOF、FOF、场外基金、天天基金、蛋卷

**不覆盖（明确边界）**：账户持仓/交易下单（用 ttskill）、港基专项、盘中实时估值（官方已下线，V0.3 将提供本地估算）、基金经理历任切分（V0.2）、全市场排名筛选 rankhandler（V0.2）、货基万份收益（V0.2）。
""")

out = SKILL_DIR / "SKILL.md"
out.write_text("\n".join(S), encoding="utf-8")
(SKILL_DIR / "fund_data.py").write_text(SRC, encoding="utf-8")
(SKILL_DIR / "test_fund_data.py").write_text(
    (HERE / "test_fund_data.py").read_text(encoding="utf-8"), encoding="utf-8")
print(f"OK: {out} ({out.stat().st_size} bytes)")
print(f"附带: {SKILL_DIR/'fund_data.py'} / {SKILL_DIR/'test_fund_data.py'}")
