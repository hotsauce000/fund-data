# PLAN-V0.8.2：申万行业指数层（官方源直连 · 指数族 + 官方估值）

> 状态：**计划定稿（审查修复后），未落码**｜起草 2026-11-24｜基线 V0.8.1（b53d18f 已推 GitHub；SkillHub 已发 0.8.1）
> 触发：调研 AkShare 非股票覆盖面 → 用户点名补「申万行业指数族/指数估值」；行业基金对标从「东财 BK 板块（单源、无估值）」升级为「申万官方口径 + 官方估值」

## ⓪ 探针与源码实证

**预算口径（单一计数单位=sws 线真实网络发，握手/拉源码不计发）**：总预算 ≤9 发；已花 3 发（探针①②③）；待发 4 发（⑥⑦⑧⑨，见第四节）；**9−3−4=余 2 发缓冲**。（④⑤ 两号已用于本轮前两项不计发实证：表中第 4 行 openssl 握手与「—」行源码拉取）

| # | 实验 | 结果 |
|---|---|---|
| 1 | 估值报表 `index_analysis/index_analysis_report/`（一级行业，2026-11-20 单日，Chrome UA，curl） | ✅ **一发命中**：count=31，字段实值（农林牧渔 PE 84.38/PB 2.36/股息率 1.14/收盘 2459.88；基础化工 PE 20.08；钢铁 PB 0.86）——官方估值可用性实锤 |
| 2 | 清单 `index_publish/current/`（indextype=一级行业） | ❌ curl 报 `unable to get local issuer certificate`（证书链问题，非封锁） |
| 3 | 同上（requests 栈） | ❌ 同 `SSLCertVerificationError`——**与栈无关，是服务器证书链缺陷** |
| 4 | `openssl s_client` 握手检查 | **实锤**：服务器只发叶证书 `CN=*.swsresearch.com`，**漏发中间 CA**（issuer=GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1）；`verify return code: 21`。负载均衡节点配置不一——探针 1 能过即有健康节点存在 |
| — | AkShare 源码（index_research_sw.py，jsdelivr 镜像拉取 23353B，不计发） | 官方通道全实现拿到；其应对=`session.verify=False`（源码注释自认「只对这个会话关闭证书校验」）——**我们不接受关校验**（P12 直连立场 + 中间人风险），改自组 CA bundle（见改动 6） |

**AkShare 官方通道接口形态（源码实锤，非推断）：**
| 接口 | 参数 | 返回字段（原始键） |
|---|---|---|
| `GET /institute-sw/api/index_publish/current/` | `page/page_size=50/indextype∈{市场表征,一级行业,二级行业,风格指数,大类风格指数,金创指数}`，响应 `data.count + data.results[]` | swindexcode, swindexname, l3昨收,l4今开,l5成交额,l6最高,l7最低,l8最新,l11成交量 |
| `GET /institute-sw/api/index_publish/trend/` | `swindexcode=801030&period=DAY/WEEK/MONTH`（无日期参数，返回全史） | swindexcode, bargaindate, openindex, maxindex, minindex, closeindex, hike, markup, bargainamount, bargainsum |
| `GET /institute-sw/api/index_publish/details/component_stocks/` | `swindexcode&page=1&page_size=10000`（单发拿全） | stockcode, stockname, newweight, beginningdate |
| `GET /institute-sw/api/index_analysis/index_analysis_report/` | `page/page_size=50/index_type∈{市场表征,一级行业,二级行业,风格指数}&start_date/end_date(YYYY-MM-DD)&type=DAY&swindexcode=all或单代码`，翻页 `data.count` | swindexcode, swindexname, bargaindate, closeindex, bargainamount, markup, turnoverrate, **pe, pb**, meanprice, bargainsumrate, negotiablessharesum1/2, **dp(股息率)** |
| `GET /institute-sw/api/index_analysis/week_month_datetime/` | `type=WEEK/MONTH` → 报表可用日期序列 | bargaindate[] |

**AkShare 实现弱点清单（本版要修掉的）：** ①`verify=False` 关校验（红线冲突）②空数据静默返回空 DF（违背 fail-loud）③固定 page_size=50 逐页翻页烧请求数 ④无业务 code/结构校验 ⑤无风控与软封识别 ⑥trend 无日期参数（全史一把梭——反而省请求，照抄）

## 一、背景与目标

1. 现状缺口：fund_data 行业基金对标基准=东财 BK 板块指数（125 项 BENCH_SECID 中的 100 个 BK，EM 单源无备胎、无估值、非申万口径）；文档已声明「申万口径走 a-stock-data skill」——但 a-stock-data 同样没有申万官方估值，且跨 skill 取数违背用户「一站式底座」期待
2. 申万宏源官网（swsresearch.com）官方发布：31 个一级行业 + ~134 个二级行业 + 风格指数族 + **官方日度估值报表（PE/PB/股息率/换手率/流通市值）**——行业基金对标与估值的权威第一手
3. 目标：新增**申万行业指数层**（sws 线），四公开入口：清单 / 日线全史 / 估值序列 / 成分股；口径声明从「走 a-stock-data」改为「本 skill 直连官方」

## 二、防线设计（sws 线独立 provider）

### 改动 1：`_sws_request` 通道 + provider 策略
- 策略：`ProviderPolicy(max_requests=120, window_seconds=300, min_interval=1.0, max_in_flight=1, lease_seconds=180)`——与 core DEFAULT_POLICIES 两处同值 + 双向锁测试（V0.8.1 惯例）；`_HOST_PROVIDERS` 加 `swsresearch.com→sws`，别名 `sw/申万`
- 超时分层（csindex 成对设计照搬）：清单/成分 30s、trend 全史与估值大区间 60s，配 lease 180s
- 单发即弃（1 调用=1 网络命中）、独立账本 `SWS_REQUEST_COUNT`、熔断镜像键 `sws`、连接级熔断 2 连败停 1h（P26 同款，标记 `.cbreak_swconn`）、软封守卫（HTTP 200+非 JSON/空 results → 信号+RuntimeError；**业务 code 守卫待探针⑥捕获完整信封后再冻结**——上游信封是否有 code 字段未实证，照抄 csindex 会误抛）
- 403/429 → RiskControlError（F6，不重试不降级）；teardown 模式 **fail-closed 拒直连**（csindex 同款）
- UA：Chrome 浏览器 UA + `Referer: https://www.swsresearch.com/`（探针实证形态）

### 改动 6（前置依赖）：TLS 链缺陷应对——自组 CA bundle，绝不关校验
- 内置 GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1 中间证书 PEM 公开常量（DigiCert 公开发布的公共 CA 证书，无隐私问题；注释标注来源/有效期/换证提醒）
- `_sws_session()`：`certifi.where()` + 中间证书拼临时 bundle（cache 目录写一次复用），**verify=自组 bundle**（完整链校验保留）
- 兜底：若服务器换签发者致 bundle 失效 → SSLError 显式报错（文案带「申万证书链变更，更新 _SWS_EXTRA_CERT」指引），fail-loud 不静默
- 证书失败与熔断的分层（审查项，口径钉死）：①判定走异常链 `ssl.SSLCertVerificationError`（sws 线钉死走 requests transport——curl 的证书失败是 exit 60 另一形态，本线不用 curl）；②**连接级豁免 ≠ provider 级豁免**：gateway 对一切异常先 record_failure 再抛（gateway.py:243-248），证书失败照样推高 provider 级连击（3 触发 OPEN 600s）——这是证书风暴的兜底背板，**有意接受不豁免**（豁免须改 gateway，超出本期范围单列 backlog）；③本线只做连接级（2 连败停 1h 标记）对 SSLCertVerificationError 的豁免——证书问题≠IP 封锁，不应触发停机
- P34 坑区入册：症状/openssl 诊断命令/AkShare verify=False 对照/我们的 bundle 方案

### 改动 2：`sw_index_list(indextype="一级行业", force_refresh=False) -> list[dict]`
- 返回 `[{index_code:"801010", index_name:"农林牧渔", prev_close, open, latest, high, low, volume, amount}]`（l3~l11 重命名，全 float None 容错）
- `indextype` 白名单五值（市场表征/一级行业/二级行业/风格指数/金创指数）——**上游实为六值，「大类风格指数」除外**：其走另一 POST 接口（AkShare __index_realtime_sw 形态不同，get/post 双轨本期只做 GET 轨）；翻页：`page_size=200`（探针⑥验证上限，支持则二级行业 134 个一页拿全；不支持回落 50/页循环）
- 校验：count=实收数、代码 6 位数字正则、名称非空、行业清单锚（一级行业 ≥31 且含 801010 农林牧渔）；缓存 `sws/index_list_{indextype}.json` TTL 24h（盘后快照型）

### 改动 3：`sw_index_daily(index_code, period="day", force_refresh=False) -> list[dict]`
- 代码校验 `_sws_code`（6 位数字）；period 白名单 day/week/month；返回 `[{date, open, high, low, close, volume, amount, change_pct}]`
- trend 无日期参数返回全史 → **单发全史**，按年切片落盘 `sws/daily/{code}_{period}_{year}.json`——**键含 period 段防 day/week/month 三频串台**（审查项；三 period 各一发全史成本可接受，保留三频）（csindex 模式：历史年永久缓存、当前年 20h TTL + **当日行缺失 mtime 回拨 18h**（V0.8.1 刚落的模式，申万估值/行情同为盘后发布））；全量读时拼年返回 + 完整性校验（日期升序唯一、OHLC>0、close 与 markup 勾稽 ±0.5% **警告不拒**——差异于 csindex（changePct 矛盾才拒）是有意为之：申万 markup 口径未探明（探针⑦采样后决定首版先宽后紧，实测口径差如 <0.1% 则二期收紧为拒绝）
- 官方行情起始日实锚：探针⑦看 801010 最早日期（申万行业基日常见 2003-12-31，以探针⑦实测最早日期回填，不预设）；L1 补 day/week 缓存键隔离断言

### 改动 4：`sw_index_valuation(index_code, start_date="", end_date="", force_refresh=False) -> list[dict]` ★核心增量
- `index_analysis_report/` + `swindexcode=单代码` + 日期区间，单指数序列**尽量少请求**：按查询区间直接取（page_size 拉大探针⑥）；`type=DAY`，周/月暂不开（YAGNI，日度已覆盖对标需求）
- 返回 `[{date, close, pe, pb, dividend_yield, turnover_rate, float_mv, mean_price}]`
- 守卫：date 升序唯一、close>0；**pe/pb/dividend_yield 允许 None（行业亏损 PE 为空是真实口径），非 None 时 0<x<2000 / 0<x<500 / 0≤x<30**，越界 RuntimeError 拒缓存
- 成本口径：page_size=200 若成立，年度切片 ~244 行 ≈2 发/年，20 年全史回填 ~40 发（120/300s 配额内、单指数一次性成本）；缓存：按年切片 `sws/valuation/{code}_{year}.json` + 当前年 20h TTL + mtime 回拨；当年增量仿 csindex（stale 末行→起取重叠段）；缓存校验 `_sws_cache_valid`（行结构/代码/数值有限性，对照 csindex 加 bool 排除）
- 当日未发布豁免：end=今天且缺失 → 不计缺失、`pending_today` 入报告（V0.8.1 盘前模式照搬）

### 改动 5：`sw_index_components(index_code, force_refresh=False) -> list[dict]`
- 单发 page_size=10000；返回 `[{stock_code, stock_name, weight, since}]`
- 校验：results 非空（空=该指数无成分公开，显式报错不静默空）、代码 6 位、weight 数值化；权重和 95~105% 窗口警告不拒绝；缓存 `sws/components/{code}.json` TTL 30 天（季频数据）

### 改动 7：文档与版本
- 版本 0.8.1→**0.8.2**；SKILL 路由表新行（申万行业指数｜四入口｜冷启≤4/缓存0——清单/日线/估值/成分各 ≥1 发）；reference.md 新 section（内嵌逐字节）+ P34 坑区；README 计数/版本史/端点表；examples.md 场景 15（行业基金对标：申万一级行业估值+成分）；DESCRIPTION 微调（加「申万行业指数」关键词）；build 断言锚 4 条（`def sw_index_list(`/P34/`_SWS_EXTRA_CERT`/`_sws_cache_valid`）
- 口径更新：fund_data.py 行业板块注释与 reference P29 处「申万口径走 a-stock-data」→「申万官方直连见 sw_index_*；东财 BK 为备选口径」

## 三、测试计划（L1 全离线密闭 + L2 小预算在线）

| 层 | 锁什么 | 条数 |
|---|---|---|
| L1 | 通道：TLS bundle 会话 verify 非 False（读 session.verify 是 str/Path 而非 False）、软封 200 非JSON/空 results 计信号、403 分流、teardown fail-closed 零 transport、证书类 **SSLCertVerificationError 不计连接级熔断标记**（provider 级 gateway record_failure 照计数=兜底背板，改动 6②）、传输失败计连接级熔断 2 连败停机 | ~7 |
| L1 | 清单：count 锚（31 一级行业+801010）、白名单拒非法 indextype、缓存二调 0 请求 | ~3 |
| L1 | 日线：全史单发、按年切片读写、当前年 20h 过期增量 + mtime 回拨、OHLC 校验拒脏值、**day/week 缓存键隔离**（同代码双 period 不互读） | ~5 |
| L1 | 估值：区间查询、pe=None 容错（亏损行业口径）+越界拒缓存、按年缓存/增量缝合、pending_today 豁免、损坏缓存 miss 重取 | ~5 |
| L1 | 成分：单发、空 results 显式报错、权重和警告 | ~2 |
| L1 | 边界：period 非法值拒绝、_sws_code 非法代码拒绝（daily/valuation/components 三入口）、估值 end≥start 与未来日期拒绝（csindex 先例） | ~3 |
| 策略 | core DEFAULT_POLICIES↔主模块 sws 覆盖双向锁（V0.8.1 同款）+ **_ZERO_POLICY 加 sws 行**（防离线 mock 真睡 1s） | 2 |
| L2 | 在线锚点（--online，sws 线预算 ≤6 发）：清单 31 行业实值、801010 日线首末锚、估值农林牧渔/银行 PE 实值区间、成分银行前 10 权重和>50% | 4 |
| 全量 | 离线 ≥381+22~27=~403~408（以实跑为准）；README/SKILL/reference/examples/build 计数五处核对 | — |

## 四、验证与交付顺序

1. **待发探针（4 发，sws 线独立不受 push2 熔断影响；每发多参数复用）**：
   - ⑥ 估值报表三合一：`page_size=200` 上限试探 + `start_date=2003-01-01` 早锚（估值回溯深度实锚——申万行业基日常见 2003-12-31，取更早锚防低估）+ `index_type=一级行业&swindexcode=all` 全量信封捕获（**含软封守卫冻结所需的信封形状**——是否存在业务 code 字段）+ 顺带采样疑似亏损行业（如房地产）确认负 PE/None 口径
   - ⑦ `801010 trend/` 全史：最早日期（行情起始锚）+ OHLCV 字段实值 + markup vs hike 差异采样（勾稽容差冻结依据）
   - ⑧ 成分 `801030 component_stocks/`：实值 + 实值 + float_mv/市值单位旁证；成交单位旁证见探针⑦
   - ⑨（视⑥结果）page_size 回落验证
2. 落码：改动 6（bundle）→ 改动 1（通道/策略）→ 2→3→4→5（依赖序）→ 7（文档）
3. 离线全绿（381+22~27=~403~408）→ 在线全量（离线 + 在线 91 + 新在线 4，以实跑为准）
4. 本地部署副本 cp（fund_data.py + fund_data_core）+ 本地 skill 部署目录 build 同步
5. 审查链 APPROVE → 本地 commit → **停：推 GitHub / 发 SkillHub 等用户明确指令**（发布红线）
6. 观察项：swsresearch 负载均衡健康节点比例（证书失败率）；估值报表当日发布时点（决定 mtime 回拨值是否 18h 合适）

## 五、风险与边界（如实）

- TLS 链缺陷是服务器配置 bug：自组 bundle 后**不再依赖节点运气**；但站点换 CA 时 bundle 失效 → 显式报错 + P34 指引（中间证书有效期至 2032，风险低）
- 估值报表覆盖起始日未探明（报表可能只回 N 年）→ 探针⑥早锚/实锚后如实写入文档，不承诺全史
- trend 无日期参数=每次全史：首取后按年缓存，日常成本仅当前年增量一发；但若官方改参数则跟随
- 覆盖范围=GET 轨五类 indextype（清单接口自然覆盖，不硬编码行业清单）；「大类风格指数」POST 轨留 backlog
- push2 桶当日已烧空（连接级熔断冷却中）不影响本层（swsresearch.com 独立域独立账本）；跨域影响为零已由域名隔离保证
- 与下游消费方影响隔离：新增层不动既有接口；本地部署副本照常 cp

## 六、关联

- 坑区：P34 新增（TLS 链漏中间证书）；P26（诊断烧 IP 教训——本计划探针总预算 ≤9 发，已花 3 发，另 2 项实证［openssl 握手/源码拉取］不计发）；P31/P32（探针先行的惯例沿例）；P33（csindex 层先例——按年缓存/当日豁免/mtime 回拨全部复用）
- 通道先例：中债 curl 通道（P11 TLS 墙）、csindex 业务 code 校验与 teardown fail-closed
- AkShare 对照：index_research_sw.py（官方通道实现参考，verify=False 与弱点清单为本版反面教材）；乐咕通道不采用（聚合站+token 反爬）
- SkillHub：0.8.2 发布等指令（0.8.1 已上线 skillId=314627）

## 附录 A：上游键 → 契约字段映射（冻结依据）

| 上游键 | 契约字段 | 备注 |
|---|---|---|
| swindexcode/swindexname | index_code/index_name | 全接口通用 |
| **bargaindate** | **date** | 日线/估值通用（ISO 化） |
| l3/l4/l8/l6/l7 | prev_close/open/latest/high/low | 清单 |
| l11/l5 | volume/amount | 清单；单位以探针⑦（trend 同族成交字段 bargainamount/bargainsum）旁证 |
| openindex/maxindex/minindex/closeindex | open/high/low/close | 日线 |
| bargainamount/bargainsum | volume/amount | 日线 |
| **markup** | change_pct | **选 markup 弃 hike**（hike 在 AkShare 映射列为空名，口径存疑；探针⑦采样两者差异后冻结勾稽容差） |
| closeindex/bargainamount/turnoverrate/pe/pb/meanprice/dp | close/volume→不取/turnover_rate/pe/pb/mean_price/dividend_yield | 估值 |
| negotiablessharesum**1** | float_mv | sum1=流通市值合计；**单位（亿元/万元）探针⑥实测后定**；sum2（平均）不取 |
| stockcode/stockname/newweight/beginningdate | stock_code/stock_name/weight/since | 成分 |

> 估值 report 含 pending_today 指 **`sws/reports/{code}_latest.json` 缓存文件**（消费方式同 csindex），非返回值——返回值恒为 list[dict]。

## 附录 B：公开接口契约（冻结用）

```python
sw_index_list(indextype: str = "一级行业", force_refresh: bool = False) -> list[dict]
    # {index_code, index_name, prev_close, open, latest, high, low, volume, amount}
sw_index_daily(index_code, period: str = "day", force_refresh: bool = False) -> list[dict]
    # {date, open, high, low, close, volume, amount, change_pct}  升序全史
sw_index_valuation(index_code, start_date: str = "", end_date: str = "",
                   force_refresh: bool = False) -> list[dict]
    # {date, close, pe, pb, dividend_yield, turnover_rate, float_mv, mean_price}
    # pe/pb/dividend_yield 允许 None（亏损行业口径——探针⑥实测负值/None 后冻结）；report 见附录 A
sw_index_components(index_code, force_refresh: bool = False) -> list[dict]
    # {stock_code, stock_name, weight, since}
```
