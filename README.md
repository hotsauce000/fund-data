# fund-data

> 📦 项目主页：https://github.com/hotsauce000/fund-data — 更新、反馈、支持作者
>
> A 股域有 [a-stock-data](https://github.com/simonlin1212/a-stock-data)，**基金域有 fund-data**——形态对标、纪律同源。

基金领域底层数据工具包 V0.4.1：零 key、零登录、零第三方封装，为基金诊断 / 评测 / 定投回测 / 持仓穿透 / 宏观利率分析提供底层数据原语。**105/105 分层断言实测通过（2026-11-09）**，覆盖 11 个上游接口，32 个公开函数。

**核心立场：请求预算是稀缺资源**——所有端点内置跨进程限流、风控分流、熔断器三层防封结构；所有上游坑（18 个实测）内嵌防线；宁可显式报错，绝不静默空。

---

## 特性一览

- 🔇 **零 key 零登录**：天天基金主源 + 蛋卷跨域备胎 + 中债官网 + 东财 datacenter + 腾讯行情，全部公开端点直连
- 💰 **请求预算制防封**：单基金深度诊断常态 **1 请求**（当日缓存命中 0 请求）；跨进程磁盘锁让服务/AI 会话/手跑脚本合并限速不越线
- 📦 **一站式快照**：`fund_snapshot()` 1 请求 = 净值全史/规模史/持有人结构/现任经理/重仓股+债/资产配置/**仓位史**/**同类百分位史**/**基准三曲线**/申赎状态/费率（pingzhongdata 436KB → 20+ 数据集）
- 🩺 **评测三件套（零网络）**：`position_trend` 仓位解读 / `peer_rank` 同类百分位对照 / `relative_performance` vs 沪深300·同类平均（相对收益+β+相关性）
- 📉 **真口径本地计算**：复权净值链接官方日收益率（分红/拆分已处理）；定投回测 **XIRR 分期真年化**（朴素年化仅作对照）
- 🔍 **一键持仓穿透**：`penetrate()` 权重 × 腾讯批量行情 × 板块归属 → 主题聚合 → *"这只基金主要是白酒（约 86%）"* 白话地图
- 🏦 **宏观利率层**：中债曲线族 40+ 条（1 请求 = 1 曲线 1 年日频）+ 信用利差本地算差 + SHIBOR 全史 + 基准利率调整史 + GC007 + 国债盘中

## 架构总览

```
L1 前置层（防封是一等公民，不是补丁）
├── 三线限流入口：ttfund_get / em_get / 腾讯直连
│   └── F1 跨进程磁盘锁 → F6 风控分流 → F2 熔断器 → P12 直连
├── fund_list      → 全量 2.8 万只主键表（30 天缓存，miss 单点回源）
└── norm_fund_code → 代码/名称/拼音消歧（A/C 份额族列候选绝不猜；股票代码拦截）

端点层（天天基金主源 + 蛋卷跨域备胎）
├── E2 ⭐ fund_snapshot  → 1 请求 = 90% 静态数据；挂了自动降级 E3+E4a+蛋卷
├── E3  fund_nav_history → 区间净值 + 每日申赎状态史
├── E4a fund_holdings    → 季度前十大（thead 动态列映射，上游改列序显式报错）
├── D1  fund_detail_danjuan → 蛋卷档案（唯一跨域活路）
└── E6  fund_realtime_nav → 批量最新净值（N 只 = 1 请求）

L5 本地计算层（零网络，全部可离线单测；编号承袭模块内部分区，与测试分层 L1~L5 无对应）
├── adjusted_nav → 复权净值（主链官方 equityReturn，分红/拆分已处理）
├── fund_metrics → 收益/年化/最大回撤/波动/夏普/卡玛 + 管理费率标注
├── fund_dca_backtest → 定投回测 + XIRR
├── position_trend / peer_rank / relative_performance → 评测三件套
└── holding_map  → 重仓股穿透桥（喂 a-stock-data 的纯代码清单）

L6 跨域穿透层（重仓股 → 股票域）
├── tencent_quote  → 批量实时行情（腾讯不封 IP，N 只 1 请求）
├── concept_blocks → 个股板块归属（东财 slist，当日缓存）
└── penetrate ⭐ → 主题聚合 → plain_text_map 白话地图

L7 宏观利率层（债基/理财叙事的数据地基）
├── cb_yield_curve → 中债曲线族 40+ 条（subprocess curl 绕 TLS 指纹墙）
├── credit_spread  → 信用利差本地算差（三段式报错：期限缺失 vs 日期错位）
├── treasury_yield_daily / shibor_history / benchmark_rate_history → 东财利率报表
└── repo_rate_gc / cn_treasury_realtime → GC007 代理 / 国债四期限盘中
```

## 端点路由速查

| 编号 | 函数 | 拿什么 | 请求数 |
|---|---|---|---|
| E1 | `fund_list()` | 全量清单 2.8 万只（代码/简称/类型/拼音） | 首载 1，缓存 0 |
| 前置 | `norm_fund_code(q)` / `resolve_fund_code(q)` | 归一化消歧；多候选列出不猜 / 唯一命中强取 | 0（E1 缓存） |
| E2 ⭐ | `fund_snapshot(q)` | 一站式快照（含 V0.4 新增：仓位史/同类百分位史/基准三曲线） | 1，当日缓存 0 |
| E3 | `fund_nav_history(q,s,e)` | 区间净值 + 每日申赎状态（E2 失败时净值兜底） | 1~N 页 |
| E4a | `fund_holdings(q,year)` | 季度前十大（含历史各季报） | 1，当日缓存 |
| D1 | `fund_detail_danjuan(q)` | 蛋卷档案：规模/经理/风险等级/申赎（跨域备胎） | 1 |
| E6 | `fund_realtime_nav(codes)` | 批量最新净值（≤50 只/请求自动分块） | N 只 = 1（≤50） |
| 本地 | `adjusted_nav` / `fund_metrics(s,s,e)` | 复权净值（分红再投）/ 区间全指标+费率标注 | 0 |
| 本地 | `fund_dca_backtest(s,...)` | 定投回测 + XIRR 分期真年化 | 0 |
| 本地 | `position_trend(s)` / `peer_rank(s)` | 仓位史解读（加减仓信号）/ 同类百分位（一年前对照） | 0 |
| 本地 | `relative_performance(s,基准)` | vs 沪深300/同类平均：相对收益 + β + 相关性 | 0 |
| 本地 | `holding_map(s)` | 重仓穿透桥 → 喂 a-stock-data | 0 |
| L6 | `tencent_quote(codes)` / `concept_blocks(code)` | 重仓股批量行情 / 个股板块标签 | N 只=1 / 1·当日缓存 |
| L6 ⭐ | `penetrate(q)` / `plain_text_map(p)` | 一键穿透主题聚合白话地图 | 冷启≤13，当日重跑 1 |
| L7 | `cb_yield_curve(c,year)` / `cb_curve_series` / `credit_spread` | 中债曲线族年文件 / 单期限序列 / 信用利差 | 1/曲线/年（利差=2），当日缓存 |
| L7 | `treasury_yield_daily` / `shibor_history` / `benchmark_rate_history` | 中美国债日频 / SHIBOR 全史 / 基准利率史 | 1·当日缓存 |
| L7 | `repo_rate_gc()` / `cn_treasury_realtime()` | GC007（DR007 代理）/ 国债 2/5/10/30Y 盘中 | 1 / 4 |
| 降级 | `fund_snapshot` 自动降级 | E2 挂 → E3+E4a+蛋卷分拆，`source` 标注 degraded | ~N+2 |

## 防封设计：请求预算契约（先读）

### 预算表

| 场景 | 预算 |
|---|---|
| 单基金深度诊断 | **常态 1 请求**，上限 2 |
| 同基金当日重复诊断 | **0 请求**（磁盘缓存命中） |
| 全市场清单 | 1 请求（30 天缓存），**明令禁止** `for fund in all: fund_snapshot(fund)` |
| 批量实时净值 | N 只 = 1 请求（≤50，自动分块） |
| 重仓穿透 | 冷启 ≤13；当日重跑 1 |
| 利率曲线 | 1 请求 = 1 曲线 1 年日频；当日缓存 0 |

### 四层防线（对 a-stock-data 单进程 `em_get` 的结构升级）

| 防线 | 机制 |
|---|---|
| **F1 跨进程磁盘锁** | 限流时间戳落盘 + `flock`——licai-map 服务 / AI 会话 / 手跑脚本多进程并发时合并速率不越线 |
| **F6 风控分流** | 403/429 → `RiskControlError`（非 `RequestException` 子类），**绝不触发降级风暴**——原实现 403 被误当网络故障 → 自动降级 → 37 页连发打风控枪口 |
| **F2 熔断器** | 连续 3 次风控信号 → 落盘熔断 10 分钟，冷却期内**零网络**直接拒绝；成功响应清零连击；tt/em 两线独立 |
| **P12 直连** | tt/em 两线 Session `trust_env=False` + 腾讯 urllib 空 `ProxyHandler`——全直连绕过 macOS 系统代理（VPN/Clash 转发国内金融站只引入抖动，实测 SSL EOF 间歇失败，curl 直连稳）。企业代理刚需网络需改回 `True` |

### 东财风控阈值（社区实测）

| 行为 | 阈值 | 后果 |
|---|---|---|
| 每秒请求数 | > 5 次/秒 | 高危 |
| 单 IP 并发 | ≥ 10 | 高危 |
| 5 分钟请求总数 | ≥ 300 | **触发封禁** |

> **真实案例**（a-stock-data #36）：10 线程并发 1 小时 45000+ 请求 → `push2` 全系 `RemoteDisconnected`，**IP 级封禁 20+ 小时**。`em_get` 默认间隔下 1 小时最多约 3000 次，与踩坑者差一个数量级——这就是"限流是铁律"的实证。

## 快速上手

```bash
pip install requests   # 唯一 Python 依赖（L7 中债通道需系统 curl，macOS/Linux 自带）
```

```python
from fund_data import (fund_snapshot, fund_metrics, fund_dca_backtest,
                       position_trend, peer_rank, relative_performance,
                       penetrate, plain_text_map, cb_yield_curve, credit_spread)

# ── 场景 1：单基金深度诊断（常态 1 请求）──
snap = fund_snapshot("161725")                 # 或传名称 "招商中证白酒"
m = fund_metrics(snap)                         # 收益/回撤/波动/夏普/卡玛 + 管理费率
dca = fund_dca_backtest(snap, amount=1000)     # 定投回测：XIRR 分期真年化
print(m["total_return_pct"], m["max_drawdown_pct"], dca["irr_xirr_pct"])

# ── 场景 2：评测三件套（零网络，全部吃 snapshot）──
position_trend(snap)                           # 仓位水平 + 加减仓信号
peer_rank(snap)                                # 同类百分位 + 一年前对照
rp = relative_performance(snap, benchmark="沪深300")   # 相对收益 + β + 相关性
rp2 = relative_performance(snap, benchmark="同类平均")  # 相对同类平均超额

# ── 场景 3：持仓穿透白话地图 ──
print(plain_text_map(penetrate("161725")))
# → 某次实测示意（随最新季报浮动）：
# 《招商中证白酒里装了什么》（2026Q3 季报，前十大占净值 86%）
#   一句话：这只基金主要是白酒（相关持仓权重约 86%）……

# ── 场景 4：宏观利率（债基敏感度 / 理财波动）──
cb_yield_curve("treasury", year=2026)          # 中债国债曲线全年日频
credit_spread("AA", "3y")[-1]                  # 3Y AA 信用利差
shibor_history("1年(1Y)", days=5)              # SHIBOR 1Y

# ── 场景 5：名字消歧（A/C 份额族 → 列候选不猜）──
from fund_data import norm_fund_code
norm_fund_code("华夏成长")                     # matches: 000001/024808/... resolved=None
```

使用方式为 import 模块（`fund_data.py` 无命令行入口）。

## 坑区速查（18 个实测坑，防线已内嵌）

| # | 坑 | 防线 |
|---|---|---|
| P1 | pingzhongdata 走 HTTP **静默返 0 字节**（200 照常） | `ttfund_get` 强制升级 HTTPS |
| P2 | lsjz 三连：pageSize 服务端钳 20；TotalCount 在顶层；无区间行为不稳 | 固定 20 + 顶层读取 + 实收强校验 |
| P3 | E2 变量按基金类型选择性存在 | `missing_datasets` 显式列出 |
| P3b | 值后挂 `;/*注释*/var`；`stockCodesNew` 是 secid 数组 | 相邻 var 切片 + 剥注释 + 剥前缀 |
| P4 | 基金/股票代码同段碰撞（000001） | 不在基金清单 → 报错引导 a-stock-data |
| P5 | fundgz 盘中估值已下线；蛋卷 growth 不可用 | 净值无跨域备胎，诚实报缺 |
| P6 | unitMoney 分红金额被上游剥离、拆分混入 | 复权主链官方 equityReturn，unitMoney 仅回退 |
| P7 | E3 新→旧 vs E2 旧→新 | 降级反转 + 指标入口升序断言双防线 |
| P8~P10 | 中债 searchYc 参数黑洞 / xlsx inlineStr / 东财 datacenter 臆测报表名全报 9501 错误 | 只走下载端点 / zipfile+regex / 从页面 bundle 逆向 |
| P11 | **中债 TLS 指纹墙**掐 Python 客户端（requests 稳挂 curl 稳过） | `_cb_request` 走 subprocess curl，同入限流账本 |
| P12 | macOS 系统代理转发国内站间歇 SSL EOF | 三线全直连（tt/em Session `trust_env=False` + 腾讯 no-proxy opener，见上防封表） |
| P13 | 多进程各持内存限流器 = 合并速率×进程数 | F1 磁盘锁三线 |
| P14 | 403 被误捕 → 降级 → 37 页打风控枪口 | F6 分流 + F2 熔断 |
| P15 | E6 对货基字段语义漂移（NAV=每万份收益） | 无货基专项；`is_money_fund` 标注防误用 |
| P16 | QDII 净值 T+1/T+2 日历差 vs 252 交易日假设 | 输出解读时标注 |
| P17 | 跨域净值备胎三候选实测全死（蛋卷 growth/新浪 HTML 壳/新浪 openapi） | 诚实边界：东财域整体被封时净值无备胎 |
| P18 | E2 表格双形态：行式 `{y,mom}` 值全丢；`[[ts,v]]` 时序对被当表头 | 双形态分流 + 时序专用通道（V0.4.0 修复） |

## 测试与质量

```bash
python3 test_fund_data.py --offline-only   # 65 条单元断言，零网络，秒级
python3 test_fund_data.py                  # 全链路 105 条（真数据，约 3 分钟）
```

- **分层结构**：L1 单元 65 · L2 端点 smoke 21 · L3 三源交叉验证 5 · L4 请求预算审计 2 · L5 降级演练 7 · 防封专项 F×5
- **交叉验证**（L3）：E2↔E3↔E6 净值互证、E2↔E4a 持仓交集、本地复权收益 vs 官方 `syl_1n` 容差 0.6pct、中债↔东财↔push2 十年期国债三源（同口径近乎一致，断言容差 0.05/0.25pct）
- **预算审计**（L4）：每次跑完打印三线请求账本（基金域东财/股票域东财/腾讯，分 host 计数）
- **独立审查**：每个版本经 code-reviewer 独立审查闭环（V0.4.1 经三轮：初审 APPROVE → 建议落地 → 双通道复核 APPROVE）

部署形态：**三份拷贝逐字节同步**（本仓库 / `~/.agents/skills/fund-data/` / licai-map 服务端），由 `build_skill_md.py` 从模块自动组装 SKILL.md 保证文档内嵌代码与源码一致。

## 版本历史

- **V0.4.1（2026-11-09）审计修复**：P-C 持仓动态列映射（防"文案在列位变"静默错列）、P-A 穿透名字零请求（重跑 2→1）、P-B~P-E、复核遗留清零（105/105）
- **V0.4.0（2026-11-09）正确性欠账 + 在手数据接线**：P18 归一化双 bug（规模史/仓位史/同类百分位史从乱码恢复）、XIRR 兑现、基准三曲线、评测三件套（89/89）
- **V0.3.1（2026-11-06）防封加固**：F1 跨进程锁 / F6 风控分流 / F2 熔断 / F3 降级窗口 3年→1年 / P12 直连（70/70）
- **V0.3.0**：L7 宏观利率层（中债曲线族 + 利率报表 + GC007；三源互证 10Y 近乎一致）
- **V0.2.0**：L6 跨域穿透层（`penetrate()` 白话地图）
- **V0.1.x**：端点层 + 本地计算层 + 降级链（E2 主端点 / E3 / E4a / D1 / E6）

## 与 a-stock-data / ttskill 的关系

| 需求 | 用谁 |
|---|---|
| 个股深度（龙虎榜/估值历史/资金流/财报三表/研报） | **[a-stock-data](https://github.com/simonlin1212/a-stock-data)** |
| 基金域数据底座（本仓库）：净值/持仓/规模/经理/利率/穿透 | **fund-data** |
| 语义主题·事件选基 / 账户持仓收益 / 交易执行 / 组合回测（需登录态） | **ttskill**（天天基金官方 CLI） |

`holding_map()` 是 fund-data → a-stock-data 的官方桥：把重仓股整理成纯 6 位代码清单直接喂过去；L6 内嵌了腾讯行情+板块两条最常用通道，不装 a-stock-data 也能跑通穿透。

## 边界（明确不覆盖）

账户持仓与交易下单（ttskill 领地）、港基专项、盘中实时估值（官方 fundgz 已下线；盘中估算本地推演在路线图）、货币基金专项（字段语义漂移仅做防误用标注，见 P15）、语义主题/事件选基（ttskill 语义引擎，公开侧无等价物）、ETF 一级市场 PCF、债券持仓明细（仅定期报告 PDF 有）。

## 仓库结构

```
fund_data.py        # 核心模块（可直接 import；32 个公开函数）
test_fund_data.py   # 105 条分层回归测试（支持 --offline-only 离线跑）
build_skill_md.py   # 从模块自动组装 SKILL.md（内嵌代码逐字节一致）
SKILL.md            # Agent skill 文档（build 产物）：防封铁律/坑区/路由表/全量内嵌代码
PLAN-V0.4.0.md      # V0.4.0 规划与执行记录（含一手探测数据）
licai-map/          # R9 复刻应用（server/fund_data.py 为本模块同步副本）
archive/            # 已并入主模块的原型存档
```

> 另含 评测skill 子工具集（`评测skill.py` 等，指数增强基金评判，见 `SKILL_评测skill.md`）；上表仅列核心文件。

安装为 Agent skill：将 `SKILL.md`（及附带模块）放入 `~/.agents/skills/fund-data/`。

## License

MIT
