# fund-data

基金领域底层数据工具包（V0.4.1）——对标 [a-stock-data](https://github.com/simonlin1212/a-stock-data) 的形态，为基金诊断/筛选/定投回测等应用层提供零 key、零登录的底层数据原语。

天天基金主源 + 蛋卷跨域备胎 + 本地计算 + L6 跨域穿透 + L7 宏观利率层。**105/105 分层断言实测通过（2026-11-09）**，覆盖 11 个上游接口（探测期另发现 fundgz/蛋卷 growth 等已下线端点，见坑区 P5）。

**V0.4.1（2026-11-09）：审计修复——P-C 持仓动态列映射（防静默错列）、P-A 穿透名字零请求（重跑 2→1）、P-D/P-E 宏观层缓存与报错、复核遗留清零（+16 断言）**

**V0.4.0（2026-11-09）：正确性欠账清偿 + 在手数据接线**

- 🔴 **P18 归一化双 bug 修复**：E2 行式 `{y,mom}`（规模史）值全丢、`[[ts,v]]` 时序对（仓位/同类百分位）被当表头——三组数据从乱码/光杆行恢复真实值
- 🔴 **XIRR 兑现**：`fund_dca_backtest` 分期投入真年化从注释承诺到真值（二分法，无解显式 None）
- 🟢 **基准三曲线接线**：`snapshot.benchmark_curves`（本基金/同类平均/沪深300，0 新请求）
- 🟢 **评测函数三件套**：`position_trend`（仓位+加减仓）/ `peer_rank`（同类百分位+一年前对照）/ `relative_performance`（相对收益+β+相关性）；`fund_metrics` 附管理费率
- 坑 P15~P17：E6 货基字段语义漂移 / QDII 日历差 / 跨域净值备胎三候选实测全死

## 核心设计

### 请求预算制防封（第一约束）

| 场景 | 预算 |
|---|---|
| 单基金深度诊断 | **常态 1 请求**（pingzhongdata 436KB → 20+ 数据集），上限 2 |
| 同基金当日重复诊断 | **0 请求**（磁盘缓存命中） |
| 全市场筛选 | 只允许列表端点翻页（1 请求 = 50 只全字段） |
| 批量实时净值 | N 只 = 1 请求（移动端 FCodes 批量） |
| 重仓穿透（L6） | 冷启动 ≤13；当日重跑 1 请求（持仓+板块全缓存命中，仅腾讯行情现查；名字走 fund_list 零请求） |
| 利率曲线（L7） | 1 请求=1 条曲线 1 年日频；当日缓存命中 0 请求 |

所有请求走 `ttfund_get()` 统一入口：强制 HTTPS + 串行限流（1.5s+抖动）+ 会话复用 + 瞬态重试 + 请求计数（预算审计）。

### 宁可显式报错，绝不静默空

实测抓到并内嵌防线的 8 个上游坑（P3b 为 P3 变体；详见 SKILL.md 坑区）：

- **P1** pingzhongdata 走 HTTP 静默返 0 字节（200 照常，UA 无关）
- **P2** lsjz 三连坑：pageSize 服务端钳制 20（49~200 只回 20，500 静默 0）；TotalCount 在响应顶层不在 Data；无区间行为不稳
- **P3** E2 变量按基金类型选择性存在 → `missing_datasets` 显式列出
- **P3b** 值后挂 `;/*注释*/var` 吞后文；`stockCodesNew` 是 secid 数组
- **P4** 基金/股票代码同段碰撞（000001）→ 不在清单即报错引导 a-stock-data
- **P5** fundgz 盘中估值已下线；蛋卷 growth 不可用 → 净值无跨域备胎，诚实报缺（P17 三候选复测仍死）
- **P6** unitMoney 金额被上游剥离、拆分事件混入 → 复权引擎改链接官方 equityReturn
- **P7** E3 新→旧 vs E2 旧→新 → 降级快照必须反转，指标入口升序断言双防线
- **P15~P18**（V0.4.0）E6 货基字段语义漂移 / QDII 日历差 / 跨域净值备胎不可得 / E2 表格双形态归一化（规模史·仓位史·同类百分位修复）

## 安装与使用

```bash
pip install requests   # 唯一硬依赖
```

```python
from fund_data import fund_snapshot, fund_metrics, fund_dca_backtest

snap = fund_snapshot("161725")          # 或传名称 "招商中证白酒"
m = fund_metrics(snap)                  # 复权口径：收益/回撤/波动/夏普/卡玛
dca = fund_dca_backtest(snap, amount=1000)
```

## 功能一览（27 函数 / 11 上游接口）

| 层 | 函数 | 说明 |
|---|---|---|
| 前置 | `fund_list()` / `norm_fund_code()` / `resolve_fund_code()` / `ttfund_get()` | 2.8万只主键表（缓存30天）/ 归一化+歧义拦截（多候选列出不猜）/ 名称→代码强报错版 / 限流入口 |
| 端点 | `fund_snapshot()` ⭐ | E2 一站式：净值全史/规模/持有人/经理/重仓股+债/资产配置/排名/申赎/费率；E2 挂自动降级 E3+E4a+蛋卷 |
| 端点 | `fund_nav_history()` | E3 补充端点：区间净值 + 每日申赎状态 |
| 端点 | `fund_holdings()` | E4a 季度前十大（含历史季报） |
| 端点 | `fund_detail_danjuan()` | 蛋卷跨域备胎（档案+持仓） |
| 端点 | `fund_realtime_nav()` | E6 批量最新净值 |
| 本地 | `adjusted_nav()` / `fund_metrics()` / `fund_dca_backtest()` | 复权净值（官方收益率链式）/ 全指标 / 定投回测 |
| 本地 | `holding_map()` | 重仓股穿透桥（零网络视图，深度分析对接 a-stock-data） |
| L6 | `tencent_quote()` / `concept_blocks()` | 重仓股批量实时行情（腾讯，N只1请求）/ 个股板块标签（当日缓存） |
| L6 ⭐ | `penetrate()` / `plain_text_map()` | 一键重仓穿透：权重×行情×板块 → 共现加权主题聚合 → 白话地图（"这只基金主要是白酒约86%"） |
| L7 | `cb_yield_curve()` / `cb_curve_series()` / `credit_spread()` | 中债曲线族（40+条：国债/企业债AAA~AA-/存单全评级/城投）年文件·期限序列·信用利差本地算差 |
| L7 | `treasury_yield_daily()` / `shibor_history()` / `benchmark_rate_history()` | 中美国债日频+期限利差 / SHIBOR全史 / 基准利率调整史（附次日沪深涨跌） |
| L7 | `repo_rate_gc()` / `cn_treasury_realtime()` | GC007 回购（DR007 交易所代理）/ 国债 2/5/10/30Y 盘中实时 |

## 测试

```bash
python3 test_fund_data.py --offline-only   # L1 单元：34 条，零网络
python3 test_fund_data.py                  # 全链路 65 条（真数据，约 3 分钟）
```

测试含三源交叉验证（E2↔E3↔E6 净值 4 位小数一致、E2↔E4a 持仓交集 10/10、本地复权收益 vs 官方 syl_1n）、请求预算审计、降级演练。

## 仓库结构

```
fund_data.py        # 核心模块（可直接 import，含 L6 穿透层）
test_fund_data.py   # 65 条分层回归测试
build_skill_md.py   # 从模块自动组装 SKILL.md（保证内嵌代码逐字节一致，双写仓库+安装目录）
SKILL.md            # Agent skill 文档（build 产物）：防封铁律/坑区/路由表/全量内嵌代码
archive/            # 已并入主模块的原型存档
licai-replica/      # R9 参考页面存档（复刻对齐用素材，非本 skill 代码）
```

安装为 Agent skill：将 `SKILL.md`（及附带模块）放入 `~/.agents/skills/fund-data/`。

## 路线图

- **V0.3.0（已交付）**：L7 宏观利率层——中债曲线族 40+ 条 + SHIBOR + 基准利率史 + GC007 + 国债盘中（债基/理财叙事数据地基；三源互证 10Y 分毫不差）
- **V0.2.0（已交付）**：L6 跨域穿透层——`penetrate()` 一键重仓白话地图（R9 场景 04 数据层就位）
- **下一版本**：全市场排名筛选（rankhandler）/ 货币基金专项（万份收益·7日年化）/ 历任经理切分（F10 jjjl）/ 债券持仓明细（zqcc）
- **再下一版**：盘中估算本地推演（替代已下线的 fundgz，持仓×实时行情）/ 回撤环境上下文（对接指数估值分位）

不覆盖：账户持仓与交易下单（ttskill 领地）、港基、ETF 一级市场 PCF。

## License

MIT
