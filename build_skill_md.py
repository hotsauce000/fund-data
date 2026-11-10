# -*- coding: utf-8 -*-
"""从实测通过的 fund_data.py 自动组装双层文档（V0.4.2 渐进加载架构）：

产物一 SKILL.md（瘦路由层，上限 12KB）：skill 激活时唯一进上下文的文件——
  防封铁律 + 场景路由表 + 失效/备源路由表 + 函数签名速查（ast 从模块自动抽取）
产物二 reference.md（全量参考）：按需读取——完整内嵌代码（与源码同源直拼）
  + P1~P18 完整坑区 + 端点路由 + 使用示例 + 版本记录
防漂移：签名表自动生成；构建时强校验（瘦身体积上限 + 签名覆盖全部公开函数）。
"""
import ast
import re
from pathlib import Path

HERE = Path(__file__).parent
SRC = (HERE / "fund_data.py").read_text(encoding="utf-8")
SKILL_DIR = Path.home() / ".agents" / "skills" / "fund-data"
SKILL_DIR.mkdir(parents=True, exist_ok=True)

# ── 按「═ / 标题 / ═」三行标记对切块，映射到 reference.md 章节 ──
pat = re.compile(r"# ═══+\n# (.+?)\n# ═══+\n")
marks = list(pat.finditer(SRC))
sections = {}
for i, m in enumerate(marks):
    body = SRC[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(SRC)]
    sections[m.group(1).strip()] = body
header_code = SRC[:marks[0].start()]                     # 文件头(docstring+imports+常量)

def code(body: str) -> str:
    return "```python\n" + body.rstrip() + "\n```\n"

# ── ast 签名速查（防漂移：每次构建从源码重抽）──

def _fmt_args(node: ast.FunctionDef) -> str:
    args, defaults, parts = node.args.args, node.args.defaults, []
    pad = len(args) - len(defaults)
    for i, a in enumerate(args):
        if i >= pad:
            parts.append(f"{a.arg}={ast.unparse(defaults[i - pad])}")
        else:
            parts.append(a.arg)
    if node.args.vararg:
        parts.append(f"*{node.args.vararg.arg}")
    if node.args.kwarg:
        parts.append(f"**{node.args.kwarg.arg}")
    return ", ".join(parts)


def _sig_rows() -> list:
    """全部公开函数 → [(name, args_str, docstring 首行)]。"""
    rows = []
    for node in ast.parse(SRC).body:
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            doc = (ast.get_docstring(node) or "").strip().split("\n")[0].rstrip("。")
            rows.append((node.name, _fmt_args(node), doc))
    return rows


VERSION = "0.5.0"
DESCRIPTION = (
    "当任务需要写代码实际获取基金领域底层数据时使用——拉取基金全量清单(~2.8万只)、净值全史(单位/累计/复权/分红事件)、"
    "单基金一站式快照(pingzhongdata 436KB→20+数据集:规模变动/持有人结构/现任经理/重仓股+债/资产配置/仓位史/同类百分位史/"
    "基准三曲线(本基金-同类平均-沪深300)/申赎状态/费率/货基标志)、季度前十大持仓、区间净值+每日申赎状态、批量实时净值"
    "(N只1请求)、蛋卷跨域备胎，零网络的本地计算:复权净值(分红再投)/区间收益/最大回撤/波动/夏普/卡玛/管理费率标注/"
    "定投回测(XIRR分期真年化+朴素对照)/仓位史解读(加减仓信号)/同类百分位解读(一年前对照)/"
    "相对表现(vs沪深300或同类平均:相对收益+β+相关性)，以及L6跨域穿透:重仓股批量实时行情(腾讯N只1请求)+"
    "个股板块归属(东财slist当日缓存)+共现加权主题聚合,一键输出'这只基金主要是白酒约86%'式白话地图；"
    "L7宏观利率层:中债收益率曲线族40+条(国债/企业债AAA~AA-/同业存单全评级/城投,1请求=1曲线1年日频)、信用利差本地算差、"
    "国债收益率日频(中美)、SHIBOR多期限(单页500行上限)、存贷基准利率史、GC007回购代理、国债盘中实时；"
    "V0.5全市场数据层:全市场排行/初筛(2.4万只1请求,过滤排序本地缓存0请求)、新发基金全量(附公司ID映射)、"
    "基金公司画像、季度抱团度(巨潮官方专题)与行业配置(19门类)。"
    "请求预算制防封:单基金诊断≤2请求、当日磁盘缓存命中0请求、全市场筛选只走列表端点、巨潮独立限流线(1.5s+enckey)。"
    "仅在需要调用数据接口取数时使用：基金概念解释、投资观点讨论等无需取数的话题不要加载本skill。")

# ══════════════════════════════════════════════════════════════════
# 产物一：瘦路由层 SKILL.md
# ══════════════════════════════════════════════════════════════════

_sig_md = "\n".join(f"| `{n}({a})` | {d} |" for n, a, d in _sig_rows())

THIN = []
THIN.append(f"""---
name: fund-data
description: {DESCRIPTION}
origin: custom
version: {VERSION}
---

# fund-data 路由层 V{VERSION}

本文件是**路由层**（瘦入口，≈3K token）——skill 激活只加载本文件。实现细节/完整坑区/内嵌代码在同目录 **reference.md（按需读取，勿预加载）**；运行时代码在同目录 `fund_data.py`（import 用，勿通读）：

```python
import sys
sys.path.insert(0, str(__import__("pathlib").Path.home() / ".agents" / "skills" / "fund-data"))
from fund_data import fund_snapshot, fund_metrics   # 按场景路由表选函数
```
""")

THIN.append("""## 防封铁律（先读，违反=封禁20+小时）

东财风控阈值：>5 次/秒、并发 ≥10、5 分钟 ≥300 次。五条铁律：

1. **请求预算**：单基金深度诊断 ≤2 请求（1×pingzhongdata 当日缓存 + 0~1×lsjz）；当日缓存命中 0 请求
2. **明令禁止** `for fund in all: fund_snapshot(fund)`（全量循环=封禁路径）；全市场排行已内置全量单请求勿翻页
3. **聚合优先**：先问 pingzhongdata（1 请求=90% 静态数据），再考虑窄端点
4. **东财/天天/蛋卷请求必须走 `ttfund_get()`**（F1锁+F2熔断+F6分流+P12直连）；批量 ≥10 只把 `TT_MIN_INTERVAL` 调 2~3s；巨潮走第四线 `_cninfo_post`（1.5s 节流+enckey）
5. 缓存 TTL：fund_list 30天 / snapshot·持仓·宏观·排行·新发 20h当日 / 公司画像 24h / 抱团度·行业配置 90天（季报不可变）/ lsjz 不缓存
""")

THIN.append("""## 场景路由（我想做 X → 调什么）

| 场景 | 调用链 | 请求 |
|---|---|---|
| 诊断一只基金 | `fund_snapshot` → `fund_metrics` + `position_trend`/`peer_rank`/`relative_performance` | 1 |
| 全市场排行/初筛 | `fund_rank()`（全量1请求）→过滤排序本地0请求 | 1（缓存0） |
| 定投测算 | `fund_snapshot` → `fund_dca_backtest`（XIRR 真年化） | 1 |
| 这基金买了什么 | `penetrate` → `plain_text_map`（白话地图） | 冷启≤13/重跑1 |
| 名称/拼音找代码 | `norm_fund_code`（多候选列出不猜）/ `resolve_fund_code`（强报错） | 0 |
| 净值核对/每日申赎状态 | `fund_nav_history`（新→旧序需反转） | 1~N 页 |
| 批量最新净值 | `fund_realtime_nav`（≤50/请求） | N/50 |
| 新发/在发基金 | `fund_new_issues()`（全量+公司ID映射） | ~7（缓存0） |
| 基金公司画像 | `fund_company(基金码或8位公司ID)` | 1（基金码+1） |
| 抱团度/行业配置 | `fund_crowding()` / `fund_industry_allocation()`（季度巨潮） | 1/季（90天） |
| 债基利率敏感度 | `cb_yield_curve` + `credit_spread` / `treasury_yield_daily` | 1~2 |
| 重仓股深度（龙虎榜/估值史） | `holding_map` → **a-stock-data skill** | 0 |

## 失效与备源路由（失败时怎么办）

| 故障 | 行为 | 硬边界 |
|---|---|---|
| E2 快照失败 | **自动降级** E3+E4a+蛋卷，`source="degraded"` 标注 | 降级窗口默认 1 年 |
| E2 返回风控页/空体 | 自动识别走降级，不污染缓存 | — |
| 历史净值需要兜底 | E3 lsjz 逐页（TotalCount 强校验） | **跨域无备胎（P17 三候选全死）**——别试蛋卷growth/新浪 |
| 个股行情 | 腾讯通道（不封 IP） | — |
| 利率曲线 | 中债 curl 通道（TLS 指纹墙 P11） | 东财 datacenter 互证 |
| rankhandler 被拦 | 200+「无访问权限」（**无 403**）→ `RiskControlError`+熔断（P19） | 查 Referer 头 |
| cninfo 451/token null | enckey 时效**非风控**，刷新重试一次（P20） | 仍拒=时钟漂移 |
| 403/429 | `RiskControlError` 上抛+熔断10min（不触发降级） | 冷却期零网络，等勿重试 |
""")

THIN.append(f"""## 函数速查（{_sig_rows().__len__()} 个，构建时自动生成勿手改）

| 函数 | 说明 |
|---|---|
{_sig_md}
""")

THIN.append("""## When to Activate

查基金净值/复权/分红/回撤/夏普/卡玛；基金诊断评测（收益·风险·同类排名·仓位·相对基准）；持仓穿透白话地图；定投回测；基金档案（经理/规模/持有人/费率/申赎）；批量净值；国债收益率/信用利差/SHIBOR。关键词：基金净值、复权、回撤、定投、重仓、持仓穿透、白话地图、持有人结构、信用利差、GC007、中债曲线、全市场排行、基金初筛、抱团度、行业配置、公司画像、新发基金。

## 深读与边界（按需）

- **实现细节/完整坑区 P1~P21/使用示例/版本记录 → 读同目录 `reference.md`**（调试/扩展/排坑时读）
- 不覆盖：账户持仓/交易下单（ttskill）、盘中实时估值、货基万份收益（P15 语义漂移勿当净值）、单基金行业史（E2停供；全市场季度口径有 `fund_industry_allocation`）、语义主题/事件选基
- 个股深度分析 → a-stock-data；`holding_map()` 是官方桥
""")

# ══════════════════════════════════════════════════════════════════
# 产物二：reference.md（全量参考，按需读取层）
# ══════════════════════════════════════════════════════════════════

REF = []
REF.append(f"""# fund-data 完整参考（reference.md · 按需读取层）V{VERSION}

> 本文件按需读取：调试 / 扩展 / 排查坑时才打开，勿随 skill 激活预加载。
> 路由与签名速查见 SKILL.md；本文件内嵌代码与 fund_data.py 同源直拼（构建时生成）。

**V0.4.2（2026-11-10）文档架构：渐进加载**——SKILL.md 瘦身为路由层（上限 12KB 内 ≈3K token，激活省 ~90%），全量参考移本文件；函数签名速查改 ast 自动生成（杜绝"README 改了模板没改"漂移）；credit_spread 测试锚 0.6 防方向颠倒。
""")

REF.append("""# fund-data 基金底层数据工具包 V0.5.0 模块全档

天天基金主源 + 蛋卷跨域备胎 + 本地计算层 + L6 跨域穿透 + L7 宏观利率层 + **V0.5 全市场数据层（rankhandler 排行底座 + 巨潮第四线）**。**V0.5.0（2026-11-10）：142/142 断言实测通过**（L1单元90 · L2端点smoke 30 · L3三源交叉验证5 · L4请求预算审计2 · L5降级演练7 · 防封专项F×8），覆盖 15 个上游接口。同目录 `fund_data.py`（可直接 import 的同款模块）与 `test_fund_data.py`（回归测试）与本文件内嵌代码逐字节一致。

**V0.5.0 全市场数据层（四端点 + cninfo 第四线基础设施，防封为第一约束）：**
- 🟢 **N1 `fund_rank`**：rankhandler.aspx 全市场 24792 只 **1 请求**（鉴权=Referer fundranking.html，无 cookie）；top=None 全量 20h 缓存，fund_type/sort/ascending 全部本地执行=组合筛选 0 请求；签名上不存在翻页参数（496 页逐页爬的封禁路径从 API 形态上消失）；None 值恒排末尾；字段序经官方 syl 四点校准（161725 近1月/3月/6月/1年分毫不差）
- 🟢 **N2 `fund_new_issues`**：在发/新成立全量（record 总数强校验，≈7 页），顺带产出「公司名→公司ID」映射（fund_company 消歧用）
- 🟢 **N3 `fund_company`**：公司画像 1 请求（8 位公司ID 直取 / 6 位基金码经 jbgk 直链转 / 公司名走新发缓存 best-effort）；管理规模/基金数/经理数/旗下清单服务端直出
- 🟢 **N4 `fund_crowding` / `fund_industry_allocation`**：巨潮官方专题统计（p_sysapi1112/1113）季度重仓股聚合（3088 只，Top1 中际旭创 2424 家）+ 19 门类行业配置——抱团度原料 + 补上 E2 停供后的行业配置缺口；Accept-Enckey=AES-CBC(秒级时间戳) 用系统 node 子进程算（零新 Python 依赖，需系统 node，无则两函数显式报错）。⚠️ 披露窗口：季末后约 15 个工作日内最新季聚合不完整且首拉即缓存 90 天——刚过季末显式传上一季度 rdate
- 🔴 **P19 rankhandler 风控形态**：被拦是 200+「无访问权限」**而非 403**——状态码层面查不出 → `assert_rankhandler` 内容级断言接入全部 rankhandler 调用点
- 🔴 **P20 cninfo enckey**：451/"token null" = 钥匙时效问题（时钟漂移/秒边界）**非风控**——自动刷新重试一次不计熔断，仍拒才报错
- 🟡 **P21 r.text 编码**：requests 无 charset 头时默认 ISO-8859-1，中文正则全失灵——新端点一律 `r.content.decode("utf-8", errors="replace")`
- 防封预算：全市场排行=1 请求（20h）；类型过滤/排序本地 0 请求；抱团度 1 请求/季（90 天缓存，已发布季报不可变）；cninfo 独立 flock 节流 1.5s + 独立熔断通道 + `CNINFO_REQUEST_COUNT` 独立账本

**V0.4.1 审计修复（P-A~P-E + 复核遗留）：**
- 🔴 **P-C 持仓列序防线升级**：E4a 从"文案存在校验+下标盲取"升级为 thead 动态列映射——上游保留文案但调列位时不再静默错列（+2 断言）
- 🔴 **P-A 穿透预算黑洞**：penetrate 基金名改从 fund_list 直查（0 请求）——原实现为拿名字拉 436KB 快照；当日重跑 2→1 请求
- 🟡 P-B 预算文档漂移修正 / P-D `_dc_query` 空结果也缓存 / P-E credit_spread 报错区分"期限不存在 vs 日期错位"
- 🟡 复核遗留：XIRR 上限常量化（note 不再硬编码）、beta_note 按横盘侧分别归因、peer_rank 复用 `_assert_ascending_dates`、模块抬头版本、新守卫测试 +16（含利差三段报错/基金横盘分支）

**V0.4.0 正确性欠账清偿 + 在手数据接线（A/B 批次）：**
- 🔴 **P18 归一化双 bug 修复**：E2 表格变量两种形态旧解析器都吃不下——`{categories, series:[{y,mom}]}` 行式（规模史）值全丢成光杆 `_date` 行；`[[ts,val],...]` 时序对（仓位/同类百分位）被当"首行表头"产出乱码。修复后规模史/仓位史/同类百分位史全部恢复真实值（下游评测 skill 的份额兜底不再是常态）
- 🔴 **XIRR 兑现**：`fund_dca_backtest` 的 `irr_xirr_pct` 从"注释承诺 V0.2 补"到真值（二分法，清零无解显式 None）；naive 年化注明偏差方向取决于路径
- 🟢 **B1 基准三曲线**：`Data_grandTotal`（本基金/同类平均/沪深300，窗口约半年）从"只挂名防缺失"到 `snapshot.benchmark_curves`，fund-data 本体从此自带基准
- 🟢 **B2/B3 评测函数**：`position_trend`（仓位水平+加减仓）/ `peer_rank`（同类百分位+一年前对照）/ `relative_performance`（vs 沪深300或同类平均：相对收益+β+相关性）/ `fund_metrics` 附管理费率标注；实测白酒基金窗口收益与 grandTotal 基金曲线末点分毫不差（-4.23 vs -4.22）
- 坑 P15~P17 入册：E6 货基字段语义漂移 / QDII 日历差 / 跨域净值备胎不可得（三候选实测全死）

**V0.3.1 防封加固（两轮审计 F1~F7 修复）：**
- 🔴 **F1 跨进程节流锁**：限流时间戳从进程内存改磁盘文件+flock——多进程并发时合并速率不再越线（原实现双进程批量即可达封禁阈值的 2/3）
- 🔴 **F6 风控信号与网络故障分流**：`assert_not_risk_control()` 防线接入全部 8 个东财调用点——403/429 抛 `RiskControlError`（非 RequestException 子类），**绝不触发降级风暴**
- 🔴 **F2 熔断器**：连续 3 次风控信号→落盘熔断 10 分钟→冷却期内所有请求直接拒绝（零网络）；成功响应清零连击；tt/em 两线独立
- 🟡 F3 降级窗口 3年→1年；F4 fund_holdings 当日缓存；F7 L7 宏观层当日缓存；G2 缓存原子写 tmp+rename
- 测试 65→70：双进程合并间隔/403不触降级/冷却期拒绝且零网络/宏观与持仓二调0请求

**V0.3.0 新增：L7 宏观利率层（债基/理财叙事的数据地基）**
- 中债官网曲线族（`cb_yield_curve`，40+ 条曲线：国债/企业债AAA~AA-/存单全评级/城投…，1请求=1曲线1年日频全期限，当日缓存）+ `credit_spread` 本地算差 + `cb_curve_tree` 全族 ID
- 东财利率报表（坑P10 报表名全部从页面 bundle 逆向实测）：`treasury_yield_daily`（中美国债日频+期限利差）/ `shibor_history`（全期限·单页至多500行）/ `benchmark_rate_history`（2004起基准利率调整+次日沪深涨跌）
- `repo_rate_gc`（GC007 腾讯通道，DR007 交易所口径代理）+ `cn_treasury_realtime`（push2 国债 2/5/10/30Y 盘中）
- 坑P8~P11 入册：中债 searchYc 参数黑洞/inlineStr xlsx/EM 报表名逆向/**中债 TLS 指纹墙（requests 稳挂 curl 稳过，通道=subprocess curl）**
- 实测三源互证：中债 10Y ↔ 东财（同口径近乎一致）↔ push2 盘中（断言容差 0.05/0.25pct）

**V0.2.0 新增：L6 跨域穿透层**
- `penetrate(query)` 一键穿透：重仓股权重 × 腾讯批量行情 × 东财板块归属 → 共现加权主题聚合 → `plain_text_map()` 白话输出
- 穿透纪律：单股失败跳过不中断；每股标签先去重再累计（Ⅱ/Ⅲ 归并，否则权重翻倍）；92 北交所号段先于 9x 判断

**V0.1.2 修复记录（2×P0 + 5×P1）：**
- 🔴 **P0 降级快照净值序反转**：E3 原生新→旧、E2 旧→新——降级序列直接喂指标会收益方向全反。已反转 + 升序断言双防线
- 🔴 **P0 垃圾响应写缓存**：HTTP 200 + ≥1KB 风控页会产出全空快照污染缓存 20h。已加关键变量强校验
- 🔴 **unitMoney 金额被剥离**且拆分混入——复权引擎改链接官方 equityReturn；对照官方 syl_1n 误差 0.4pct
- 🟡 空查询/降序输入/start>end/空区间显式报错；北京时区；E6 缺只报错；jjcc 表头校验

**V0.1.1 修复记录（3 个文档外格式坑）：**
- 🔴 `stockCodesNew` 是 secid 数组而非逗号串；🔴 值后挂块注释吞后文；🔴 lsjz TotalCount 在顶层
""")

REF.append("""## 第一章 防封铁律（请求预算契约）

本 skill 的设计约束不是"接口能不能通"，而是"请求额度是稀缺资源"。天天基金/东财系与 a-stock-data 的东财同一风控面（社区实测：>5次/秒、并发≥10、1小时4.5万请求 → IP 级封禁 20+ 小时）。

**五条铁律：**

1. **请求预算**：单基金深度诊断 ≤2 请求（1×pingzhongdata 当日缓存 + 0~1×lsjz）；全市场筛选只允许列表端点
2. **明令禁止** `for fund in all_funds: fund_snapshot(fund)` —— a-stock-data #36 案例 20 小时封禁的复刻路径
3. **聚合优先**：任何数据先问"pingzhongdata 里有没有"（一次 436KB 覆盖 90% 静态数据），再考虑 F10 窄端点
4. **所有东财/天天/蛋卷请求必须走 `ttfund_get()`**：强制 HTTPS + 跨进程串行限流（F1）+ 熔断（F2）+ 会话复用 + 瞬态重试1次 + 请求计数；调用方 `raise_for_status()` 前必过 `assert_not_risk_control()`（F6）；rankhandler 家族再过 `assert_rankhandler()`（P19 内容级）；**巨潮 cninfo 只经 `_cninfo_post()`**（独立 flock 节流 1.5s + 独立熔断 + enckey 刷新重试）
5. **批量场景降速**：诊断 ≥10 只基金时把 `TT_MIN_INTERVAL` 调到 2~3s；多进程并发已被 F1 文件锁强制串行

**缓存分层（同基金当日反复诊断 = 齐网络）：**

| 数据 | TTL | 说明 |
|---|---|---|
| fund_list（2.8万只主键表） | 30天 | miss 单点回源在线查一只，不为1只重下2MB |
| fund_snapshot | 当日20h | pingzhongdata 每日更新一次 |
| fund_rank 全量 / new_issues | 当日20h | rankhandler 每日更新一次；过滤排序全在缓存上 0 请求 |
| fund_company | 24h | 公司页静态 |
| fund_crowding / fund_industry_allocation | 90天 | 季报发布后不可变；1 请求/季 |
| lsjz | 不缓存 | 补充端点，按区间现查 |
""")

REF.append("""## 端点路由速查（按需定位，不必通读）

| 编号 | 函数 | 拿什么 | 源 | 请求数 |
|---|---|---|---|---|
| E1 | `fund_list()` | 全量清单 2.8万只（代码/简称/类型/拼音） | fund.eastmoney.com | 首载1，缓存0 |
| 前置 | `norm_fund_code(q)` | 代码/名称/拼音→归一化；股票代码拦截；A/C消歧 | 本地(E1缓存) | 0 |
| E2 ⭐ | `fund_snapshot(q)` | 一站式：净值全史/规模史/持有人/经理/重仓股+债/资产配置/仓位史/同类百分位史/**基准三曲线**/申赎/费率/ishb | fund.eastmoney.com | 1 |
| E3 | `fund_nav_history(q,s,e)` | 区间净值+**每日申赎状态**（E2失败时的净值兜底） | api.fund.eastmoney.com | 1~N页 |
| E4a | `fund_holdings(q,year)` | 季度前十大重仓（含历史各季报，与E2互证） | fundf10.eastmoney.com | 1 |
| D1 | `fund_detail_danjuan(q)` | 蛋卷档案：总份额/经理/托管/风险等级/申赎（跨域备胎） | danjuanfunds.com | 1 |
| E6 | `fund_realtime_nav(codes)` | 批量最新净值（N只1请求，轻量探测） | fundmobapi.eastmoney.com | 1 |
| N1 | `fund_rank(top,fund_type,sort,ascending)` | 全市场排行/初筛底座（ytd/1m~3y/since 收益率族；类型本地过滤；None 恒末尾） | fund.eastmoney.com/data/rankhandler | 全量1，筛选0 |
| N2 | `fund_new_issues()` | 在发/新成立全量 + 公司名→ID 映射 | .../data/FundNewIssue.aspx | ~7页,缓存0 |
| N3 | `fund_company(q)` | 公司画像：规模/基金数/经理数/旗下清单（q=8位ID/基金码/公司名） | .../company/{id}.html | 1（基金码+1） |
| N4 | `fund_crowding(rdate)` / `fund_industry_allocation(rdate)` | 季度重仓股聚合（抱团度）/ 全市场19门类行业配置 | webapi.cninfo.com.cn（第四线） | 1/季,90天缓存 |
| 本地 | `adjusted_nav(recs)` | 复权净值（分红再投口径，链式） | 零网络 | 0 |
| 本地 | `fund_metrics(snap,s,e)` | 区间收益/年化/最大回撤/波动/夏普/卡玛/管理费率标注 | 零网络 | 0 |
| 本地 | `fund_dca_backtest(snap,...)` | 定投回测 + **XIRR 分期真年化**（naive 对照保留） | 零网络 | 0 |
| 本地 | `position_trend(snap)` | 仓位史解读：水平区间/加减仓信号 | 零网络 | 0 |
| 本地 | `peer_rank(snap)` | 同类百分位：最新值+一年前对照+分位标签 | 零网络 | 0 |
| 本地 | `relative_performance(snap,基准)` | vs 沪深300/同类平均：相对收益+β+相关性（用内置三曲线） | 零网络 | 0 |
| 本地 | `holding_map(snap)` | 重仓股穿透桥 → 喂 a-stock-data | 零网络 | 0 |
| L6 | `tencent_quote(codes)` | 重仓股批量实时行情（价/PE/PB/市值） | 腾讯(不封IP) | N只=1 |
| L6 | `concept_blocks(stock)` | 个股板块标签（行业/概念/地域混合） | 东财 push2 | 1/股,当日缓存 |
| L6 ⭐ | `penetrate(query)` | 一键穿透：权重×行情×板块→主题聚合→`plain_text_map()`白话地图 | 组合 | 冷启≤13,当日重跑1 |
| L7 | `cb_yield_curve(curve,year)` / `cb_curve_series` / `credit_spread` | 中债曲线族年文件·单期限序列·信用利差本地算差 | 中债(curl通道) | 1/曲线/年,当日缓存 |
| L7 | `treasury_yield_daily(days)` / `shibor_history(period)` / `benchmark_rate_history()` | 中美国债日频+利差 / SHIBOR多期限 / 基准利率调整史(附次日沪深) | 东财 datacenter | 1 |
| L7 | `repo_rate_gc()` / `cn_treasury_realtime()` | GC007回购(DR007代理) / 国债四期限盘中 | 腾讯+push2 | 1+4 |
| 降级 | `fund_snapshot` 自动降级 | E2挂→E3+E4a+蛋卷分拆，source标注degraded | 组合 | ~N+2 |

**坑区速查（全部实测，修复已内嵌）：**

| 坑 | 症状 | 防线 |
|---|---|---|
| P1 协议 | pingzhongdata 走 HTTP **静默返 0 字节**（200照常，UA无关） | ttfund_get 强制升级 HTTPS |
| P2 lsjz三连 | pageSize 钳制20；TotalCount在顶层；无区间行为不稳 | 固定20+顶层TotalCount+实收强校验 |
| P3 变量缺失 | 按基金类型选择性存在 | missing_datasets 显式列出（含 present,unparsed 区分） |
| P3b 注释陷阱 | 值后挂 `;/*注释*/var`；stockCodesNew 是 secid 数组 | 按相邻var切片+剥注释+剥secid前缀 |
| P4 同码碰撞 | 000001=华夏成长 vs 平安银行 | 不在基金清单→报错引导 a-stock-data |
| P5 已死端点 | fundgz 404；蛋卷 growth 死 | 净值无跨域备胎，诚实报缺 |
| P6 分红剥离 | unitMoney 金额被剥离，拆分混入 | 复权主链官方 equityReturn，unitMoney 仅回退 |
| P7 序反转 | E3 新→旧 vs E2 旧→新 | 降级反转 + 升序断言双防线 |
| P8 searchYc黑洞 | 中债查询端点 POST 恒返[] | 只走 downYearBzqx GET 年xlsx |
| P9 inlineStr | 中债 xlsx 无 sharedStrings | zipfile+regex 解析，零 pandas 依赖 |
| P10 报表名 | 东财臆测名全报 9501 错误 | 从页面 bundle 逆向 |
| P11 TLS指纹墙 | 中债掐 Python TLS（curl 稳过） | subprocess curl 通道，同入限流账本 |
| P12 系统代理抖动 | macOS 系统代理转发国内站间歇 SSL EOF | tt/em Session trust_env=False + 腾讯 no-proxy opener；企业代理网络需改回 True |
| P13 跨进程并发 | 各持内存限流器=合并速率×进程数 | F1 磁盘时间戳+flock 三线锁 |
| P14 风控误降级 | 403 被误捕→降级→37页打风控枪口 | F6 防线 8 调用点 + F2 熔断冷却10min |
| P15 货基语义漂移 | E6 对货基 NAV=每万份收益、ACCNAV=7日年化 | 无货基专项；先看 is_money_fund 标志 |
| P16 QDII 日历差 | 净值 T+1/T+2、非 252 节奏 | 输出解读时标注 |
| P17 跨域净值不可得 | 蛋卷growth/新浪HTML壳/openapi 三候选实测全死 | 诚实边界：东财域被封时净值无备胎 |
| P18 E2 表格双形态 | 行式{y,mom}值全丢；[[ts,v]]被当表头 | 双形态分流 + 时序专用通道（V0.4.0 修复） |
| P19 rankhandler 风控形态 | 被拦=200+「无访问权限」（**无 403**），状态码查不出 | `assert_rankhandler` 内容级断言（前500字符）+ 计入 tt 熔断 |
| P20 cninfo enckey | Accept-Enckey=AES-CBC(秒级时间戳,固定密钥)；451/"token null"=时效**非风控** | node 子进程计算；451 刷新重试一次不计熔断，仍拒=时钟漂移 |
| P21 r.text 编码 | 无 charset 头默认 ISO-8859-1，中文正则全失灵 | 新端点一律 `r.content.decode("utf-8", errors="replace")` |

## Prerequisites

```bash
pip install requests
```

硬依赖 requests + 系统 curl（L7 中债通道需要，P11；macOS/Linux 自带）+ 系统 node（V0.5 巨潮抱团度/行业配置线的 enckey 计算，macOS/Linux 常见自带；无 node 时该两函数显式报错不静默）。pandas 可选。L6 穿透自带内嵌股票域通道，无需 a-stock-data 即可跑通；个股深度再加载 a-stock-data。
""")

REF.append("## L1 前置层：统一入口 + 主键表\n\n所有端点的前置依赖：\n")
REF.append(code(header_code + sections["L1 前置层：限流入口 / 缓存目录 / 请求计数"]))
REF.append("## L1 前置层：全量基金清单 + 归一化\n")
REF.append(code(sections["L1 前置层：全量基金清单（主键表，缓存 30 天）"]))
REF.append("## 端点层\n\n### fund_snapshot（E2 ⭐主端点 + 自动降级）\n")
REF.append(code(sections["L4 解析层 + V0.1 端点：fund_snapshot（E2 主端点，1 请求 = 90% 静态数据）"]))
REF.append("### fund_nav_history（E3 补充端点：区间 + 每日申赎状态）\n")
REF.append(code(sections["V0.1 端点：fund_nav_history（E3 补充端点：区间 + 每日申赎状态）"]))
REF.append("### fund_holdings（E4a 季度前十大，可按年份拉历史）\n")
REF.append(code(sections["V0.1 端点：fund_holdings（E4a 季度前十大，可按年份拉历史）"]))
REF.append("### fund_detail_danjuan（D1 蛋卷跨域备胎）\n")
REF.append(code(sections["V0.1 端点：蛋卷备胎（D1 档案 / 唯一跨域活路）"]))
REF.append("### fund_realtime_nav（E6 批量实时净值，N 只 = 1 请求）\n")
REF.append(code(sections["V0.1 端点：fund_realtime_nav（E6 批量实时净值，N 只 = 1 请求）"]))
REF.append("## 本地计算层（零网络）\n")
REF.append(code(sections["L5 本地计算层（零网络）"]))
REF.append("## L6 跨域穿透层（V0.2.0：重仓股 → 股票域 → 白话地图）\n")
REF.append(code(sections[next(k for k in sections if k.startswith("L6"))]))
REF.append("## L7 宏观利率层（V0.3.0：中债曲线族 + 东财利率报表 + 腾讯回购代理）\n")
REF.append(code(sections[next(k for k in sections if k.startswith("L7"))]))
REF.append("## V0.5 全市场数据层（rankhandler 排行底座 + 新发 + 公司画像 + 巨潮抱团度/行业配置）\n")
REF.append(code(sections["V0.5 全市场数据层：rankhandler 排行/初筛 + 新发 + 公司 + cninfo 抱团度/行业配置"]))

REF.append("""## 使用示例

```python
# 流A：单基金深度诊断（常态 1 请求）
snap = fund_snapshot("161725")                    # 或传名称 "招商中证白酒"
m = fund_metrics(snap)                            # 复权口径全指标（含管理费率标注）
dca = fund_dca_backtest(snap, amount=1000)        # 定投回测（XIRR 分期真年化）
print(m["total_return_pct"], m["max_drawdown_pct"], dca["irr_xirr_pct"])

# 流A'：评测视角三件套（零网络，全部吃 snapshot）
position_trend(snap)                              # 仓位史：最新/区间/加减仓信号
peer_rank(snap)                                   # 同类百分位：最新+一年前对照
rp = relative_performance(snap, benchmark="同类平均")   # 相对同类平均超额

# 名字消歧（A/C 份额族 → 列候选不猜）
norm_fund_code("华夏成长")                         # matches: 000001/000002/... resolved=None

# 持仓穿透 → a-stock-data
hm = holding_map(snap)                            # 6位纯代码清单 + 调用说明

# 批量净值（N只1请求）
fund_realtime_nav(["161725", "110022", "000198"])

# L6 一键重仓穿透
print(plain_text_map(penetrate("161725")))   # → "这只基金主要是白酒（约 86%）"+持仓明细表

# L7 宏观利率层
cb_yield_curve("treasury", year=2026)        # 中债国债曲线全年日频
credit_spread("AA", "3y")[-1]                # 3Y AA 信用利差
shibor_history("1年(1Y)", days=5)            # SHIBOR 1Y
repo_rate_gc()                               # GC007
cn_treasury_realtime()                       # 国债 2/5/10/30Y 盘中

# V0.5 全市场数据层
fund_rank()                                  # 全市场 24792 只（1 请求，20h 缓存）
fund_rank(fund_type="gp", sort="1y")         # 类型过滤+排序全在本地缓存 → 0 请求
fund_rank(top=20, fund_type="zs", sort="ytd")  # 服务端 Top20 单页
fund_new_issues()                            # 在发/新成立全量 + 公司名→ID 映射
fund_company("161725")                       # 基金码→公司画像（基金码路径 +1 请求）
fund_company("80065113")                     # 8 位公司 ID 直取（1 请求）
fund_crowding()[:10]                         # 抱团度 Top10（1 请求/季，90 天缓存）
fund_industry_allocation()                   # 19 门类行业配置（巨潮官方口径）
```

**不覆盖（明确边界）**：账户持仓/交易下单（用 ttskill）、港基专项、盘中实时估值、基金经理历任切分（独立评测 skill 的 jjjl 通道）、单基金行业配置史（E2 已停供；全市场季度口径见 `fund_industry_allocation`）、货基万份收益（P15）、债券持仓明细 zqcc。
""")

# ══════════════════════════════════════════════════════════════════
# 双产物写出 + 构建时强校验（防胖回去/防签名漏/防内嵌漂移）
# ══════════════════════════════════════════════════════════════════

thin_text = "\n".join(THIN)
ref_text = "\n".join(REF)

thin_bytes = len(thin_text.encode("utf-8"))
assert thin_bytes < 12000, f"瘦 SKILL.md 超重 {thin_bytes}B（上限 12000）——把长内容移入 reference.md"
for n, _, _ in _sig_rows():                          # 签名覆盖：每个公开函数都在路由层
    assert f"| `{n}(" in thin_text, f"签名速查表漏行 {n}"
assert "def fund_snapshot(query: str, force_refresh: bool = False) -> dict:" in ref_text
assert all(p in ref_text for p in ("P19", "P20", "P21"))          # 坑区三层齐全
assert "def fund_rank(top: Optional[int] = None" in ref_text      # V0.5 内嵌同源
assert "def fund_crowding(rdate: str = \"\", force_refresh: bool = False)" in ref_text

for dest in (SKILL_DIR, HERE):
    (dest / "SKILL.md").write_text(thin_text, encoding="utf-8")
    (dest / "reference.md").write_text(ref_text, encoding="utf-8")
(SKILL_DIR / "fund_data.py").write_text(SRC, encoding="utf-8")
(SKILL_DIR / "test_fund_data.py").write_text(
    (HERE / "test_fund_data.py").read_text(encoding="utf-8"), encoding="utf-8")
print(f"OK: SKILL.md 路由层 {thin_bytes}B（token 下限粗估 ≈{thin_bytes // 3 // 1000}K）+ "
      f"reference.md {len(ref_text.encode('utf-8'))}B；函数速查 {len(_sig_rows())} 个全覆盖")
