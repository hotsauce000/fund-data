# fund-data 完整参考（reference.md · 按需读取层）V0.8.4

> 本文件按需读取：调试 / 扩展 / 排查坑时才打开，勿随 skill 激活预加载。
> 路由与签名速查见 SKILL.md；本文件内嵌代码与 fund_data.py 同源直拼（构建时生成）。

**V0.4.2（2026-11-10）文档架构：渐进加载**——SKILL.md 瘦身为路由层（上限 12KB 内 ≈3K token，激活省 ~90%），全量参考移本文件；函数签名速查改 ast 自动生成（杜绝"README 改了模板没改"漂移）；credit_spread 测试锚 0.6 防方向颠倒。

# fund-data 基金底层数据工具包 V0.8.4 模块全档

天天基金主源 + 蛋卷跨域备胎 + 本地计算层 + 跨域穿透层 + 宏观利率层 + **全市场数据层 + 中证官方全收益指数层 + 申万行业指数层 + 公募REITs层**。**Sprint 1 错误语义加固 + Sprint 2/3 协调与缓存核心接入 + V0.8.0 全收益指数 + V0.8.1 审查修复 + V0.8.2 申万行业指数 + V0.8.3 诊断修复 + V0.8.4 公募REITs：551/551 离线断言通过**（L1 单元 515 · S2 协调器集成 21 · S3 缓存集成 15；另 `fund_data_core` 独立测试 97 条。在线全量须显式 `--online`），覆盖 34 个上游接口。同目录 `fund_data.py`、`fund_data_core/`、`examples.md` 与 `test_fund_data.py`；`fund_data.py` 与本文件内嵌代码逐字节一致。

**V0.8.4 公募REITs层（腾讯基础数据 → 询问 → AI 直读报告，两段式）：**
- 🟢 **`reit_board()`**：全市场行情板——腾讯行情串一次批量（103 只实测单请求）取回现价/涨跌/开高低/量额/换手/总市值 + **单位净值 + 折溢价率**；默认只发 1 请求、不逐只补净值日期，`with_nav_date=True` 才逐只补（1 请求/只）
- 🟢 **`reit_profile(code)` ★**：单只画像 4 请求（实时概览/净值历史/分红历史/不复权日线）——基本信息（只暴露核实过的腾讯字段）、半年频净值与净值日期、净值种类（`issue_price` 新上市净值是发行价 / `report` 定期报告份额净值）、折溢价、分红史与近 12 个月派息率（没有分红返回 None 不返回 0）、分红再投资收益（复用 `fund_metrics`）、对 10Y 国债利差（默认只读缓存，零东财请求）
- 🟢 **`reit_report_text(code, kind, year, pages)`**：第二段——定期报告逐页文本（pypdf 布局模式）。先返回页码索引 + 关键词页码（**只定位、不抽值**）+ 交叉核对值，再按页取正文（单次 ≤6 万字符）；收入/净利润/现金流/可供分配金额/经营指标/持有人结构/评估值全部由 AI 读原文，库不正则、不抽值、不缓存读数
- 🔴 **P36 东财净值对 REIT 停在发行价**：公募 REIT 不按日披露份额净值（半年频），E2/E6 净值恒为发行价（180101 实测 2.31@2021）——`qdii_premium` 据此算出约 -40% 的假溢价，现拒绝 REIT 并指路；`fund_metrics` / `fund_dca_backtest` 的报错分支对 REIT 追加指路文案（普通基金文案不变）
- 🔴 **防封优先**：逐只补齐按 ≥1 发/秒慢节拍串行（叠在协调器 0.2s 腾讯地板之上）、连续 2 次取数失败即中止、已取到的留当日缓存断点续跑；403/429/熔断/配额背压立即上抛不继续打；实时概览 5 分钟微缓存防同会话连打；国债默认只读缓存
- 🟡 腾讯前复权是减法复权（除息前 qfq = 不复权 − 分红），分红再投资用不复权收盘自算，`_tencent_kline_page` 增 `fq` 参数（默认行为不变）；`finance.qq.com` 归腾讯线白名单；单页抽取失败按空页记警告（真实报告常夹空白页）；资产类型判定与经营指标结构化不在本版（AI 读报告）
- 🟠 **独立审查修复**：逐只补齐对熔断闸门拒绝（`CircuitOpenError`，与 `RiskControlError` 平级而非父子）即时上抛；`fund_metrics`/`fund_dca_backtest` 的 REIT 指路只看进程内清单（`_is_reit(allow_load=False)`），L5 零网络层不触发加载；`nav_stale` 改按披露截止判定（年报截止 3 月末、中报截止 8 月末各留 10 天；固定 212 天阈值在每年年报季与 8 月误报）；画像 `requests` 含清单冷加载；离线夹具日期全部相对今天，23 个边界日期时间旅行回归通过。审查建议项同批落地：报告文本缓存先查后下载（带抽取方式标记，PDF 被清也不多花东财请求）；REIT 识别号段命中即判（清单类型串改名不失效）；逐只补齐 `force_refresh` 生效、中止后余下行先用缓存、连接层残包异常归类不炸穿；日线空返回不缓存；交叉核对按报告期取净值点；年报优先非摘要版。实读 508068 中报时又抓到一例：PDF 文字层里的求和号是孤立代理项（非法 Unicode，UTF-8 编不了），文本缓存写入失败——现于落缓存前替换为占位符并随结果告警。

**V0.8.3 全项目诊断修复：**
- 🔴 **投资结论可信**：`penetrate` 新增 `degraded`，板块取数全失败不再谎称“持仓分散”；`qdii_premium` 强制场内代码白名单（含 52 号段），杜绝同名股票行情制造假溢价；`nav_divergence` 重仓收益按覆盖度归一，低覆盖度/低股票仓位只给参考数值；`_div_from_unit_money` 兼容 E3 裸数值分红（000001 除息日实锚）
- 🟠 **通道与异常语义**：腾讯仅在业务解析成功后恢复熔断；中债 teardown curl 解析 HTTP 状态；Gateway 捕获 BaseException 结算许可，租约过期不再顶替业务异常；股票 K 线风控类型原样透传
- 🟠 **缓存与额度**：申万日线缺当前年强制刷新，历史估值区间不连坐；估值冷缓存空信封 fail-loud，只有全史抓取可写 verified_earliest；周末不制造 2h 重拉；中证陈旧交易日清单主动刷新；K 线备胎参数入缓存键
- 🟡 **可靠性工程**：PDF 魔数校验+原子写、缓存 OSError fail-loud、经理排行 JSON 解析+分页去重、REIT/商品归桶、requirements 与 CI 超时、生成器 `--check`/`--repo-only`

**V0.8.2 申万行业指数层（官方源直连，行业基金对标升级）：**
- 🟢 **`sw_index_list()`**：申万宏源官网官方清单四类 GET 轨（市场表征 9/一级行业 31/二级行业 124/风格指数 16，实测 2026-11-24），翻页拉全 count 强校验，24h 缓存；「大类风格指数/金创指数」属上游 POST 轨（backlog）
- 🟢 **`sw_index_valuation(code, start, end)` ★**：官方日度估值报表——PE/PB/股息率/换手率/流通市值（亿元），单码查询按年切片缓存（历史年永久、当前年 20h TTL + 当年 mtime 回拨 18h）、当日未发布 pending_today 豁免；pe 允许 None（亏损期），pb/股息率越界拒缓存
- 🟢 **`sw_index_daily(code, period)`**：trend 一发全史（801010 实测 6497 行、最早 1999-12-30），按年切片、day/week/month 键隔离；`sw_index_components(code)`：成分单发拿全（801030 实测 413 只）
- 🔴 **P34 实测坑（TLS 链）**：swsresearch.com 服务器漏发中间证书（GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1，AIA cacerts.digicert.cn，效期至 2032-12-14）——负载均衡部分节点全漏，重试赌节点不可靠；AkShare 用 verify=False 关校验（不可接受），本项目自组 CA bundle（certifi+中间证书 PEM 内置常量）保持**完整链校验**；证书失败显式报错不计连接级熔断（provider 级 record_failure 照计数兜底）
- 🟡 独立 sws provider 线（120/300s·1.0s·单并发·lease180，与 core 两处同值双向锁）；行业基金对标从「东财 BK 板块（无估值）」升级为申万官方口径+估值；「申万走 a-stock-data」旧口径声明作废

**V0.8.1 审查修复批（V0.8.0 全收益层双路独立审查产出：1×P1 + 5×P2 + 11×P3）：**
- 🔴 **P1 映射语义**：`csindex_resolve_total_return` 无 TRI 衍生分支误标 `return_type:"total_return"`——如实改 `price_return`（无 TR 衍生的股票指数是价格指数）；30 天旧错值 mapping 缓存读取时自愈重取；测试补锁 return_type/total_return_code
- 🟠 **元数据串台**：日线缓存按 TR 代码共享，经不同基础代码查询（000300 vs H00300）曾带回首查方的 base_index_code——出库/落盘前按本次调用方重写（行情数值本就同源无误）；损坏年度缓存（close 非数值）补回归测试锁定既有 miss-重取行为；盘前落盘的当年文件 mtime 回拨 18h（≈2h 后过期增量补当日行，不占满 20h TTL）
- 🟠 **盘前同步不炸**：当日 tradeDate 清单先行含今天而 perf 未发布 → 不计缺失不整史重取（V0.8.0 形态是按年重取后仍 UpstreamFormatError），`pending_today` 如实入报告
- 🟠 **并发与超时**：sync manifest 落盘 file_lock 重读-合并-写回 + 只保留本次窗口 job（清单有界）；gateway curl runner 超时 +10s 余量（TimeoutExpired 非 OSError 非 RuntimeError 会击穿调用方分层）；`_cb_request` 网关分支补 TimeoutExpired 分流与 `_release_pending`（LeaseError 对称）
- 🟠 **重试口径**：`_tencent_kline_page` 有状态码失败（HTTPError 是 OSError 子类）不再当瞬态重试多烧一发——V0.7.5 tencent_quote 口径补齐
- 🟡 P3 批：新浪干净空体（无 var 行）不计风控连击；sina_quote 非 str 输入先归一；中债降级值过 (0,15) 值域守卫防驻留 300s；EdgeOne cookie 剥 `; path=/` 属性段；备胎成功但短史文案如实（不再误称「备胎失败」）；净收益排除补全角（净）；pe_ttm 在线锚点（上游 peg 改名预警）；BENCH_SECID 行业映射在线抽查 3 个；纵深防御/policies 注释澄清；core 默认表↔主模块覆盖双向锁测试

**V0.8.0 中证官方全收益指数（清单 + 官方映射 + 验证全史 + 可恢复同步）：**
- 🟢 **全系列主键表 `csindex_universe()`**：官网七个系列一次建表，分页 total/实收强校验；默认人民币，资产/系列/币种筛选本地执行
- 🟢 **官方映射 `csindex_resolve_total_return()`**：衍生指数候选 + 基本资料双重验证；H00300/H20269/932000CNY010 三类特殊代码实证，不按 H+后五位猜测；无映射/未知/歧义分列
- 🟢 **历史 `csindex_total_return_daily()`**：basicDate 起全史→官方有效日期清洗→完整性/数值校验→按年缓存；占位日删除、缺失自动按年重取、当前年增量、历史年长期缓存
- 🟢 **同步 `csindex_sync_total_return()`**：codes 或 all_indices=True 二选一；逐指数 manifest、20h 内成功任务跳过、风险熔断或连续三次上游故障停止；全收益失败绝不用价格指数静默替代
- 🔴 **防封优先**：中证 120/300s·1s·单并发；180s 租约覆盖两次60秒历史尝试；teardown/legacy 直连 fail-closed；非法同步窗口零网络；所有重试独立消耗配额
- 🔴 **P33 实测坑**：非交易日起始日会复制下一交易日；全收益代码无统一规律；HTTP 200 仍需校验业务 code；个别历史行 change 与收盘差有官方口径差（2018-06-19 H00300 差0.58），change 异常入警告、changePct 与收盘明显矛盾才拒绝

**V0.7.8 基金报告（清单 + PDF 直链，场外/上市全覆盖）：**
- 🟢 **报告清单 `fund_reports(q, category, limit)`**：f10/JJGG 七类目（periodic 默认=定期报告/all/issuing/dividend/staff/sales/other，type=页面 tab 实测钉死），返回新→旧行 {report_id,title,date,year,kind(Q1~Q4/interim/annual/other),pdf_url}；固定抓满 pageSize=100 再本地切片——缓存与 limit 解耦；当日缓存；1 请求
- 🟢 **PDF 下载 `fund_report_pdf(row|url, dest_dir)`**：直链规律 `pdf.dfcfw.com/pdf/H2_{清单ID}_1.pdf`（_1 附件号不可省）；本地已存在同名文件 0 请求直返；文件名 {code}_{date}_{kind}_{ID尾6}.pdf（防年报/摘要同日撞名）
- 🔴 **EdgeOne 反爬（P32）**：裸请求 200+Content-Type 伪装 application/pdf 但 body 是 988B `<script>` 静态挑战——node vm 沙箱 eval 捕获 `__tst_status`/`EO_Bot_Ssid` 两 cookie 重放即真 PDF（沙箱无 require/fs/net，timeout 防死循环）；挑战按会话/IP 计费，解一次后同会话直连；需系统 node（同巨潮 enckey 依赖）
- 🔴 **场外基金覆盖**：巨潮 column=fund 仅上市基金（交银趋势 519702 三路全零实证）——东财清单+直链是全量唯一解；JJGG 裸 UA 是 200+ErrCode-999+Data="" 假成功形态，必须带 fundf10 Referer

**V0.7.7 申购限额（主页面交易状态栏 → 快照新数据集）：**
- 🟢 **限额/交易状态入快照**：`fund_snapshot` 新增 `purchase_limit` 数据集（kind 四态 limited/suspended/unlimited/unknown + limit_cny 折算金额 + not_for_sale 不代销标记），快照 1→2 请求（E2+主页面）；失败进 missing_datasets 不炸快照（E2 成果不陪葬）；`fund_purchase_limit(code)` 可单独调用（当日缓存）
- 🔴 **唯一来源探针证据（P31）**：E2/E3/E6 实测均无完整限额（E3 SGZT 仅「限大额」短文本无金额）——唯一来源是主页面 `{code}.html` 交易状态栏，且**无批量接口**（N 只 N 请求，tt 线 1.5s 节流 400 只约 10 分钟）
- 🔴 **嵌套 span 解析**：金额与「限大额」分属不同层级 text node（000369 实测）——`_StaticCellParser` depth 嵌套感知全文拼接，简单 regex 必截断
- 🟡 语义修正：「限大额但金额未识别」诚实返回 limit_cny=None（早期监控脚本硬给 0 会虚报不可买）；外币按静态近似汇率折 CNY（fx 参数可覆盖），精确额度看 limit_raw 原文

**V0.7.6 K线韧性与行业基准（腾讯翻页全史备胎 + 东财行业板块指数 100 个）：**
- 🟢 **腾讯翻页全史备胎**：fqkline 单页 800 上限（P25）曾被当硬边界——实测 param 第 3/4 槽支持起止日期按「页末锚」串行翻页（茅台 6044 根/沪深300 5250 根，qfq 因子跨请求 0 漂移，2026-11-17）；备胎从「800 根短窗」升级为「翻页缝合全史」，熔断期不再缩到 ~3 年
- 🔴 **缝合三防线（拒绝静默截断）**：每缝「日期+收盘」双校验（服务端 +1 重叠协议）；锚停滞/页数阀耗尽（末页满页=历史未到头）/中段空页（协议必含锚日）三异常形态未集满 lmt 即整批拒绝；集满按去重口径（+1 重叠不计入）
- 🔴 **备胎风控信号穿透**：RiskControlError/CircuitOpenError 不再被 `except Exception` 吞掉——进备胎时 EM 残行必不足 need（否则不进备胎），re-raise 无损且归因更准
- 🟢 **东财行业板块指数 100 个入表**：clist t:2 权威清单快照（2026-11-17），映射经 kline name 双样本交叉验证；`_SECID_RE` 放行 `90.BKxxxx` 直传；板块指数腾讯无码 EM 单源（与市场2 同款）；**口径声明：东财编制非申万官方 801xxx**，行业基金对标够用、申万口径走 a-stock-data
- 🟡 BENCH_SECID 19→119 指数（20→125 别名键）；未命中名称走 secid 直传兜底（表为快照会漂移）

**V0.7.5 新浪互备通道 + 行情防封加固 + 指数扩表：**
- 🟢 **`sina_quote()` 新浪批量实时行情备胎**：≤50 只/请求自动分块、输入归一去重、sh/sz 前缀强制（防裸码错路由）；provider=sina 独立分账 120/300s·0.5s·单并发；403/429 → RiskControlError 计 sina 熔断；空体拒绝静默空；债券盘初「最新」为 0 取 bid1 作 live 参考
- 🔴 **软封锁守卫（sina）**：新浪真实封禁形态=200+非空体+全空 var 行——绕过空体守卫与 403 分流；chunk 全 miss → 风控信号+fail-fast（与全无效码协议层不可分，宁可误伤不漏判——封禁前科通道）
- 🔴 **连接级熔断（sina，P26 同款）**：RST/超时/拒连连续 2 次停机 1h 零网络（无状态码形态 F2 看不见，此前中性结算配额照烧）
- 🔴 **tencent 主路补三道**：403/429 分流（此前 HTTPError 被 OSError 重试——F6「风控码绝不重试」在全库的破口）+ 风控信号链激活（策略注册但从未触发的熔断从此可用）+ 结构守卫（响应有行但 0 行解析成功=疑似字段变更，拒绝静默空；无效码不回行不在此列）；teardown 模式补 F1 地板 + F2 镜像（此前裸奔）
- 🔴 **中债 curl 通道接协调器（Sprint 3+ backlog 兑现）**：`_cb_request` 走 Gateway curl transport——chinabond 分账 120/300s·1.5s·在途≤2·3 连击熔断生效；403/429 网关层转 RiskControlError；空体计信号；`cb_curve_tree`/`cb_yield_curve` 业务校验通过显式清零（Sprint 1 口径）；teardown 走 CB_MIN_INTERVAL 独立常量（不再误用东财 TT_MIN_INTERVAL）
- 🟢 **`cn_treasury_realtime` 降级防重打**：降级结果写独立 300s 短缓存键（不污染健康缓存）——此前降级不写缓存，push2 软失败窗口每次调用重打 4 发（P26 教训：诊断行为本身烧 IP 额度）；到期自动重试主路
- 🟢 **指数查表扩容**：BENCH_SECID +上证指数/上证综指/深证成指/深证综指/上证180（19 指数/25 别名键，腾讯码实测 2026-11-17）；sh000001/sh000010 前缀路由随值域回 index；`_SH_INDEX_Q` 派生覆盖 000010 后移除显式保留

**V0.7.4 文档三层（examples.md 菜谱+FAQ 层）：**
- 🟢 **第三产物 examples.md**：12 场景复制即用菜谱（意图+代码+输出形态+坑提醒四件套）+ 16 条按现象组织的 FAQ（坑号深链 reference.md）——对症分发平台评测扣分项「示例与 FAQ 不集中、要翻较长文档」
- 🟢 **构建管线接入**：build_skill_md.py 同步第三产物并断言（逐场景锚点带「 ·」防 1 被 10 子串掩盖；版本号双向防漂移——bump 忘改 examples.md 头部版本必炸构建）；SKILL.md 路由层两处指路（intro 行 + 深读区）
- ⚪ 零代码行为变更（仅版本号 bump；快照缓存按版本失效一次属预期，每基金重取 1 请求）

**V0.7.3 审查修复（证券身份、收益口径与缓存一致性）：**
- 🔴 **同码股票/指数分流**：腾讯路由默认按股票，指数须显式上下文或前缀——修 000016 等深股被静默导到沪指的问题
- 🔴 **收益窗口统一**：重仓探测基金/股票共用实际基点与共同终点；相对表现按共同起止日期计算，消除虚假背离与超额
- 🟢 **provider 与缓存一致性**：蛋卷成功只清自身风控，teardown 也按 URL 分线；季末后120天内 1h、其后 90 天；批量净值输入去重并校验响应代码；快照全部表格解析失败显式入 missing，ECharts 合法空结构不误报
- 🟢 **边界与工程质量**：重仓探测强制刷新贯通；新发零费率保留且升级缓存键；CI 三平台 × Python 3.12/3.13（最低支持版本提升至 3.12）；主套件 264 条离线断言全绿

**V0.7.2 三平台同等支持（锁抽象层）：**
- 🟢 **`fund_data_core.locks` 锁抽象层**：跨进程文件锁按**能力检测**选机制（flock / 字节范围锁），机制命名、零平台分支（守卫测试强制）；进程内注册表按归一化路径互斥（剥 `\\\\?\\` 前缀+大小写归一），不可重入契约显式报错（宁可 loud 不死锁），有界后端外层重试补齐无限阻塞语义
- 🔴 **消灭 F1 静默降级**：原 fcntl 缺失环境下节流降级为进程内——多进程并发时限流失效且无告警（与「绝不静默」立场冲突）；现三平台同一锁语义，锁文件与时间戳文件分离（旧时间戳无缝衔接）
- 🟢 **修 `safe_cache_path` 扩展前缀竞态**：并发创建瞬间 resolve 可能返回 `\?\` 形态与普通形态 root 假性不等 → 误报 escapes（多进程冷启动偶发）；双侧剥前缀 + resolve 瞬态与真逃逸分开上报
- 🟢 **CI 三平台矩阵**（离线全量 + flake 压测）+ 在线 workflow_dispatch 手动触发；Windows 实机原生全绿：core 92 + 离线 234/234 + 在线 304/304，flaky 压测 10/10

**V0.7.1 push2 防复发加固（IP 缓释桶定案，五发探针实证）：**
- 🔴 **P26 定性修正**：封锁=**IP 级缓释桶限流**——健康期容量大（treasury 4 连发数周无事）、惩罚期容量极小（3 连发即放空、第 4 发 RST）且恢复慢；12:16 三连过（同栈同 UA）**证伪「TLS 指纹永久黑名单」**，12:22/12:23 两连挂定案重触机制（上午 12 连发 0/6、1/6 是热窗观测，混淆了冷却与指纹）。教训：**诊断行为本身会烧 IP 额度——排查封锁先 ≤5 次小样本，绝不做几十连发**
- 🟢 **连接级熔断（新防线）**：`_push2_get` 单发即弃（删内部重试——1 调用=1 网络命中）+ 连续 2 败写独立停机标记（`.cbreak_push2conn`，与 F2 分工：F2 管 403/429 显式风控、本线管 RST/超时/空体无状态码形态）停机 3600s——停机期零网络直通 RuntimeError，降级链自动接管（国债→中债收盘/kline→腾讯备胎/板块→穿透单股降级）；期满自然恢复，每小时真实请求 ≤2 次
- 🟢 **`cn_treasury_realtime` 300s 盘中短缓存**（5 分钟内重复调用 0 请求）+ 值域守卫 0<y<15（拒脏值，ulist 式全零源即死于此）；`kline_daily` 指数路径行内 `source` 字段（push2his/tencent 诚实标注——主源健康但历史短的指数不能按长度推断来源）
- 🟢 **`concept_blocks` 迁入 `_push2_get`**（原走 em_get 无防线裸奔同域——上午全量测试崩溃的漏列暴露点）+ **`_SH_INDEX_Q` 从 BENCH_SECID 派生**（修 tencent_quote 裸码路由 000922→sz 深股静默错数据，5 码补齐）
- ⚪ ulist.np 批量方案实证否决：对 171 债券 secid 只回全零值（午休 tick 缺失或源未接线，两说未定案）且与单发共享同一 IP 桶——批量收益抵不过触桶风险

**V0.6.1 基准指数层（quant-excess 端点层收编，kline_daily 一函数三形态）：**
- 🟢 **B1 `kline_daily`**：指数/股票日线收盘原语——指数名查表（V0.6.1 时 15 指数/20 别名键；**现已 119 指数/125 别名键含行业板块 100，备胎已升级腾讯翻页全史**）/EM secid/6 位股票码；指数走 push2his 主源（F6 分流）+ 腾讯备胎（市场 2 无腾讯码不降级），股票复用 `_tencent_kline` 腾讯通道；20h 缓存键含 lmt 防窗口截断；实测沪深300 十年 2600 根、二调 0 请求
- 🔴 **P25 push2his 日K形态**入册；P23 补 dt=15 二次实测（pi/pn 全忽略恒返回拼音前 10）

**V0.7.0 字段语义升级 + 费率全表（破坏性改名，源端点逐一实证）：**
- 🔴 **字段名不副实清偿**：`fund_metrics().mgmt_fee_pct` 实为现申购费→改名 `subscription_fee_pct`（源端点实证：E2 fund_Rate=0.15 申购现价；管理费 1.20 只在 F10）；snapshot.manager 三字段改名——`tenure_start`→`total_tenure`（累计时长文本非日期，EM 原名 workTime）、`fund_size_yi`→`current_scale_text`（带单位字符串非 float）、`return_pct`→`return_chart`（三系列图表对象非标量）。旧键除名不双写（破坏性升级）
- 🟢 **B3 `fund_fees`**：F10 jjfl 费率全表——运作费三率（管理/托管/销售服务，每年%）+ 认购/申购（原/天天优惠）/赎回三阶梯（表头特征分派防插表错列；固定费档保原文）。赎回阶梯直接回答「拿多久赎回才划算」。实测 005001：管理1.20/托管0.20/申购<200万 1.50/0.15/赎回五档至 ≥730天免
- ✅ **syl 四窗口源端点实证无误**：005001 本地复权交叉对照——近3月/近6月/近1年吻合、近1月=官方20交易日口径（残差0.03）；fees 三标量验证正确
- 🔴 **P26 push2 系封锁应急**（V0.7.0 当时定性「TLS 指纹墙」，V0.7.1 经五发探针修正为 IP 缓释桶，见上）：`_push2_get` 探测通道 + kline 自动落腾讯备胎 + 国债实时中债收盘降级（as_of 口径标注）

**V0.6.0 经理画像与专项组装层（八函数，源全部一手实测钉死）：**
- 🟢 **`rank_persistence`**：fund_rank 缓存上算各周期同类分位（不高于该值占比口径，桶内最高=100），纳入门槛 ytd+1y 且 ≥4 周期；consistent=全周期 ≥75 分位——零请求「长牛」初筛
- 🟢 **`fund_scale`**：移动端 `FundMNBasicInformation.ENDNAV` 元精确值（161725 锚 209.47 亿分毫不差，1 请求）——rankhandler/E1 均无规模列（坑P22），**全市场规模榜无聚合源**，榜单场景由调用方对候选逐只富集
- 🟢 **`bond_plus_profile`**：快照组装权益仓位带（偏债<30/平衡/偏股≥60）+ 回撤带（≤5优/≤10良/≤20中）+ 利率敏感标注（债券≥50% 联动 cb_yield_curve/credit_spread）——零新请求
- 🟢 **`qdii_premium`**：腾讯市价 ÷ E6 净值 − 1（N 只=2 请求），溢价随 nav_date 一起解读（净值 T-1/T-2 滞后口径，剧烈行情会高估）
- 🟢 **`nav_divergence`**：净值收益 vs 上季重仓加权收益（腾讯日K前复权，新增 `_tencent_kline` 通道）→ 背离度；冷启 ≤12 请求、当日重跑 0~2；|背离|≤5 一致/≤10 关注/>10 疑似调仓；实测白酒 0.4pp「持仓一致」
- 🟢 **`fund_manager` / `manager_rank`**：经理主页服务端直出（侯昊：任职9年+82天/现任461.32亿/任期回报92.81%/mgrlists 在管清单）+ dt=14 排行（record≈4384 强校验，50 条/页）；**服务端仅姓名序、姓名→ID 无公开索引（坑P23 诚实边界）**——用基金码转接或经理ID
- 🔴 **P22 rankhandler 无规模列**：[17][18]=自定义区间收益、[19][20]=申购费原价/折扣价、[22]=优惠费率（双基金锚校准）——规模只能走移动端 ENDNAV
- 🔴 **P23 经理接口遗存**：旧名单页 404；dt=15 建议接口退化为固定 10 条样本（参数全忽略）；dt=14 的 sc 只认 XML 元素名（abbname），20 个规模/回报候选码全无效；pn 上限 50（88 页=超预算）
- 🔴 **P24 FOF 底层基金无结构化接口**：东财网页 F10/App/蛋卷/好买/新浪五源实测均只有股票直持仓——基金投资明细仅存季报 PDF，诚实边界
- 🟡 快照缓存按版本失效：0.5.0→0.6.0 首跑会重取所有诊断过的基金快照（批量诊断首日预算翻倍，预期行为非 bug）

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
- 🟢 **B2/B3 评测函数**：`position_trend`（仓位水平+加减仓）/ `peer_rank`（同类百分位+一年前对照）/ `relative_performance`（vs 沪深300或同类平均：相对收益+β+相关性）/ `fund_metrics` 附现申购费标注（管理费走 fund_fees）；实测白酒基金窗口收益与 grandTotal 基金曲线末点分毫不差（-4.23 vs -4.22）
- 坑 P15~P17 入册：E6 货基字段语义漂移 / QDII 日历差 / 跨域净值备胎不可得（三候选实测全死）

**V0.3.1 防封加固（两轮审计 F1~F7 修复）：**
- 🔴 **F1 跨进程节流锁**：限流时间戳从进程内存改磁盘文件+flock——多进程并发时合并速率不再越线（原实现双进程批量即可达封禁阈值的 2/3）
- 🔴 **F6 风控信号与网络故障分流**：`assert_not_risk_control()` 防线接入全部 8 个东财调用点——403/429 抛 `RiskControlError`（非 RequestException 子类），**绝不触发降级风暴**
- 🔴 **F2 熔断器**：连续 3 次风控信号→落盘熔断 10 分钟→冷却期内所有请求直接拒绝（零网络）；成功响应清零连击（V0.3.1 当时口径；Sprint 1 起改为业务完整性校验成功后清零）；tt/em 两线独立
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

## 第一章 防封铁律（请求预算契约）

本 skill 的设计约束不是"接口能不能通"，而是"请求额度是稀缺资源"。天天基金/东财系与 a-stock-data 的东财同一风控面（社区实测：>5次/秒、并发≥10、1小时4.5万请求 → IP 级封禁 20+ 小时）。

**五条铁律：**

1. **请求预算**：单基金深度诊断 ≤2 请求（1×pingzhongdata 当日缓存+0~1×lsjz）；全市场筛选只允许列表端点
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
| fund_crowding / fund_industry_allocation | 季末后120天内1h；其后90天 | 披露期防不完整数据固化；历史季度基本不可变 |
| lsjz | 不缓存 | 补充端点，按区间现查 |
| reits 净值历史 / 分红历史 / 不复权日线 | 24h / 24h / 20h | 半年频与事件频数据；逐只补齐断点续跑的载体（空结果有效期缩为 6h） |
| reits 实时概览 | 5 分钟 | 防同会话重复调用连打 |
| reits 报告文本 | 长期 | 命中就不下载 PDF、不重抽；带抽取方式标记，换抽取方式自动重抽 |

## 端点路由速查（按需定位，不必通读）

| 编号 | 函数 | 拿什么 | 源 | 请求数 |
|---|---|---|---|---|
| E1 | `fund_list()` | 全量清单 2.8万只（代码/简称/类型/拼音） | fund.eastmoney.com | 首载1，缓存0 |
| 前置 | `norm_fund_code(q)` | 代码/名称/拼音→归一化；股票代码拦截；A/C消歧 | 本地(E1缓存) | 0 |
| E2 ⭐ | `fund_snapshot(q)` | 一站式：净值全史/规模史/持有人/经理/重仓股+债/资产配置/仓位史/同类百分位史/**基准三曲线**/申赎/费率/ishb | fund.eastmoney.com | 1 |
| E3 | `fund_nav_history(q,s,e)` | 区间净值+**每日申赎状态**（E2失败时的净值兜底） | api.fund.eastmoney.com | 1~N页 |
| E4a | `fund_holdings(q,year,force_refresh)` | 季度前十大重仓（含历史各季报，与E2互证；可强制刷新） | fundf10.eastmoney.com | 1 |
| D1 | `fund_detail_danjuan(q)` | 蛋卷档案：总份额/经理/托管/风险等级/申赎（跨域备胎） | danjuanfunds.com | 1 |
| E6 | `fund_realtime_nav(codes)` | 批量最新净值（N只1请求，轻量探测） | fundmobapi.eastmoney.com | 1 |
| N1 | `fund_rank(top,fund_type,sort,ascending)` | 全市场排行/初筛底座（ytd/1m~3y/since 收益率族；类型本地过滤；None 恒末尾） | fund.eastmoney.com/data/rankhandler | 全量1，筛选0 |
| N2 | `fund_new_issues()` | 在发/新成立全量 + 公司名→ID 映射 | .../data/FundNewIssue.aspx | ~7页,缓存0 |
| N3 | `fund_company(q)` | 公司画像：规模/基金数/经理数/旗下清单（q=8位ID/基金码/公司名） | .../company/{id}.html | 1（基金码+1） |
| N4 | `fund_crowding(rdate)` / `fund_industry_allocation(rdate)` | 季度重仓股聚合（抱团度）/ 全市场19门类行业配置 | webapi.cninfo.com.cn（第四线） | 季末后120天内1h/历史90天 |
| V6 | `rank_persistence(top,fund_type)` | 业绩持续性矩阵：各周期同类分位+评分+consistent（零请求长牛初筛） | 本地(fund_rank缓存) | 0 |
| V6 | `fund_scale(q)` | 单基金最新规模（ENDNAV 元精确值）——重叠预警/榜单候选富集 | fundmobapi | 1 |
| V6 | `bond_plus_profile(q)` | 固收+画像：权益仓位带/回撤带/利率敏感标注 | 本地(快照) | 0 |
| V6 | `qdii_premium(codes)` | 场内溢价（市价÷净值-1，净值滞后口径标注） | 腾讯+E6 | N只=2 |
| V6 | `nav_divergence(q)` | 重仓探测：净值 vs 上季重仓加权收益背离度（疑似调仓） | 快照+E4a+腾讯日K | 冷启≤12 |
| B1 | `kline_daily(q,lmt)` | 指数/股票日线收盘 [{date,close}]：指数名查表/secid/股票码三形态 | push2his+腾讯备胎 | 1（缓存0） |
| B2 | `fund_scale_history(q)` | 规模全历史：募集期→最新季报（期间申购/赎回流量+份额+净资产+变动率，升序） | F10 gmbd | 1（缓存0） |
| B3 | `fund_fees(q)` | 费率全表：管理/托管/销售服务费（每年%）+认购/申购（原/优惠）/赎回三阶梯（持有期递减） | F10 jjfl | 1（缓存0） |
| PL | `fund_purchase_limit(q)` | 申购限额/交易状态：kind 四态+limit_cny 折算+not_for_sale（P31 唯一来源；已并入快照 purchase_limit 数据集） | fund.eastmoney.com/{code}.html | 1（缓存0）；⚠️无批量接口 |
| RP | `fund_reports(q,category,limit)` / `fund_report_pdf(row,dest_dir)` | 报告清单（7 类目·新→旧·kind/年份分类）+ PDF 下载（EdgeOne 挑战 node 自动解，需系统 node；场外/上市全覆盖 P32） | api.fund.eastmoney.com/f10/JJGG + pdf.dfcfw.com | 清单1（缓存0）·PDF 1~2/份 |
| V6 | `fund_manager(q)` / `manager_rank(pages)` | 经理画像（任职/规模/回报/在管清单）/ 排行（姓名序） | eastmoney manager 页+dt=14 | 1·50/页 |
| 本地 | `adjusted_nav(recs)` | 复权净值（分红再投口径，链式） | 零网络 | 0 |
| 本地 | `fund_metrics(snap,s,e)` | 区间收益/年化/最大回撤/波动/夏普/卡玛/现申购费标注 | 零网络 | 0 |
| 本地 | `fund_dca_backtest(snap,...)` | 定投回测 + **XIRR 分期真年化**（naive 对照保留） | 零网络 | 0 |
| 本地 | `position_trend(snap)` | 仓位史解读：水平区间/加减仓信号 | 零网络 | 0 |
| 本地 | `peer_rank(snap)` | 同类百分位：最新值+一年前对照+分位标签 | 零网络 | 0 |
| 本地 | `relative_performance(snap,基准)` | vs 沪深300/同类平均：相对收益+β+相关性（用内置三曲线） | 零网络 | 0 |
| 本地 | `holding_map(snap)` | 重仓股穿透桥 → 喂 a-stock-data | 零网络 | 0 |
| L6 | `tencent_quote(codes)` | 重仓股批量实时行情（价/PE/PB/市值） | 腾讯(不封IP) | N只=1 |
| L6 | `sina_quote(codes)` | 新浪批量实时行情（V0.7.5 备胎）：{code: {name, open, prev_close, last, bid1, ask1, date, time}}；须带 sh/sz 前缀 | 新浪(独立分账·互备) | ≤50只/请求 |
| L6 | `concept_blocks(stock)` | 个股板块标签（行业/概念/地域混合） | 东财 push2 | 1/股,当日缓存 |
| L6 ⭐ | `penetrate(query)` | 一键穿透：权重×行情×板块→主题聚合→`plain_text_map()`白话地图 | 组合 | 冷启≤13,当日重跑1 |
| L7 | `cb_yield_curve(curve,year)` / `cb_curve_series` / `credit_spread` | 中债曲线族年文件·单期限序列·信用利差本地算差 | 中债(curl通道) | 1/曲线/年,当日缓存 |
| L7 | `treasury_yield_daily(days)` / `shibor_history(period)` / `benchmark_rate_history()` | 中美国债日频+利差 / SHIBOR多期限 / 基准利率调整史(附次日沪深) | 东财 datacenter | 1 |
| L7 | `repo_rate_gc()` / `cn_treasury_realtime()` | GC007回购(DR007代理) / 国债四期限盘中 | 腾讯+push2 | 1+4 |
| 降级 | `fund_snapshot` 自动降级 | E2挂→E3+E4a+蛋卷分拆，source标注degraded | 组合 | ~N+2 |
| REIT | `reit_board(force_refresh,with_nav_date)` | 全市场公募 REITs 行情板：现价/涨跌/开高低/量额/换手/总市值 + 单位净值 + 折溢价率（103 只一次批量）；with_nav_date=True 逐只补净值日期 | 腾讯 qt.gtimg.cn + stockjs.finance.qq.com | 1（补齐 +N 只，当日缓存 0） |
| REIT ⭐ | `reit_profile(code,force_refresh,macro)` | 单只画像：基本信息 / 半年频净值与日期（发行价 or 报告净值）/ 折溢价 / 分红史与近 12 个月派息率 / 分红再投资收益 / 对 10Y 国债利差 | 腾讯基金页 4 接口 | 4（当日缓存后 0~1） |
| REIT | `reit_report_text(code,kind,year,pages,...)` | 定期报告逐页文本（pypdf 布局模式）+ 页码索引 + 关键词页码 + 交叉核对值；AI 直接读，库不抽值 | 东财 f10/JJGG + pdf.dfcfw.com | 2~3（文本缓存后 0） |

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
| P22 rankhandler 无规模列 | [17][18]=自定义区间收益、[19][20]=申购费原/折扣价、[22]=优惠费率——没有规模 | 单基金规模走移动端 ENDNAV（`fund_scale`）；全市场榜无聚合源=诚实边界 |
| P23 经理接口遗存 | 旧名单页404；dt=15 退化为固定10条样本；dt=14 sc 只认 XML 元素名（20 候选全无效）、pn≤50 | 经理ID/基金码两路进 `fund_manager`；排行如实标注姓名序；姓名→ID 无索引 |
| P24 FOF 底层无接口 | 五源（东财F10/App/蛋卷/好买/新浪）均只有股票直持仓 | 基金投资明细仅存季报 PDF——诚实边界，FOF 驾驶舱降级为池+仓位史+直持仓穿透 |
| P25 push2his日K形态 | fields2 只取 f51,f53（日期,收盘）；市场2（中证指数公司）无腾讯代码不可备胎；缓存键必须含 lmt 防窗口截断 | `kline_daily` F6 分流（RiskControlError 不降级）+ 日期去重 + need=min(lmt,100) 下限 |
| P26 push2系IP缓释桶 | 封锁=**IP 级缓释桶**：惩罚期容量极小（3 连发即放空、第 4 发 RST）、恢复慢（小时级续量）、溢出全客户端全掐（curl 亦然）；健康期容量大（4 连发数周无事）。12:16 三连过证伪「TLS 指纹永久黑名单」，12:22/12:23 两连挂定案重触（上午 12 连发系热窗观测）。教训：诊断行为本身烧 IP 额度，排查先 ≤5 次小样本 | `_push2_get` 单发即弃 + flock 地板 + 0.5~3.0s 抖动 + **连接级熔断**（2 连败停 1h，独立标记 `.cbreak_push2conn`，停机期零网络）+ 降级链（kline→腾讯备胎/国债→中债收盘 300s 短缓存+值域守卫/板块→穿透单股降级）；TLS 指纹假说未排除但不伪装（不军备竞赛） |
| P27 跨平台锁与路径形态 | ①Windows 扩展前缀竞态：并发创建瞬间 `resolve(strict=False)` 可返回 `\?\` 形态与普通形态 containment 假性不等→误报 escapes（多进程冷启动偶发）；②字节范围锁同进程不同句柄互斥（嵌套=死锁→显式报错）；③LK_LOCK ~10s 即弃（需外层重试补无限阻塞） | `locks` 抽象层：能力检测选机制+注册表互斥+不可重入报错+外层重试；`drop_extended_prefix` 归一入口；契约测试 14 条 |
| P28 腾讯日K翻页协议 | 单页 800 上限（P25）非硬边界：param 第 3/4 槽支持起止日期按页末锚翻页；服务端回含锚日 +1 行重叠供缝校验；qfq 因子跨请求一致（全史翻页实测 0 漂移）；锚停滞/页阀耗尽/中段空页三异常形态曾可静默截断 | `_tencent_kline_page`/`_stitch_tencent_pages`/`_tencent_kline_paginated`：缝双校验+三防线整批拒绝+去重集满口径（V0.7.6，两轮审查加固） |
| P29 板块 BK 码空间 | BK 码不可凭记忆/类目外推：同码空间跨行业(t:2)/概念(t:3)（BK0477 实为酿酒概念、BK0475=银行Ⅱ 而非白酒）；clist fs 语法是冒号 `t:2` 非 `t=2`（后者 rc:102 参数错） | BENCH_SECID 行业段=clist t:2 快照 + kline name 双样本交叉验证；未命中走 `90.BKxxxx` secid 直传；概念板块不收录（归 a-stock-data 概念域） |
| P30 新浪行情通道（V0.7.5） | hq.sinajs.cn 无 Referer 即拒（实测空响应）；高频爬虫有封禁前科；银行间信用债无公开实时端点（货币网现券板仅利率债活跃券） | `sina_quote` 内置 Referer + provider=sina 独立分账 120/300s·0.5s·单并发 + 403/429 计熔断 + 空体计信号；定位=腾讯互备低频兜底，银行间实时须 QB 等权限源 |
| P31 申购限额唯一来源（V0.7.7） | E2/E3/E6 探针实测均无完整限额（E3 SGZT 仅「限大额」短文本无金额）——唯一来源是主页面 `{code}.html` 交易状态栏，且无批量接口（N 只 N 请求）；金额与「限大额」分属不同层级 text node（嵌套 span），简单 regex 必截断（000369 实测）；外币折算是静态近似汇率非实时牌价 | `fund_purchase_limit`：`_StaticCellParser` 嵌套感知全文拼接 + `parse_purchase_limit` 纯函数（kind 四态；限大额无金额诚实 limit_cny=None 不虚报 0）；已并入快照 `purchase_limit` 数据集（失败进 missing 不炸快照）；rankhandler ft=qdii 仅 363 只混合/股票类（联接/LOF 归「指数型-海外股票」不在内） |
| P32 基金报告双通道（V0.7.8） | ①JJGG 必须带 fundf10 Referer——裸 UA 是 200+ErrCode-999+Data=""「假成功」；type=页面 tab（0 全部/1 发行运作/2 分红送配/3 定期报告/4 人事调整/5 基金销售/6 其他），部分基金加披 Q2/Q4 季报（交银趋势实测 6 期/年）②PDF 直链 `H2_{ID}_1.pdf` 的 _1 附件号不可省（少了拿到挑战壳）③pdf.dfcfw.com 腾讯 EdgeOne 反爬：200+Content-Type 伪装 pdf 但 body 是 988B 静态 `<script>` 挑战；巨潮 column=fund 仅上市基金，场外不在其体系 | `fund_reports`：Referer 必带+Data 非 list 显式报错+无 ID 行跳过+total>0 解析空拒绝；`fund_report_pdf`：_edgeone_solve node vm 沙箱（无 require/fs/net+timeout）解 `__tst_status`/`EO_Bot_Ssid` 重放一次，仍非 PDF/残包(<1KB)显式报错；挑战 cookie 进程级复用；dfcfw.com 归 eastmoney 线同账限流 |
| P34 申万官网 TLS（V0.8.2） | swsresearch.com 漏发中间证书（GeoTrust G2 2022 CA1，效期 2032-12-14），负载均衡部分节点全漏——requests/curl 必报 `unable to get local issuer certificate`，重试赌节点不可靠；AkShare 以 verify=False 绕过（关校验不可接受） | 自组 CA bundle：内置中间证书 PEM 常量 + certifi 根库拼接（完整链校验保留）；证书失败（SSLCertVerificationError）显式报错更新指引、不计连接级熔断；provider 级 record_failure 照计数兜底；P34 诊断命令 `openssl s_client -connect … | grep verify` |
| P35 按年切片缓存跨年（V0.8.3） | 只盘点磁盘现存年会在跨年后把旧年当永久完整缓存；当前年缺失、周末末行落后、短区间查询被误当全史起点会分别造成冻结、重复请求与静默短史 | 日线要求当前年存在；历史估值区间不强制当前年；周末不回拨；只有全史抓取可写 verified_earliest；中证交易日清单陈旧主动刷新并留痕 |
| P33 中证全收益（V0.8.0） | 非交易日起始日会生成与下一交易日相同的占位行；全收益代码存在 H00300/H20269/932000CNY010 等无统一规律形态；HTTP 200 可携带业务400；个别历史行 change 与收盘差有上游口径差；当日 tradeDate 清单先行含今天而行情盘后才发布 | 官方 tradeDate 过滤+缺失按年重取；衍生指数+基本资料+行情三验身份；业务 code 强校验；change 异常写审计警告，changePct 与收盘明显矛盾拒绝入库；不降级价格指数；当日未发布不计缺失（pending_today 入报告，V0.8.1 盘前同步不炸） |
| P36 东财净值对 REIT 停在发行价（V0.8.4） | 公募 REIT 不按日披露份额净值（半年频，中期/年度报告），东财 E2/E6 对 REIT 的净值恒为发行价（180101 实测 2.31@2021）——`qdii_premium` 放行 508/180 号段后据此算出约 -40% 的假溢价；腾讯前复权是减法复权（除息前 qfq = 不复权 − 分红）；新上市 REIT 的「净值」是发行价（净值日期 = 成立日） | `qdii_premium` 号段判断后加 `_is_reit` 拒绝并指路；`fund_metrics`/`fund_dca_backtest` 报错分支追加指路；REIT 走 `reit_board`/`reit_profile`（腾讯半年频净值，行情串第 81 段单位净值 / 第 77 段折溢价率，输出必带 `nav_date`/`nav_kind`）；`_tencent_kline_page(fq="")` 取不复权自算分红再投资；`finance.qq.com` 归腾讯线白名单 |

## Prerequisites

```bash
pip install requests
```

硬依赖 requests + 系统 curl（L7 中债通道需要，P11；macOS/Linux 自带）+ 系统 node（V0.5 巨潮抱团度/行业配置线的 enckey 计算与 V0.7.8 报告 PDF 的 EdgeOne 挑战求解，macOS/Linux 常见自带；无 node 时相应函数显式报错不静默）。pandas 可选。pypdf 可选（仅 `reit_report_text` 的 REIT 报告 PDF 转文本需要，缺失时显式报错给安装提示）。L6 穿透自带内嵌股票域通道，无需 a-stock-data 即可跑通；个股深度再加载 a-stock-data。

## 层 1 · 前置层：统一入口 + 主键表（模块 L1）

所有端点的前置依赖：

```python
# -*- coding: utf-8 -*-
"""
fund-data V0.8.4 — 基金底层数据工具包（基金主源 + 蛋卷备胎 + 本地计算 + 跨域穿透 + 宏观利率 + 全市场数据层 + 经理画像与专项组装层 + 基金报告 + 中证全收益指数 + 申万行业指数 + 公募REITs）

请求预算契约（防封铁律，详见 SKILL.md 第一章）：
  1. 单基金深度诊断 ≤3 请求（1×pingzhongdata + 0~1×lsjz + 0~1×主页面限额，V0.7.7 起快照含 purchase_limit）
  2. 全市场筛选走 rankhandler 全量单请求或 TopN 单页，禁止 for fund in all: snapshot(fund)
  3. 同一基金当日快照磁盘缓存命中 = 0 请求
  4. 所有东财/天天请求走 ttfund_get()：串行 + 1.5s 最小间隔 + 会话复用 + 强制 HTTPS
  5. 基金报告清单 1 请求（f10/JJGG，当日缓存）；报告 PDF 下载 1~2 请求/份
     （EdgeOne 挑战自动解，同会话后续通常 1 请求直连，V0.7.8）
  6. 公募 REITs（V0.8.4）：全市场行情板 1 请求（腾讯）；单只画像 4 请求（腾讯，当日缓存后 0~1）；
     行情板逐只补净值日期另计 1 请求/只（≥1s 慢节拍、连续 2 败中止、断点续跑）；报告 PDF
     1~2 请求/份（东财，仅第二段且先问用户）。REIT 不走 fund_snapshot/fund_metrics/qdii_premium
     （东财净值对 REIT 停在发行价，坑P36）

坑记录（2026-11-05 实测）：
  P1  pingzhongdata 必须 HTTPS（UA 无关）——HTTP 静默返回 0 字节，HTTP 200 照常
  P2  lsjz 三连坑：①pageSize 服务端钳制 20（请求 49~200 都只回 20 条，500 静默 0 条）
      ②TotalCount 在响应顶层不在 Data 内 ③必须显式日期区间。
      → 固定 pageSize=20 + 顶层 TotalCount + 实收条数强校验
  P3  pingzhongdata 变量按基金类型选择性存在（如白酒基金无 Data_industryAllocation）
      → 解析器容忍缺失，返回 missing 列表，绝不静默空
  P3b pingzhongdata 值后挂 `;/*注释*/var`——按相邻 var 位置切片+剥注释，
      否则字符串值吞掉后文（实测 syl_1y 坏过）；stockCodesNew 是 ["1.600519",...] secid 数组
  P4  基金代码与股票代码同段碰撞（000001=华夏成长混合 vs 平安银行）
      → 代码不在基金清单时明确报错并引导 a-stock-data，不静默空
  P5  fundgz.1234567.com.cn 盘中估值已下线（404），蛋卷 growth 净值接口亦不可用
      → 净值无跨域备胎，东财系全挂时诚实报缺；盘中估算走 V0.3 本地推演
  P6  unitMoney 事件标记存在但**金额数字被上游剥离**（实测"分红：每10份派现金元"），
      且"拆分"事件混在同一字段——解析分红金额不可靠
      → 复权引擎优先链接官方 equityReturn（已处理分红/拆分），unitMoney 解析仅作回退
  P7  E3 lsjz 返回**新→旧**序，E2 netWorthTrend 是**旧→新**序——降级快照必须反转，
      否则喂给指标计算收益方向全反（静默错，比静默空更危险）
  P8  中债 searchYc POST 是参数黑洞（实测恒返回 []），downYearBzqx GET 直出全年
      xlsx——只用下载端点，不碰查询端点；中债请求必须带 Referer
  P9  中债 xlsx 的单元格是 inlineStr（无 sharedStrings），zipfile+regex 可解，
      无需 pandas/openpyxl——行结构为 (日期, 期限码, 收益率) 三元组
  P10 东财 datacenter 报表名必须从页面 bundle 逆向（臆测名全 9501）：
      国债日频=RPTA_WEB_TREASURYYIELD / SHIBOR=RPT_IMP_INTRESTRATEN(filter CNY)
      / 基准利率调整=RPT_ECONOMY_DEPOSIT_RATE；LPR 独立报表不存在(9501)
  P11 中债 TLS 指纹墙：requests/urllib 稳定 SSL EOF，curl 稳定 200（A/B 实测
      2026-11-06）——中债通道用 subprocess curl（_cb_request），仍计入限流与审计
"""
import html as _html
import http.client
import io
import json
import math
import os
import re
import ssl
import sys
import time
from html.parser import HTMLParser
import random
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests

try:  # pandas 可选：仅 fund_metrics 的 DataFrame 输出用到，缺了降级为 list[dict]
    import pandas as pd
except ImportError:
    pd = None

__version__ = "0.8.4"


FUND_CACHE_DIR = Path.home() / ".fund_data_cache"
SNAPSHOT_TTL_HOURS = 20          # 距上次抓取 20h 失效（滚动窗口，非自然日切分）
_BJ_TZ = timezone(timedelta(hours=8))   # E2 时间戳按北京时区解析，非东八区机器不漂移

TT_MIN_INTERVAL = 1.5            # 东财/天天系最小请求间隔（秒）；批量场景建议调 2~3
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 坑P12：本 skill 全部端点是国内金融数据站——macOS 系统代理（VPN/Clash，requests 的
# trust_env 会读 scproxy）转发这些域名只会引入抖动（实测 SSL EOF 间歇失败，curl 直连则稳）。
# 故两条 Session 一律直连（不读系统代理/环境代理）。
TT_SESSION = requests.Session()
TT_SESSION.trust_env = False                          # 直连：绕过系统代理（坑P12）
TT_SESSION.headers.update({"User-Agent": UA})
TT_REQUEST_COUNT = [0, {}]       # [总请求数, {host: 次数}] — 预算审计用，ttfund_get 自增

# ── F1：跨进程节流锁（文件锁 + 磁盘时间戳）───────────────────────────
# 原实现的时间戳在进程内存里——licai-map 服务、AI 会话、手跑脚本各持一份限流器，
# 合并速率 = 进程数 × 0.67req/s，双进程批量即可越 300req/5min 封禁线（审计 F1）。
# 现改为磁盘时间戳 + 文件锁：同机所有进程强制串行，锁等待自动吸收间隔。
# 锁经 fund_data_core.locks 抽象层（能力检测选机制，全平台同一语义，
# 与 flock 时代一致不再有平台静默降级——多进程限流保证不分操作系统）。

_CBREAK_COOLDOWN = 600           # F2：熔断冷却 10 分钟
_CBREAK_TRIP = 3                 # 连续 3 次风控信号触发熔断
_cbreak_state = {"tt": [0, 0.0], "em": [0, 0.0], "cninfo": [0, 0.0],
                 "danjuan": [0, 0.0], "sina": [0, 0.0], "tencent": [0, 0.0],
                 "cb": [0, 0.0], "csindex": [0, 0.0], "sws": [0, 0.0]}

# Sprint 2/3：异常与缓存语义与 fund_data_core 统一（core 为硬依赖，与主模块同一套部署）。
from fund_data_core import CachePolicy, CacheRepository, StaleDataError
from fund_data_core.cache_policies import (FUND_LIST_TTL_SECONDS,
                                           PROVISIONAL_QUARTER_TTL_SECONDS)
from fund_data_core.errors import (CircuitOpenError, LeaseError,
                                   RiskControlError, UnknownHostError)
from fund_data_core.locks import file_lock
from fund_data_core.policies import provider_for_url

FUND_LIST_TTL_DAYS = FUND_LIST_TTL_SECONDS // 86400   # 单一来源：core 常量派生（30 天）


# ── Sprint 2：跨进程请求协调核心（fund_data_core）接入 ─────────────────
# 模块加载完成后默认激活协调器（见文件末尾 _reset_request_coordination() 调用）：
# 配额滑窗/最小间隔/在途上限按 provider 分账（tt+em+push2 合并东财风控面），
# 熔断闸门由协调器裁决。成功语义保持 Sprint 1 口径：传输层（Gateway）只分类
# 风险与可达性，HTTP 2xx 绝不清零；业务完整性校验通过后由调用方显式
# _cbreak_success() 清零（provider 级结论）。测试可再次调 _reset_request_coordination
# 注入 FakeClock/临时状态目录重建；下方 legacy F1/F2 实现保留供 _teardown 回退。
_COORDINATOR = None
_REQUEST_GATEWAY = None
_CACHE_REPO = None     # Sprint 3：CacheRepository（FUND_CACHE_DIR 即缓存根，键=legacy 相对路径）
_CORE_LINE_PROVIDER = {"tt": "eastmoney", "em": "eastmoney", "cninfo": "cninfo",
                       "danjuan": "danjuan", "tencent": "tencent", "sina": "sina",
                       "chinabond": "chinabond", "cb": "chinabond",
                       "csindex": "csindex", "sws": "sws"}


def _reset_request_coordination(clock=None, sleeper=None, policies=None):
    """（重新）构建进程级协调器与网关；返回 Coordinator（测试/嵌入方用）。

    状态目录固定在 FUND_CACHE_DIR/coordinator（测试先重定向 FUND_CACHE_DIR 再调
    本函数）。clock/sleeper 可注入确定性时钟；缺省用真实 time 模块。policies
    可传 {"eastmoney": {"min_interval": 0}} 等字段 dict——在本模块预算策略之上
    replace 合并（保住 240/300s 等防封余量，绝不能回落 DEFAULT_POLICIES）。
    构建后 legacy F1/F2 磁盘文件不再读写（实现保留，供 _teardown 回退）。
    """
    global _COORDINATOR, _REQUEST_GATEWAY
    from dataclasses import replace as _dc_replace
    from fund_data_core import Coordinator, Gateway, ProviderPolicy
    resolved = {
        # 东财全域（tt/em/push2 同一 IP 风控面）预算 240/300s：300 封禁线留 20% 余量
        "eastmoney": ProviderPolicy(
            max_requests=240, window_seconds=300, min_interval=TT_MIN_INTERVAL,
            max_in_flight=4, lease_seconds=30, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        "cninfo": ProviderPolicy(
            max_requests=120, window_seconds=300, min_interval=CNINFO_MIN_INTERVAL,
            max_in_flight=2, lease_seconds=30, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        "danjuan": ProviderPolicy(
            max_requests=120, window_seconds=300, min_interval=1.5, max_in_flight=2,
            lease_seconds=30, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        "tencent": ProviderPolicy(
            max_requests=300, window_seconds=300, min_interval=0.2, max_in_flight=4,
            lease_seconds=30, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        # 新浪（V0.7.5 备胎通道，定位=腾讯互备低频使用）：hq.sinajs.cn 无 Referer
        # 即拒（实测），高频爬虫有封禁前科——配额取腾讯的 40% 且单并发保守起量
        "sina": ProviderPolicy(
            max_requests=120, window_seconds=300, min_interval=0.5, max_in_flight=1,
            lease_seconds=30, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        # 中债 curl 通道：V0.7.5 已由 _cb_request 的 Gateway curl transport 分支
        # 消费（分账/间隔/在途/熔断生效）；teardown 模式走 legacy F1 地板 + F2 镜像。
        # lease 须覆盖 curl 30s 与 runner 额外 10s，并留慢调度余量，避免结算顶替业务异常。
        "chinabond": ProviderPolicy(
            max_requests=120, window_seconds=300, min_interval=1.5, max_in_flight=2,
            lease_seconds=70, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        "csindex": ProviderPolicy(
            max_requests=120, window_seconds=300, min_interval=CSINDEX_MIN_INTERVAL,
            max_in_flight=1, lease_seconds=180, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
        "sws": ProviderPolicy(
            max_requests=120, window_seconds=300, min_interval=SWS_MIN_INTERVAL,
            max_in_flight=1, lease_seconds=180, circuit_threshold=_CBREAK_TRIP,
            open_seconds=_CBREAK_COOLDOWN),
    }
    for name, override in (policies or {}).items():
        if name not in resolved:
            raise ValueError(f"未知 provider 策略覆盖: {name!r}")
        resolved[name] = (_dc_replace(resolved[name], **override)
                          if isinstance(override, dict) else override)
    tick = clock if clock is not None else time
    _COORDINATOR = Coordinator(FUND_CACHE_DIR / "coordinator", policies=resolved,
                               clock=tick, sleeper=sleeper if sleeper is not None else tick)
    _REQUEST_GATEWAY = Gateway(_COORDINATOR)
    global _CACHE_REPO
    _CACHE_REPO = CacheRepository(FUND_CACHE_DIR)   # 数据文件保持在 legacy 相对路径
    for line in _cbreak_state:
        _cbreak_state[line] = [0, 0.0]
    return _COORDINATOR


def _teardown_request_coordination():
    """拆除协调（回退 legacy F1/F2 与直读缓存）：诊断/降级排查时按需调用。"""
    global _COORDINATOR, _REQUEST_GATEWAY, _CACHE_REPO
    _COORDINATOR = None
    _REQUEST_GATEWAY = None
    _CACHE_REPO = None


def _tt_hit(url: str):
    """ttfund_get 逐尝试账本（与 legacy 口径一致：host 明细入账）。"""
    TT_REQUEST_COUNT[0] += 1
    host = re.sub(r"^https?://", "", url).split("/")[0]
    TT_REQUEST_COUNT[1][host] = TT_REQUEST_COUNT[1].get(host, 0) + 1


def _em_hit(url: str):
    EM_REQUEST_COUNT[0] += 1


def _cninfo_hit(url: str):
    CNINFO_REQUEST_COUNT[0] += 1


class _SessionAdapter:
    """Gateway 注入用 Session 视图：只暴露 get/post（网关按 per-method 分发，
    测试对 TT_SESSION.get/.post 的替换继续生效），每次尝试回调 on_hit 记账
    （与 legacy 逐 attempt 的请求账本口径一致）。被包裹会话若返回异常实例
    （mock/半破坏 session 的形态）按传输失败抛出，交由网关重试/结算。"""

    def __init__(self, session, on_hit):
        self._session = session
        self._on_hit = on_hit

    def get(self, url, **kwargs):
        self._on_hit(url)
        result = self._session.get(url, **kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    def post(self, url, **kwargs):
        self._on_hit(url)
        result = self._session.post(url, **kwargs)
        if isinstance(result, BaseException):
            raise result
        return result


def _release_pending(resp, note: str = "raw-passthrough"):
    """中性结算响应上待裁决的许可：入账响应、释放租约、绝不清零（Sprint 1）。

    业务层的成功清零统一走 _cbreak_success()（provider 级显式结论）。
    慢响应边缘租约可能已到期被 _prune 中性清理——LeaseError 静默（语义等价）。
    """
    permit = getattr(resp, "permit", None)
    if permit is not None:
        try:
            permit.failure(reason=note, response_received=True)
        except LeaseError:
            pass


class UpstreamFormatError(RuntimeError):
    """上游响应结构不符合契约：不计成功、不触发网络降级。"""


class UpstreamRequestError(UpstreamFormatError):
    """请求信封/业务接口失败；供批量同步与单指数内容校验错误分流。"""


def _throttle_file(line: str) -> Path:
    return FUND_CACHE_DIR / f".throttle_{line}"


def _cbreak_file(line: str) -> Path:
    return FUND_CACHE_DIR / f".cbreak_{line}"


def _cross_proc_throttle(line: str, min_interval: float):
    """F1 跨进程节流：文件锁抢锁 → 读磁盘时间戳 → 睡足间隔 → 写回 → 放锁。

    时间戳语义=放行时刻（非请求完成时刻）：慢响应下相邻请求**发起**间隔恒 ≥interval
    （V0.3.0 是间隔+响应耗时，本版略松但仍在设计线内，见 ttfund_get docstring）。
    锁文件与时间戳文件分离（与 core 的 state/cache 同格局）：旧版时间戳写在锁文件
    本体里，升级后原文件继续作为时间戳载体无缝衔接，锁改为旁挂 .lock 兄弟文件。
    """
    ts_path = _throttle_file(line)
    lock_path = Path(str(ts_path) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(lock_path):
            ts_path.touch(exist_ok=True)               # O_CREAT 原子首建，绝不截断已有内容
            try:
                last = float(ts_path.read_text().strip() or 0)
            except ValueError:
                last = 0.0
            now = time.time()                          # 拿锁后再取样（排队时长不计入间隔）
            wait = min_interval - (now - last)
            if wait > 0:
                time.sleep(wait + random.uniform(0.05, 0.3))
            ts_path.write_text(f"{time.time():.6f}")
    except OSError:
        # 锁文件异常 → 降级进程内保守节流（保持可用性优先）
        time.sleep(min_interval * random.uniform(0.8, 1.2))


def _line_label(line: str) -> str:
    """熔断文案按实际 provider 标注，避免跨域备胎误报为东财。"""
    return {"cninfo": "巨潮", "danjuan": "蛋卷", "sina": "新浪",
            "tencent": "腾讯", "chinabond": "中债", "cb": "中债",
            "csindex": "中证指数", "sws": "申万指数"}.get(line, "东财")


def _cbreak_check(line: str):
    """F2：熔断检查。冷却期内所有请求直接拒绝（不敲上游），报错给明确解除时间。

    Sprint 2：协调核心激活时读协调器闸门（OPEN 拒绝 / HALF_OPEN 放行，探针语义
    由网关-许可层保证；快照读取同时完成到期转 HALF_OPEN 的推进）。legacy 路径
    不变（磁盘冷却文件）。
    """
    if _COORDINATOR is not None:
        snap = _COORDINATOR.snapshot(_CORE_LINE_PROVIDER.get(line, "eastmoney"))
        if snap["circuit"] == "OPEN":
            remain = int(max(0.0, snap["open_until"] - time.time()))
            raise CircuitOpenError(
                f"{_line_label(line)}[{line}] 熔断中（疑似触发风控已自动停机），冷却剩 {remain}s。"
                f"这是保护机制——继续请求只会延长封禁；稍后再试或换网络。")
        return
    f = _cbreak_file(line)
    if f.exists():
        try:
            until = float(f.read_text().strip() or 0)
        except ValueError:
            until = 0.0
        if until > time.time():
            remain = int(until - time.time())
            raise CircuitOpenError(
                f"{_line_label(line)}[{line}] 熔断中（疑似触发风控已自动停机），冷却剩 {remain}s 至 "
                f"{datetime.fromtimestamp(until).strftime('%H:%M:%S')}。"
                f"这是保护机制——继续请求只会延长封禁；稍后再试或换网络。")
        f.unlink(missing_ok=True)                     # 冷却期满自动复位
        _cbreak_state[line] = [0, 0.0]


def _cbreak_success(line: str):
    """业务结构与完整性校验全部通过后，才清零该线的风控连击。"""
    if _COORDINATOR is not None:
        _COORDINATOR.clear_risk_streak(_CORE_LINE_PROVIDER.get(line, "eastmoney"))
    _cbreak_state[line] = [0, 0.0]        # 镜像 legacy 状态（审计打印/内省兼容）


def _cbreak_signal(line: str, why: str):
    """F2：记录一次风控信号。连续 _CBREAK_TRIP 次（5 分钟窗口内）→ 落盘熔断并抛错。

    Sprint 2：协调核心激活时信号入协调器（跨进程共享连击——真实账本；legacy 的
    5min 孤立信号衰减由「业务成功清零 + 滑窗配额」替代：连击只被显式业务成功
    清零，不会被时间静默冲销）。_cbreak_state 同步镜像（进程内审计口径兼容），
    触闸抛错口径与 legacy 一致。
    """
    cnt, last_ts = _cbreak_state.get(line, [0, 0.0])
    cnt = 1 if (time.time() - last_ts) > 300 else cnt + 1   # 镜像口径：跨 5min 孤立信号重计
    _cbreak_state[line] = [cnt, time.time()]
    if _COORDINATOR is not None:
        outcome = _COORDINATOR.signal_risk(_CORE_LINE_PROVIDER.get(line, "eastmoney"), why)
        if outcome["opened"]:
            _cbreak_state[line] = [0, 0.0]
            raise RiskControlError(
                f"{_line_label(line)}[{line}] 连续 {_CBREAK_TRIP} 次风控信号（最近: {why}），已自动熔断 "
                f"{_CBREAK_COOLDOWN // 60} 分钟。"
                f"熔断期内所有{_line_label(line)}请求直接拒绝（保护 IP）。")
        return
    if cnt >= _CBREAK_TRIP:
        until = time.time() + _CBREAK_COOLDOWN
        try:
            _cbreak_file(line).parent.mkdir(parents=True, exist_ok=True)
            _cbreak_file(line).write_text(f"{until:.0f}")
        except OSError:
            pass
        _cbreak_state[line] = [0, 0.0]
        raise RiskControlError(
            f"{_line_label(line)}[{line}] 连续 {_CBREAK_TRIP} 次风控信号（最近: {why}），已自动熔断 "
            f"{_CBREAK_COOLDOWN // 60} 分钟至 {datetime.fromtimestamp(until).strftime('%H:%M:%S')}。"
            f"熔断期内所有{_line_label(line)}请求直接拒绝（保护 IP）。")


def assert_not_risk_control(resp, line: str = "tt", context: str = ""):
    """F6 防线：调用方 raise_for_status **之前**先过这一道——403/429 转 RiskControlError。

    此函数只分类传输层风险，不判定业务成功；HTTP 500、非 JSON、结构异常均不清零。
    调用方完成业务完整性校验后，必须显式调用 _cbreak_success(line)。
    """
    if resp.status_code in (403, 429):
        _cbreak_signal(line, f"HTTP {resp.status_code} {context}")
        raise RiskControlError(
            f"HTTP {resp.status_code}（{context}）：疑似{_line_label(line)}风控信号，"
            f"已计入熔断计数。本错误不触发降级（防请求风暴），请降频稍后再试。")


def _assert_not_risk_content(text: str, line: str, context: str):
    """识别已实测的 200+拒绝文案；其他结构异常由调用方按 UpstreamFormatError 处理。"""
    if "无访问权限" in text:
        _cbreak_signal(line, f"内容风控 {context}")
        raise RiskControlError(
            f"{context} 返回「无访问权限」：疑似{_line_label(line)}风控信号，"
            f"已计入熔断计数。本错误不触发降级。")


def _legacy_line_for_url(url: str) -> str:
    """teardown 路径同样按域名分线；未知 host 保持旧版 tt 行为。"""
    try:
        provider = provider_for_url(url)
    except UnknownHostError:
        return "tt"
    return "danjuan" if provider == "danjuan" else "tt"


def ttfund_get(url: str, params: Optional[dict] = None,
               headers: Optional[dict] = None, timeout: int = 20, **kwargs):
    """天天/东财/蛋卷统一请求入口：跨进程串行限流(F1) + 熔断(F2) + 强制 HTTPS（坑P1）
    + 会话复用 + 瞬态重试1次（仅网络异常；HTTP 状态码在调用方经 assert_not_risk_control
    分类——403/429 是风控信号，不重试、不降级、计熔断，见 F6）。

    Sprint 2：协调核心激活时改走 Gateway——403/429/风控正文由网关分类并计熔断，
    配额按 URL 域名分 provider（东财/蛋卷各自独立账）；2xx 只代表可达，
    中性结算不清零，业务成功仍由调用方显式 _cbreak_success()。
    """
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if _REQUEST_GATEWAY is not None:
        resp = _REQUEST_GATEWAY.get(
            url, params=params, headers=headers, timeout=timeout, retries=1,
            retry_statuses=(),                         # 仅网络异常重试（legacy 口径）
            session=_SessionAdapter(TT_SESSION, _tt_hit), **kwargs)
        _release_pending(resp)
        return resp
    line = _legacy_line_for_url(url)
    for attempt in (1, 2):
        _cbreak_check(line)                             # F2：按 URL 对应 provider 检查
        _cross_proc_throttle(line, TT_MIN_INTERVAL)     # F1：同 provider 共用节流与闸门
        try:
            TT_REQUEST_COUNT[0] += 1
            host = re.sub(r"^https?://", "", url).split("/")[0]
            TT_REQUEST_COUNT[1][host] = TT_REQUEST_COUNT[1].get(host, 0) + 1
            return TT_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2.0)


def _cache_read(rel: str, max_age_hours: Optional[float]) -> Optional[dict]:
    """读磁盘缓存；rel 相对 FUND_CACHE_DIR。过期/损坏返回 None（损坏即重下，坑见 L5 演练）。

    Sprint 3：core 激活时走 CacheRepository.read（raw JSON 同路径，miss/损坏/
    过期一律 None，与直读 JSON 语义等价）；teardown 模式回退 legacy 直读。
    """
    if _CACHE_REPO is not None:
        return _CACHE_REPO.read(
            rel, None if max_age_hours is None else max_age_hours * 3600)
    p = FUND_CACHE_DIR / rel
    if not p.exists():
        return None
    if max_age_hours is not None:
        age_h = (time.time() - p.stat().st_mtime) / 3600
        if age_h > max_age_hours:
            return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None                                    # 缓存损坏 → 视为 miss 重下


def _cache_write(rel: str, payload: "dict | list") -> None:
    """原子写（tmp+rename）：进程中断不留半截文件（G2；损坏兜底仍在 _cache_read）。

    Sprint 3：core 激活时走 CacheRepository.write（mkstemp+rename，无 schema
    标记，与 legacy 落盘 JSON 语义等价——分隔符为紧凑格式，且 NaN/Inf 直接
    拒绝而非落盘 NaN 字面量）；teardown 模式回退 legacy 直写。
    """
    if _CACHE_REPO is not None:
        _CACHE_REPO.write(rel, payload)
        return
    p = FUND_CACHE_DIR / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
```

## 层 1 · 前置层：全量基金清单 + 归一化

```python

_FUND_LIST_MEM: list[dict] = []     # 进程级缓存（同一会话多次调用零 IO）


def fund_list(force_refresh: bool = False) -> list[dict]:
    """全量基金清单 ~2.8万只：[{code, pinyin, name, type}, ...]

    E1: fund.eastmoney.com/js/fundcode_search.js（首载 1 请求，之后磁盘 30 天 / 进程内 0 开销）

    Sprint 3（get_or_load 榜样）：core 激活时冷启动并发收敛（跨进程锁内单一装载，
    follower 等待后直读）；装载器内完成业务完整性校验并通过后才写缓存/清零——
    loader 成功=业务成功，天然对齐 Sprint 1。retry_after=0：结构异常后立刻重试
    允许（保持既有契约）；StaleDataError 保真上抛诱因（旧缓存不改变异常类型）。
    进程内快路径仍由 _FUND_LIST_MEM 负责（repo 内存层有意不用，避免双层缓存）。
    """
    if _FUND_LIST_MEM and not force_refresh:
        return _FUND_LIST_MEM

    def _load() -> list[dict]:
        url = "https://fund.eastmoney.com/js/fundcode_search.js"
        r = ttfund_get(url, headers={"Referer": "https://fund.eastmoney.com/"})
        assert_not_risk_control(r, "tt", "E1 清单")
        r.raise_for_status()
        text = r.text
        brace = text.find("[")
        if brace < 0:
            raise RuntimeError(f"基金清单响应异常（无 JSON 数组）: {text[:120]!r}")
        rows, _ = json.JSONDecoder().raw_decode(text[brace:])
        loaded = [{"code": x[0], "pinyin": x[1], "name": x[2], "type": x[3]} for x in rows]
        if len(loaded) < 5000:                        # 正常 ~2万条，骤降=上游结构变了
            raise RuntimeError(f"基金清单仅 {len(loaded)} 条，疑似上游结构变更，拒绝缓存脏数据")
        _cbreak_success("tt")
        return loaded

    if _CACHE_REPO is not None:
        try:
            result = _CACHE_REPO.get_or_load(
                "fund_list.json",
                CachePolicy(ttl_seconds=FUND_LIST_TTL_SECONDS,   # core 常量，与 30 天口径单一来源
                            retry_after_seconds=0.0),
                _load, force_refresh=force_refresh)
        except StaleDataError as stale:
            raise stale.__cause__ if stale.__cause__ is not None else stale
        _FUND_LIST_MEM[:] = result.data
        return _FUND_LIST_MEM
    cached = None if force_refresh else _cache_read(
        "fund_list.json", max_age_hours=FUND_LIST_TTL_DAYS * 24)
    if cached:
        _FUND_LIST_MEM[:] = cached
        return _FUND_LIST_MEM
    loaded = _load()
    _cache_write("fund_list.json", loaded)
    _FUND_LIST_MEM[:] = loaded
    return loaded


def norm_fund_code(query: str) -> dict:
    """基金代码/名称/拼音 → 归一化结果。

    返回 {query, matches: [{code,name,type,pinyin}], resolved, note}
      - 6 位代码且在基金清单 → resolved=code
      - 6 位代码但不在清单 → ValueError（坑P4：可能是股票代码，引导 a-stock-data）
      - 名称/拼音 → 精确→包含匹配；多候选（如 A/C 份额）resolved=None 列出全部，绝不猜
    """
    q = str(query).strip()
    if not q:
        raise ValueError("查询为空：需要 6 位基金代码 / 基金名称 / 拼音缩写")
    digits = q.replace(".OF", "").replace(".of", "")
    if re.fullmatch(r"\d{6}", digits):
        for f in fund_list():
            if f["code"] == digits:
                return {"query": q, "matches": [f], "resolved": digits,
                        "note": f["name"]}
        raise ValueError(
            f"{digits} 不在基金清单（{len(fund_list())} 只）中——它可能是股票代码。"
            f"查询股票请使用 a-stock-data skill；基金代码请核对天天基金页面。")
    # 名称 / 拼音匹配
    fl = fund_list()
    exact = [f for f in fl if f["name"] == q or f["pinyin"].lower() == q.lower()]
    if len(exact) == 1:
        return {"query": q, "matches": exact, "resolved": exact[0]["code"],
                "note": exact[0]["name"]}
    if len(exact) > 1:                                # 典型：同基金多份额（A/C/E/后端）
        return {"query": q, "matches": exact, "resolved": None,
                "note": f"命中 {len(exact)} 只同份额族基金，需指定 6 位代码消歧"}
    contains = [f for f in fl if q in f["name"] or q.lower() in f["pinyin"].lower()]
    if not contains:
        raise ValueError(f"{q!r} 未匹配到任何基金（清单 {len(fl)} 只）。"
                         f"建议用 6 位代码或完整基金名称。")
    if len(contains) == 1:
        return {"query": q, "matches": contains, "resolved": contains[0]["code"],
                "note": contains[0]["name"]}
    contains.sort(key=lambda f: len(f["name"]))       # 短名优先，取前 8 个供选择
    return {"query": q, "matches": contains[:8], "resolved": None,
            "note": f"模糊命中 {len(contains)} 只，请从候选中选择"}


def resolve_fund_code(query: str) -> str:
    """norm_fund_code 的便捷包装：唯一命中返回代码，多候选/未命中直接抛错。"""
    r = norm_fund_code(query)
    if r["resolved"]:
        return r["resolved"]
    cands = " / ".join(f"{f['code']}{f['name']}" for f in r["matches"][:5])
    raise ValueError(f"{query!r} 有多个候选：{cands} ...")
```

## 层 2 · 端点层（模块 L4 + V0.1 端点）

### fund_snapshot（E2 ⭐主端点 + 自动降级）

```python

_PZ_VARS_KNOWN = [
    "fS_name", "fS_code", "fund_sourceRate", "fund_Rate", "fund_minsg", "ishb",
    "stockCodes", "stockCodesNew", "zqCodes", "zqCodesNew",
    "syl_1n", "syl_1y", "syl_3y", "syl_6y",
    "Data_netWorthTrend", "Data_ACWorthTrend", "Data_grandTotal",
    "Data_rateInSimilarType", "Data_rateInSimilarPersent",
    "Data_fluctuationScale", "Data_holderStructure", "Data_assetAllocation",
    "Data_performanceEvaluation", "Data_fundSharesPositions",
    "Data_currentFundManager", "Data_buySedemption", "Data_industryAllocation",
]


def _parse_js_vars(text: str) -> dict:
    """pingzhongdata 的 `var X = <value>;` 全量解析。

    value 形态：JSON 数组/对象、双引号字符串、true/false、裸数字。
    🔴 值后面常挂 `;/*注释*/` 再接下一个 var——必须按"相邻两个 var 声明的位置"切片
    并剥离块注释，否则字符串值会吞掉后面一整段（实测 syl_1y 就这样坏掉过）。
    坑P3：按基金类型选择性存在 → 未出现的变量不在结果里，由调用方记入 missing。
    """
    ms = list(re.finditer(r"var\s+([A-Za-z_0-9]+)\s*=", text))
    out = {}
    for i, m in enumerate(ms):
        name = m.group(1)
        if name in out:
            continue
        seg = text[m.end(): ms[i + 1].start() if i + 1 < len(ms) else len(text)]
        seg = re.sub(r"/\*.*?\*/", "", seg, flags=re.S).strip().rstrip(";").strip()
        if not seg:
            continue
        if seg[0] in "{[":
            try:
                out[name], _ = json.JSONDecoder().raw_decode(seg)
                continue
            except json.JSONDecodeError:
                pass
        sm = re.match(r'^"((?:[^"\\]|\\.)*)"', seg)
        if sm:
            out[name] = sm.group(1)
            continue
        if seg in ("true", "false"):
            out[name] = (seg == "true")
            continue
        try:
            out[name] = float(seg) if ("." in seg or "e" in seg.lower()) else int(seg)
        except ValueError:
            out[name] = seg                            # 未知形态原样保留，绝不静默丢
    return out


def _div_from_unit_money(unit_money) -> float:
    """unitMoney → 每份现金分红（元）。

    ⚠️ 坑P6（实测）：unitMoney 事件标记存在但金额常被上游剥离（"分红：每10份派现金元"），
    且"拆分：每份基金份额折算份"混在同一字段——拆分不是现金分红，恒返回 0。
    本函数仅作 adjusted_nav 的回退路径；主路径是链接官方 equityReturn。
    支持"每10份派现金0.5元"（÷10）、"每份派现金0.0100元"（÷1）及 E3 裸数值
    （每份金额，2025-09-22 华夏成长 000001 实测 "0.01"）三种形态。
    """
    s = str(unit_money or "").strip()
    if re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", s):
        value = float(s)
        return value if math.isfinite(value) and value >= 0 else 0.0
    if not s or "折算" in s or "拆分" in s:
        return 0.0                                    # 空 或 拆分/折算事件：无现金分红
    if "分红" not in s and "派" not in s:
        return 0.0                                    # 无现金分红标记
    m = re.search(r"每(\d+)份[^0-9]*([\d.]+)\s*元", s)
    if m:
        return float(m.group(2)) / float(m.group(1))
    m = re.search(r"([\d.]+)\s*元", s)                # 无"每N份"限定的兜底形态（E3口径）
    return float(m.group(1)) if m else 0.0


def _pair_rows_to_dicts(raw) -> list[dict]:
    """E2 通用表格形态 [[label...],[v1...],[v2...]] → [dict,...]（首行当表头）。"""
    if not raw or not isinstance(raw, list):
        return []
    rows = [r for r in raw if isinstance(r, list) and r]
    if len(rows) < 2:
        return []
    head = [str(x) for x in rows[0]]
    return [dict(zip(head, r)) for r in rows[1:]]


def _echarts_to_records(obj) -> list[dict]:
    """E2 echarts 表格 → [dict,...]。两种系列形态分流（坑P18，2026-11-09 实测）：

    形态一（列式，持有人结构等）：{'categories':[...], 'series':[{name, data}...]}
      → [{_date, <系列名>: 值}, ...]
    形态二（行式，fluctuationScale 规模等）：{'categories':[...], 'series':[{y, mom}, ...]}
      series[i] 与 categories[i] 逐期对应、无 name/data 列键——旧实现只认形态一，
      行式值全丢（规模史只剩 _date 光杆行）。
    """
    if not isinstance(obj, dict):
        return []
    cats = obj.get("categories") or []
    series = [s for s in (obj.get("series") or []) if isinstance(s, dict)]
    if not cats or not series:
        return []
    if all(s.get("data") is None for s in series):     # 形态二：行式 {y, mom}
        recs = []
        for i, c in enumerate(cats):
            if i >= len(series):
                break
            row = {"_date": str(c)}
            row.update({k: v for k, v in series[i].items()})
            recs.append(row)
        return recs
    cols = {}                                          # 形态一：列式 {name, data}
    for s in series:
        if s.get("data") is not None:
            cols[str(s.get("name", "value"))] = s["data"]
    recs = []
    for i, c in enumerate(cats):
        row = {"_date": str(c)}
        for k, arr in cols.items():
            row[k] = arr[i] if i < len(arr) else None
        recs.append(row)
    return recs


def _normalize_table(raw) -> list[dict]:
    """E2 表格变量双格式兼容：echarts dict 或 [[label行],[值行]...] 嵌套数组 → [dict,...]。

    不同变量/不同版本两种形态都出现过，按实际类型分流，都不匹配返回 []（进 missing 告知）。
    """
    if isinstance(raw, dict):
        return _echarts_to_records(raw)
    if isinstance(raw, list):
        return _pair_rows_to_dicts(raw)
    return []


def _ms_to_date(ms) -> str:
    """E2 毫秒时间戳 → YYYY-MM-DD（北京时区，与 snapshot 净值口径一致）。"""
    return datetime.fromtimestamp(int(ms) / 1000, _BJ_TZ).strftime("%Y-%m-%d")


def _xy_pairs_to_records(raw, value_key: str = "value") -> list[dict]:
    """E2 时序对 [[ts_ms, v], ...] → [{"date", value_key}]（坑P18 关联修复）。

    fundSharesPositions（仓位）/ rateInSimilarPersent（同类百分位）/ grandTotal.data
    都是这个形态——旧路径走 _pair_rows_to_dicts 会把首对当表头行，产出
    {"时间戳串": 时间戳} 乱码（2026-11-09 实测修复）。
    任一行不是二元可数值化对 → 整体判非此形态返回 []（宁缺毋错，交回表格分流）。
    """
    if not isinstance(raw, list):
        return []
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return []
        try:
            out.append({"date": _ms_to_date(p[0]), value_key: float(p[1])})
        except (TypeError, ValueError, OverflowError):
            return []
    return out


def _scale_history(raw) -> list[dict]:
    """Data_fluctuationScale（行式 {y: 规模亿, mom: 环比}）→ [{_date, scale_yi, mom_pct}]。

    兼容旧列式（系列名"规模"）以防上游回切。下游评测 skill 的份额兜底路径由此修复为正路。
    """
    recs = _normalize_table(raw)
    out = []
    for r in recs:
        v = r.get("scale_yi", r.get("y", r.get("规模")))
        out.append({"_date": r.get("_date"),
                    "scale_yi": float(v) if isinstance(v, (int, float)) else None,
                    "mom_pct": r.get("mom")})
    return out


def _benchmark_curves(raw) -> list[dict]:
    """Data_grandTotal → [{"name", "points": [{"date", "return_pct"}]}]（V0.4.0 B1）。

    系列实测为 [基金简称, "同类平均", "沪深300"] 三条；窗口约近半年（126 交易日）
    非全史；值 = 窗口起点以来累计收益%（起点 0）。旧版只把它挂在 _PZ_VARS_KNOWN
    防缺失清单里从未输出。
    """
    out = []
    if not isinstance(raw, list):
        return out
    for s in raw:
        if not isinstance(s, dict):
            continue
        pts = _xy_pairs_to_records(s.get("data"), "return_pct")
        if pts:
            out.append({"name": str(s.get("name", "")), "points": pts})
    return out


def _codes_list(x) -> list[str]:
    """stockCodes/zqCodes → 纯 6 位代码列表。

    双格式兼容：JSON 数组（实测 stockCodesNew 是 ["1.600519",...] secid 形态）或
    逗号分隔字符串。secid 前缀（0./1. 市场号）剥掉——喂 a-stock-data 用纯 6 位最通用。
    """
    if not x:
        return []
    raw = x if isinstance(x, list) else str(x).split(",")
    out = []
    for c in raw:
        c = str(c).strip()
        c = re.sub(r"^[01]\.", "", c) if re.fullmatch(r"[01]\.\d{6}", c) else c
        if c:
            out.append(c)
    return out


def fund_snapshot(query: str, force_refresh: bool = False) -> dict:
    """单基金一站式快照（E2 pingzhongdata，436KB → 20+ 数据集）。

    ⭐ 主端点：常态诊断只花这 1 个请求（当日磁盘缓存命中则 0 请求）。
    E2 网络失败或真正空体走分拆降级（E3+E4a+蛋卷）；风控页与结构异常显式上抛，
    不触发多页降级。降级返回体 source 标注 "degraded"，绝不静默空。
    返回结构见 keys；缺失数据集列入 missing（坑P3），绝不静默为空。
    """
    code = resolve_fund_code(query)
    rel = f"snapshots/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached and cached.get("_meta", {}).get("version") == __version__:
            return cached

    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    try:
        r = ttfund_get(url, headers={"Referer": "https://fund.eastmoney.com/"})
        assert_not_risk_control(r, "tt", "E2 快照")
        r.raise_for_status()
    except requests.RequestException as e:
        return _snapshot_degraded(code, reason=f"E2 网络失败: {e}")
    text = r.content.decode("utf-8", errors="replace")
    _assert_not_risk_content(text, "tt", "E2 快照")
    # 只有零字节响应才按瞬态上游异常降级；纯空白等非空畸形体走结构校验。
    if not r.content:
        return _snapshot_degraded(code, reason="E2 响应为空，疑似瞬态上游异常")
    v = _parse_js_vars(text)
    missing = [k for k in _PZ_VARS_KNOWN if k not in v]
    # 非风控的结构异常不触发多页降级：避免把上游改版误判为网络故障。
    if not v.get("fS_name") or ("Data_netWorthTrend" not in v and not v.get("ishb")):
        raise UpstreamFormatError(
            "E2 响应缺少关键变量（fS_name/netWorthTrend），拒绝生成空快照或触发分拆降级")

    nav = [{"date": datetime.fromtimestamp(int(p["x"]) / 1000, _BJ_TZ).strftime("%Y-%m-%d"),
            "nav": float(p["y"]),
            "daily_return": None if p.get("equityReturn") in (None, "") else float(p["equityReturn"]),
            "dividend": _div_from_unit_money(p.get("unitMoney"))}
           for p in (v.get("Data_netWorthTrend") or []) if isinstance(p, dict) and "x" in p]
    ac_nav = [{"date": datetime.fromtimestamp(int(p["x"]) / 1000, _BJ_TZ).strftime("%Y-%m-%d"),
               "ac_nav": float(p["y"])}
              for p in (v.get("Data_ACWorthTrend") or []) if isinstance(p, dict) and "x" in p]

    mgr_raw = (v.get("Data_currentFundManager") or [None])[0] or {}
    scale_hist = _scale_history(v.get("Data_fluctuationScale"))
    position_hist = _xy_pairs_to_records(v.get("Data_fundSharesPositions"), "position_pct")
    peer_hist = _xy_pairs_to_records(v.get("Data_rateInSimilarPersent"), "percentile")
    bench_curves = _benchmark_curves(v.get("Data_grandTotal"))
    holder_structure = _normalize_table(v.get("Data_holderStructure"))
    asset_allocation = _normalize_table(v.get("Data_assetAllocation"))
    industry_allocation = _normalize_table(v.get("Data_industryAllocation"))
    # P18 关联自洽：变量存在但形态解析失败 ≠「E2 没有」——P3 只查键缺失，
    # 所有经过结构转换的数据集统一标记，消费者才能区分"没有"与"解析失败"。
    for name, parsed in (("Data_netWorthTrend", nav), ("Data_ACWorthTrend", ac_nav),
                         ("Data_fluctuationScale", scale_hist),
                         ("Data_fundSharesPositions", position_hist),
                         ("Data_rateInSimilarPersent", peer_hist),
                         ("Data_grandTotal", bench_curves),
                         ("Data_holderStructure", holder_structure),
                         ("Data_assetAllocation", asset_allocation),
                         ("Data_industryAllocation", industry_allocation)):
        raw = v.get(name)
        empty_echarts = (isinstance(raw, dict)
                         and ("categories" in raw or "series" in raw)
                         and not raw.get("categories") and not raw.get("series"))
        if raw and not parsed and not empty_echarts:
            missing.append(f"{name}(present,unparsed)")
    snap = {
        "_meta": {"code": code, "version": __version__,
                  "fetched_at": datetime.now().isoformat(timespec="seconds"),
                  "source": "eastmoney/pingzhongdata"},
        "name": v.get("fS_name"),
        "is_money_fund": bool(v.get("ishb")),          # 仅类型标注：无货基专项口径（坑P15）
        "fees": {"origin_rate": v.get("fund_sourceRate"),
                 "current_rate": v.get("fund_Rate"),
                 "min_buy": v.get("fund_minsg")},
        "period_returns_pct": {                        # 官方口径收益率（也是测试 ground truth）
            "1month": _to_float(v.get("syl_1y")), "3month": _to_float(v.get("syl_3y")),
            "6month": _to_float(v.get("syl_6y")), "1year": _to_float(v.get("syl_1n"))},
        "manager": {"name": mgr_raw.get("name"), "manager_id": mgr_raw.get("id"),
                    "total_tenure": mgr_raw.get("workTime"),          # 累计任职时长文本（EM workTime）
                    "current_scale_text": mgr_raw.get("fundSize"),   # "66.83亿(6只基金)" 带单位字符串
                    "return_chart": mgr_raw.get("profit"),           # 任期收益图表对象（三系列）
                    "star": mgr_raw.get("star")},
        "nav_history": nav,                            # 单位净值全史（含每日分红事件）
        "ac_nav_history": ac_nav,
        "top_stocks": _codes_list(v.get("stockCodesNew") or v.get("stockCodes")),
        "top_bonds": _codes_list(v.get("zqCodesNew") or v.get("zqCodes")),
        "scale_history": scale_hist,
        "holder_structure": holder_structure,
        "asset_allocation": asset_allocation,
        "stock_position_history": position_hist,
        "industry_allocation": industry_allocation,
        "peer_rank_history": peer_hist,
        "benchmark_curves": bench_curves,
        "buy_redemption_status": v.get("Data_buySedemption"),
        "missing_datasets": missing,                   # 坑P3：显式列出，调用方知情
    }
    # V0.7.7 申购限额（主页面独立小请求）：失败/熔断不炸快照——风控信号已由
    # ttfund_get 内部计数，此处只吞异常并标注 missing（E2 成果不该陪葬）。
    _cbreak_success("tt")          # E2 业务校验通过才有资格清零（须在限额页 try 前：
                                   # 限额页 403 被吞时若在其后清零，连击永远凑不满）
    try:
        snap["purchase_limit"] = fund_purchase_limit(code)
    except (RuntimeError, requests.RequestException):
        snap["purchase_limit"] = None
        missing.append("purchase_limit(fetch-failed)")
    _cache_write(rel, snap)
    return snap


def _snapshot_degraded(code: str, reason: str = "", years: float = 1.0) -> dict:
    """E2 失效时的分拆降级：E3 净值(近N年) + E4a 持仓 + 蛋卷 D1 档案。

    ⚠️ 降级快照不写缓存（下次调用会重试 E2 主端点）；数据集缺口显式列入 missing；
    净值窗口默认近 1 年（F3：原 3 年=37 页连发，批量场景会形成请求风暴；
    需要更长历史可显式传 years，单基金场景代价可接受）。
    """
    missing: list[str] = ["Data_* 系列（E2 整体不可用）",
                          "purchase_limit(degraded-skip)"]   # V0.7.7：降级期不追打主页面（同域故障概率高），显式标注
    start = (date.today() - timedelta(days=int(years * 365))).isoformat()
    hist = fund_nav_history(code, start=start)        # E3 兜底净值（可能抛错→由调用方感知）
    if not hist:
        raise RuntimeError(f"降级失败：{code} 在 {start} 起无净值记录（E2/E3 双不可用或代码有误）")
    nav_hist = [{"date": h["date"], "nav": h["nav"],        # 🔴 E3 原生新→旧（坑P7），
                 "daily_return": h["daily_return_pct"],     # 必须反转为旧→新与 E2 口径一致，
                 "dividend": _div_from_unit_money(h.get("dividend"))}   # 否则指标方向全反
                for h in hist[::-1]]
    top_stocks: list[str] = []
    try:
        hold = fund_holdings(code)                    # E4a 持仓
        if hold:
            latest = hold[0]["period"]
            top_stocks = [h["stock_code"] for h in hold if h["period"] == latest]
    except (RiskControlError, CircuitOpenError):
        raise
    except Exception:
        missing.append("stockCodes/E4a")
    dj: dict = {}
    try:
        dj = fund_detail_danjuan(code)                # 蛋卷档案（跨域）
    except (RiskControlError, CircuitOpenError):
        raise
    except Exception:
        missing.append("danjuan/D1")
    return {
        "_meta": {"code": code, "version": __version__,
                  "fetched_at": datetime.now().isoformat(timespec="seconds"),
                  "source": "degraded(E3+E4a+D1)", "reason": reason},
        "name": dj.get("name"), "is_money_fund": None,
        "purchase_limit": None,                       # V0.7.7：降级期不拉主页面（见 missing 标注）
        "manager": {"name": dj.get("manager")},
        "nav_history": nav_hist,
        "ac_nav_history": [], "top_stocks": top_stocks, "top_bonds": [],
        "fees": {}, "period_returns_pct": {},
        "scale_history": [], "holder_structure": [], "asset_allocation": [],
        "stock_position_history": [], "industry_allocation": [],
        "peer_rank_history": [], "benchmark_curves": [],
        "buy_redemption_status": None,
        "missing_datasets": missing,
        "note": f"降级快照：净值窗口近{years}年（非全史）；原因={reason}",
        "window_years": years,
    }
```

```python

class _StaticCellParser(HTMLParser):
    """交易状态栏 staticCell span 文本提取（嵌套感知）。

    金额与「限大额」分属不同层级 text node（实测 000369：外层 span 只收到
    「限大额 (」，「单日累计购买上限N元」在嵌套子 span）——depth 计数期间
    持续收集 handle_data，单个 cell 输出拼接全文（简单 regex 必截断）。
    """

    def __init__(self):
        super().__init__()
        self._depth = 0
        self._buf = ""
        self.cells: list = []

    def handle_starttag(self, tag, attrs):
        if tag != "span":
            return
        if self._depth == 0 and "staticCell" in (dict(attrs).get("class") or "").split():
            self._depth, self._buf = 1, ""
        elif self._depth:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag == "span" and self._depth:
            self._depth -= 1
            if not self._depth:
                self.cells.append(" ".join(self._buf.split()))

    def handle_data(self, data):
        if self._depth:
            self._buf += data


# 近似折算汇率（静态快照口径，非实时牌价）——外币份额限额折 CNY 仅供比较/排序
_LIMIT_FX_DEFAULT = {"万元": 1e4, "元": 1.0, "美元": 7.2, "港币": 0.92,
                     "欧元": 7.8, "英镑": 9.2, "日元": 0.05}
_LIMIT_AMOUNT_RE = re.compile(
    r"单日累计购买上限\s*([0-9][0-9,.]*)\s*(万元|元|美元|港币|欧元|英镑|日元)")


def parse_purchase_limit(status_text: str, fx: Optional[dict] = None) -> dict:
    """交易状态文本 → 结构化限额（纯函数，零网络）。

    kind 四态：limited / suspended（暂停申购，limit_cny=0——一分买不了）/
    unlimited（开放申购无限额）/ unknown（封闭期、募集期等未覆盖形态——
    limit_cny=None 诚实留空，绝不冒充无限额）。
    金额优先于状态词：暂停申购文本若附带金额仍提取（kind=limited）。
    「限大额」但金额形态未识别 → limited + limit_cny=None（上游改版预警，
    不学早期脚本硬给 0 虚报不可买）。外币按近似汇率折 CNY（fx 可覆盖），
    精确额度以 limit_raw 原文为准。
    """
    text = str(status_text or "")
    m = _LIMIT_AMOUNT_RE.search(text)
    if m:
        unit = m.group(2)
        try:
            rate = float((fx or {}).get(unit, _LIMIT_FX_DEFAULT[unit]))
            amount = round(float(m.group(1).replace(",", "")) * rate, 2)
        except (ValueError, TypeError):
            amount = None
        return {"kind": "limited", "limit_cny": amount, "currency": unit,
                "limit_raw": m.group(0)}
    if "暂停申购" in text:
        return {"kind": "suspended", "limit_cny": 0.0, "currency": None,
                "limit_raw": "暂停申购"}
    if "限大额" in text:
        return {"kind": "limited", "limit_cny": None, "currency": None,
                "limit_raw": text.strip() or "限大额"}
    if "开放申购" in text:
        return {"kind": "unlimited", "limit_cny": None, "currency": None,
                "limit_raw": "开放申购"}
    return {"kind": "unknown", "limit_cny": None, "currency": None,
            "limit_raw": text.strip()}


def fund_purchase_limit(query: str, force_refresh: bool = False) -> dict:
    """单基金申购限额/交易状态（主页面 {code}.html，1 请求，当日缓存）。

    E2/E3/E6 实测均无完整限额信息（E3 SGZT 仅「限大额」短文本无金额），
    唯一来源是主页面交易状态栏。输出：
      {code, name, status_text 交易状态原文（申购/赎回 cell 以；拼接）,
       not_for_sale 天天基金暂不代销, kind, limit_cny, currency, limit_raw}
    kind/折算语义见 parse_purchase_limit。页面无交易状态栏（已清盘/新发
    未开购/结构变更）→ kind="unknown" 而非报错——按只容错，批量不中断。
    ⚠️ 批量循环 = N 只 N 请求（主页面无批量接口），tt 线 1.5s 自动节流，
    400 只约 10 分钟；当日缓存后同日重查 0 请求。
    """
    code = resolve_fund_code(query)
    rel = f"purchase_limits/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached
    r = ttfund_get(f"https://fund.eastmoney.com/{code}.html",
                   headers={"Referer": "https://fund.eastmoney.com/"})
    assert_not_risk_control(r, "tt", f"主页面 {code}")
    r.raise_for_status()
    h = r.content.decode("utf-8", errors="replace")
    _assert_not_risk_content(h, "tt", f"主页面 {code}")   # 200+风控文案页不落缓存
    m = re.search(r"<title>([^<\-]{2,40})", h)
    name = ((m.group(1).strip() if m else "").split("(")[0]
            .split("_")[0].split("-")[0].strip()) or None
    m = re.search(r"交易状态：</span>(.*?)</div>", h, re.DOTALL)
    cells = []
    if m:
        parser = _StaticCellParser()
        parser.feed(m.group(1))
        cells = [c for c in parser.cells if c]   # 滤空 cell：噪声空串不得占 cells[0]
    status_text = "；".join(cells)
    # kind 只看申购 cell（cells[0]）——赎回 cell 里的「限大额/暂停」词样不得
    # 污染申购判定（实测存在「开放申购；限大额赎回」形态：开放可买被误报受限）
    parsed = parse_purchase_limit(cells[0]) if cells else parse_purchase_limit("")
    out = {"code": code, "name": name,
           "status_text": status_text or "未找到交易状态栏",
           "not_for_sale": "该基金暂不开放购买" in h, **parsed}
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out
```

### fund_nav_history（E3 补充端点：区间 + 每日申赎状态）

```python

LSJZ_PAGE_SIZE = 20                                   # 坑P2：服务端实际钳制 pageSize=20（实测）


def fund_nav_history(query: str, start: str = "", end: str = "",
                     max_pages: int = 80) -> list[dict]:
    """历史净值逐页（E3 lsjz）。定位为补充端点——E2 已含净值全史，本端点独有价值是按日申赎状态史与 E2 失效时的净值兜底。

    坑P2 安全用法（2026-11-05 实测钉死）：
      - pageSize 服务端钳制为 20：请求 49/50/100/200 都只回 20 条，500 直接静默 0 条
      - TotalCount 在响应【顶层】不在 Data 里，读错位置=total 恒 0=分页循环第一页就断
      - 每页校验：TotalCount 与实收条数不一致即抛错（防上游静默截断）
    返回 [{date, nav, ac_nav, daily_return_pct, buy_status, redeem_status, dividend}]（新→旧）。
    """
    code = resolve_fund_code(query)
    if not start:                                     # 默认近一年，避免无区间的不稳定行为
        start = (date.today() - timedelta(days=365)).isoformat()
    if not end:
        end = date.today().isoformat()
    if start > end:
        raise ValueError(f"start({start}) 不得晚于 end({end})")
    rows: list[dict] = []
    total = 0
    for page in range(1, max_pages + 1):
        params = {"fundCode": code, "pageIndex": page, "pageSize": LSJZ_PAGE_SIZE,
                  "startDate": start, "endDate": end}
        r = ttfund_get("https://api.fund.eastmoney.com/f10/lsjz", params=params,
                       headers={"Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html"})
        assert_not_risk_control(r, "tt", "E3 lsjz")
        r.raise_for_status()
        d = r.json()
        if d.get("ErrCode") not in (None, 0):
            raise RuntimeError(f"lsjz 接口返回失败: {d.get('ErrMsg')}")
        lst = (d.get("Data") or {}).get("LSJZList") or []
        total = int(d.get("TotalCount") or 0)          # 🔴 顶层字段，不在 Data 内
        rows.extend({
            "date": x["FSRQ"], "nav": float(x["DWJZ"]), "ac_nav": float(x["LJJZ"]),
            "daily_return_pct": None if x.get("JZZZL") in (None, "") else float(x["JZZZL"]),
            "buy_status": x.get("SGZT"), "redeem_status": x.get("SHZT"),
            "dividend": x.get("FHFCZ") or None,
        } for x in lst if x.get("FSRQ"))
        if not lst or (total > 0 and len(rows) >= total):
            break
    if total and len(rows) != total:
        raise RuntimeError(                            # 坑P2 核心防线
            f"lsjz 分页校验失败：TotalCount={total}，实收 {len(rows)} 条"
            f"（区间 {start}~{end}）。若因 max_pages={max_pages} 用尽，可调大 max_pages "
            f"或缩小区间；否则为上游静默截断，拒绝返回不完整数据。")
    if not total and rows:
        raise RuntimeError(f"lsjz 响应缺 TotalCount 顶层字段但返回了 {len(rows)} 条数据，"
                           f"响应结构异常，拒绝返回未校验的数据。")
    if not rows:
        raise RuntimeError(f"{code} 在 {start}~{end} 无净值记录"
                           f"（基金未成立/已清盘/区间早于成立日/代码有误），拒绝静默返回空。")
    _cbreak_success("tt")
    return rows
```

### fund_holdings（E4a 季度前十大，可按年份拉历史）

```python

def _html_table_rows(html: str) -> list[list[str]]:
    """F10 content 里的 <table> → 行列表（仅依赖标准库，不依赖 lxml）。"""
    body = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.S)
    if not body:
        return []
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1), re.S):
        cells = [re.sub(r"<[^>]+>|\s+", "", c)
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if cells:
            rows.append(cells)
    return rows


def fund_holdings(query: str, year: Optional[int] = None,
                  force_refresh: bool = False) -> list[dict]:
    """季度股票持仓前十大（E4a FundArchivesDatas type=jjcc）。

    与 E2 的 stockCodes 互为校验；year 指定时返回该年各季报持仓（历史穿透）。
    force_refresh=True 绕过当日缓存重新拉取。
    ⚠️ 口径：季度报告期前十大重仓（全部持仓明细在年报 PDF，属公告层，不在本 skill 范围）。
    列序（实测 2026-11）：0序号 1代码 2名称 3最新价 4涨跌幅 5资讯 6占净值% 7持股数(万股) 8市值(万元)
    """
    code = resolve_fund_code(query)
    rel = f"holdings/{code}_{year or 'latest'}.json"      # F4：季报数据当日缓存（原每次现查）
    cached = None if force_refresh else _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
    if cached is not None:
        return cached
    params = {"type": "jjcc", "code": code, "topline": "10",
              "year": str(year) if year else "", "month": ""}
    r = ttfund_get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx", params=params,
                   headers={"Referer": f"https://fundf10.eastmoney.com/ccmx_{code}.html"})
    assert_not_risk_control(r, "tt", "E4a jjcc")
    r.raise_for_status()
    m = re.search(r'content:"(.*)"\s*,\s*arryear', r.text, re.S) or \
        re.search(r'content:"(.*)"\s*}', r.text, re.S)
    if not m:
        raise RuntimeError(f"jjcc 响应无 content（{code}）：{r.text[:120]!r}")
    html = m.group(1)
    # 每个季度一张表；报告期日期在表外 h4（"截止至：<font>2026-09-30</font>"），需按表定位
    out = []
    prev_end = 0
    tables = list(re.finditer(r"<table[^>]*>.*?</table>", html, re.S))
    if not tables:
        raise RuntimeError(f"jjcc 持仓表为空（{code}）——债基/货基可能无股票持仓，"
                           f"请检查 fund_snapshot().top_stocks 或 zqCodes。")
    for t in tables:
        header_zone = html[prev_end:t.start()]
        prev_end = t.end()
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", header_zone)
        period = dates[-1] if dates else None
        # 列序防线（P-C 升级）：不再按下标盲取——从 thead 解析「列名→索引」映射动态取列，
        # 上游保留文案但调整列位（如插一列涨跌额）时旧防线会静默错列（F10 家族有改列前科）。
        thead = re.search(r"<thead[^>]*>(.*?)</thead>", t.group(0), re.S)
        head_cells = ([re.sub(r"<[^>]+>|\s+", "", c)
                       for c in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1), re.S)]
                      if thead else [])
        if not head_cells:
            raise RuntimeError(f"jjcc 表头缺失/无 th 列（{code}，报告期 {period}），拒绝盲取")

        def col_idx(keyword: str) -> int:
            for i, h in enumerate(head_cells):
                if keyword in h:
                    return i
            raise RuntimeError(f"jjcc 表头缺 {keyword!r} 列（{code}，报告期 {period}）："
                               f"{head_cells}——上游列序变更，拒绝盲取")

        i_seq = col_idx("序号")
        i_code, i_name = col_idx("股票代码"), col_idx("股票名称")   # 全称做关键词：
        # "代码"/"名称"过短，上游若插"行业代码"之类列会错列命中
        i_pct, i_shares, i_value = col_idx("占净值"), col_idx("持股数"), col_idx("市值")
        i_max = max(i_seq, i_code, i_name, i_pct, i_shares, i_value)
        for cells in _html_table_rows(t.group(0)):
            if len(cells) > i_max and cells[i_seq].isdigit():   # 序号也走映射——上游删序号列时
                out.append({"period": period, "seq": int(cells[i_seq]),   # 代码列 isdigit 会冒充序号
                            "stock_code": cells[i_code], "stock_name": cells[i_name],
                            "nav_pct": _to_float(cells[i_pct]),
                            "shares_wan": _to_float(cells[i_shares]),
                            "value_wan": _to_float(cells[i_value])})
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out


def _to_float(s) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
```

### fund_detail_danjuan（D1 蛋卷跨域备胎）

```python

def fund_detail_danjuan(query: str) -> dict:
    """蛋卷基金详情（D1 djapi/fund/{code}）：总份额/经理/托管/风险等级/申赎状态。

    定位：东财系全挂时的跨域备胎（不同域名不同风控面）。
    ⚠️ 蛋卷 growth 净值端点已死（坑P5），此处只保档案，无净值能力。
    """
    code = resolve_fund_code(query)
    r = ttfund_get(f"https://danjuanfunds.com/djapi/fund/{code}",
                   headers={"Referer": "https://danjuanfunds.com/"})
    assert_not_risk_control(r, "danjuan", "D1 蛋卷")
    r.raise_for_status()
    d = (r.json() or {}).get("data") or {}
    if not d:
        raise RuntimeError(f"蛋卷详情返回空（{code}）")
    out = {
        "code": d.get("fd_code"), "name": d.get("fd_name"), "full_name": d.get("fd_full_name"),
        "type": d.get("fd_type"), "found_date": d.get("found_date"),
        "total_share": d.get("totshare"), "manager": d.get("manager_name"),
        "company": d.get("keeper_name"), "custodian": d.get("trup_name"),
        "risk_level": d.get("risk_level"),
        "subscribe_status": d.get("subscribe_status"), "redeem_status": d.get("withdraw_status"),
        "source": "danjuan",
    }
    _cbreak_success("danjuan")
    return out
```

### fund_realtime_nav（E6 批量实时净值，N 只 = 1 请求）

```python

def fund_realtime_nav(codes: list[str]) -> list[dict]:
    """批量最新净值（E6 fundmobapi FCodes 逗号分隔）。轻量探测（1KB vs E2 的 436KB），
    批量循环场景的省流量通道。⚠️ GSZ 盘中估值字段已恒为 null（官方下线，坑P5 关联）。
    输入按首次出现顺序去重，输出与去重后输入同序；超过 50 只自动分块。
    响应逐一代码校验，缺失、意外或重复代码均拒绝静默返回。"""
    cs = list(dict.fromkeys(resolve_fund_code(c) for c in codes))
    out = []
    for i in range(0, len(cs), 50):
        chunk = cs[i:i + 50]
        params = {"pageIndex": 1, "pageSize": len(chunk), "plat": "Wap", "product": "EFund",
                  "Version": "6.2.8", "deviceid": "Wap", "FCodes": ",".join(chunk)}
        r = ttfund_get("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNFinfo", params=params)
        assert_not_risk_control(r, "tt", "E6 批量净值")
        r.raise_for_status()
        datas = (r.json() or {}).get("Datas") or []
        if (not isinstance(datas, list) or any(
                not isinstance(x, dict) or not isinstance(x.get("FCODE"), str)
                or not re.fullmatch(r"\d{6}", x["FCODE"]) for x in datas)):
            raise RuntimeError("E6 批量净值响应含畸形记录（非对象或缺有效 FCODE），拒绝静默返回。")
        got = [x["FCODE"] for x in datas]
        missing = [c for c in chunk if c not in got]
        unexpected = [c for c in got if c not in chunk]
        duplicates = sorted({c for c in got if got.count(c) > 1})
        if missing or unexpected or duplicates:
            raise RuntimeError(f"E6 批量净值响应代码校验失败：缺失={missing}，"
                               f"意外代码={unexpected}，重复代码={duplicates}；拒绝静默返回。")
        by_code = {x["FCODE"]: x for x in datas if x.get("FCODE")}
        out.extend({"code": c, "name": by_code[c].get("SHORTNAME"),
                    "nav_date": by_code[c].get("PDATE"), "nav": _to_float(by_code[c].get("NAV")),
                    "ac_nav": _to_float(by_code[c].get("ACCNAV")),
                    "daily_return_pct": _to_float(by_code[c].get("NAVCHGRT"))}
                   for c in chunk)
    if cs:
        _cbreak_success("tt")
    return out
```

## 层 3 · 本地计算层（模块 L5，零网络）

```python

def adjusted_nav(nav_records: list[dict]) -> list[dict]:
    """净值序列 → 复权净值（分红再投口径，零网络）。

    主路径（坑P6）：逐日链接官方 equityReturn（daily_return 字段，上游已处理分红/拆分）
      TR_t = TR_{t-1} × (1 + equityReturn_t / 100)
    回退路径（记录无 daily_return 时）：TR_t = TR_{t-1} × (NAV_t + DIV_t) / NAV_{t-1}。
    ⚠️ 直接用单位净值比算收益会漏分红；用 unitMoney 解析分红金额不可靠（金额常被剥离），
    因此凡有官方日收益率就链接它——回退路径仅用于手工构造/极端缺字段场景。
    输入即 fund_snapshot().nav_history；输出首点 = 首日单位净值（元口径，只影响平移不影响比率）。
    """
    if not nav_records:
        raise ValueError("nav_records 为空，无法计算复权净值")
    tr, prev_nav = [], None
    for rec in nav_records:
        nav = float(rec["nav"])
        dr = rec.get("daily_return")
        if prev_nav is None:
            tr.append({"date": rec["date"], "adj_nav": nav})
        elif dr not in (None, ""):                    # 主路径：官方日收益率
            tr.append({"date": rec["date"],
                       "adj_nav": round(tr[-1]["adj_nav"] * (1 + float(dr) / 100), 6)})
        else:                                         # 回退路径：单位净值+分红
            div = float(rec.get("dividend") or 0.0)
            if prev_nav <= 0:
                raise ValueError(f"{rec['date']} 前一日净值为 {prev_nav}，除零")
            tr.append({"date": rec["date"],
                       "adj_nav": round(tr[-1]["adj_nav"] * (nav + div) / prev_nav, 6)})
        prev_nav = nav
    return tr


def _assert_ascending_dates(records: list[dict], ctx: str) -> None:
    """日期序列必须升序（E2 净值升序；E3 lsjz 原生降序——喂指标前必须反转，坑P7；
    peer_rank_history 等一切时序输入同样适用）。"""
    dates = [r["date"] for r in records]
    if dates != sorted(dates):
        raise ValueError(
            f"{ctx}：日期序列非升序（首 {dates[0]} → 末 {dates[-1]}）。"
            f"若数据来自 lsjz(E3) 请先反转——降序序列会让收益/回撤方向全反。")


def fund_metrics(snapshot: dict, start: str = "", end: str = "",
                 risk_free_annual: float = 0.015) -> dict:
    """区间收益/年化/最大回撤/年化波动/夏普/卡玛（复权口径，零网络）。

    年化收益按实际天数复合年化，包括不足一年的区间；短窗口外推需谨慎解读。
    对照 ground truth：snapshot.period_returns_pct 是官方同期收益（测试容差 0.6pct）。
    """
    _assert_ascending_dates(snapshot["nav_history"], "fund_metrics")
    adj = adjusted_nav(snapshot["nav_history"])
    if start or end:
        adj = [a for a in adj if (not start or a["date"] >= start)
               and (not end or a["date"] <= end)]
    if len(adj) < 2:
        raise ValueError(f"区间内净值不足 2 个点（{len(adj)}），无法计算指标"
                         + _reit_not_applicable((snapshot.get("_meta") or {}).get("code")))
    navs = [a["adj_nav"] for a in adj]
    total_ret = navs[-1] / navs[0] - 1
    days = (datetime.strptime(adj[-1]["date"], "%Y-%m-%d")
            - datetime.strptime(adj[0]["date"], "%Y-%m-%d")).days
    annual_ret = (1 + total_ret) ** (365 / max(days, 1)) - 1

    peak, max_dd, dd_end, dd_peak_date = navs[0], 0.0, None, None
    cur_peak_date = adj[0]["date"]
    for a, n in zip(adj, navs):
        if n > peak:
            peak, cur_peak_date = n, a["date"]
        dd = n / peak - 1
        if dd < max_dd:
            max_dd, dd_end, dd_peak_date = dd, a["date"], cur_peak_date

    daily = [navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))]
    var = sum((x - sum(daily) / len(daily)) ** 2 for x in daily) / max(len(daily) - 1, 1)
    vol_annual = (var * 252) ** 0.5
    rf_daily = (1 + risk_free_annual) ** (1 / 252) - 1
    sharpe = ((sum(daily) / len(daily) - rf_daily) / (var ** 0.5)) * (252 ** 0.5) if var > 0 else None
    calmar = annual_ret / abs(max_dd) if max_dd < 0 else None
    fee = None                                         # 现申购费率（E2 fund_Rate；注意不是管理费——
    try:                                              # 管理费/托管费/赎回阶梯走 fund_fees）
        fee = float(snapshot.get("fees", {}).get("current_rate"))
    except (TypeError, ValueError):
        pass
    return {
        "window": {"start": adj[0]["date"], "end": adj[-1]["date"], "days": days, "points": len(adj)},
        "total_return_pct": round(total_ret * 100, 4),
        "annualized_return_pct": round(annual_ret * 100, 4),
        "max_drawdown_pct": round(max_dd * 100, 4),
        "max_drawdown_window": {"peak": dd_peak_date, "trough": dd_end},
        "annualized_volatility_pct": round(vol_annual * 100, 4),
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
        "subscription_fee_pct": fee,                   # 现申购费（V0.7.0 改名，原 mgmt_fee_pct 名不副实）；sharpe 分母未扣费
    }


XIRR_MAX_RATE = 10.0          # XIRR 求解域上限（年化 1000%）；note 文案引用此常量防漂移


def _xirr(cashflows: list, max_rate: float = XIRR_MAX_RATE) -> Optional[float]:
    """分期现金流年化 IRR（二分法，A1）。cashflows: [(date_str, amount)]，投入负/期末市值正。

    有效性：单符号变化现金流（先负后正，定投即此模式）在 (-1, +∞) 恰有唯一根；
    NPV 对 r 非单调（先降后升存在极小值）但两端异号下二分仍收敛到唯一根。
    返回 None 的情形：无符号变化（如清零）、根超出求解域 (-99.99%, max_rate]、
    数值溢出——绝不硬造。
    """
    if len(cashflows) < 2:
        return None
    t0 = datetime.strptime(cashflows[0][0], "%Y-%m-%d")
    ts = [(datetime.strptime(d, "%Y-%m-%d") - t0).days / 365.0 for d, _ in cashflows]

    def npv(r: float) -> float:
        return sum(a / (1.0 + r) ** t for (_, a), t in zip(cashflows, ts))

    lo, hi = -0.9999, max_rate
    try:
        f_lo, f_hi = npv(lo), npv(hi)
    except (OverflowError, ZeroDivisionError):
        return None
    if not (math.isfinite(f_lo) and math.isfinite(f_hi)) or f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6 or hi - lo < 1e-10:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return mid


def fund_dca_backtest(snapshot: dict, amount: float = 1000.0, freq: str = "monthly",
                      start: str = "", end: str = "") -> dict:
    """定投回测（真实复权净值口径，零网络）。

    约定：扣款日=每月（周）首个有净值的交易日，按当日复权价买入；
    期末市值=Σ(amount/adj_t)×adj_end。单位：元（复权序列以首日单位净值起算）。
    freq: monthly | weekly。
    年化双口径（V0.4.0 A1）：naive_annualized_pct（总倍数全窗口年化，忽略分期，
    方向性偏差取决于路径，仅供参考）+ irr_xirr_pct（XIRR 分期投入真年化）。
    """
    if freq not in ("monthly", "weekly"):
        raise ValueError(f"freq 只支持 monthly/weekly，收到 {freq!r}")
    if not amount > 0:
        raise ValueError(f"amount 必须为正数（每期扣款额），收到 {amount!r}")
    _assert_ascending_dates(snapshot["nav_history"], "fund_dca_backtest")
    adj = adjusted_nav(snapshot["nav_history"])
    if start or end:
        adj = [a for a in adj if (not start or a["date"] >= start)
               and (not end or a["date"] <= end)]
    if len(adj) < 2:
        raise ValueError("区间内净值不足 2 个点，无法回测"
                         + _reit_not_applicable((snapshot.get("_meta") or {}).get("code")))
    seen, invest_dates = set(), []
    for a in adj:                                      # 每期首个交易日 = 该期第一条净值
        if freq == "monthly":
            k = a["date"][:7]
        else:
            iso = date.fromisoformat(a["date"]).isocalendar()
            k = (iso.year, iso.week)
        if k not in seen:
            seen.add(k)
            invest_dates.append(a)
    units = sum(amount / a["adj_nav"] for a in invest_dates)
    invested = amount * len(invest_dates)
    final_value = units * adj[-1]["adj_nav"]
    profit = final_value - invested
    years = max((datetime.strptime(adj[-1]["date"], "%Y-%m-%d")
                 - datetime.strptime(adj[0]["date"], "%Y-%m-%d")).days / 365, 1e-9)
    cfs = [(a["date"], -float(amount)) for a in invest_dates]
    cfs.append((adj[-1]["date"], float(final_value)))
    xirr = _xirr(cfs)
    short_note = None
    if len(invest_dates) < 2 or years < 0.25:
        short_note = (f"仅 {len(invest_dates)} 期 / {years:.2f} 年窗口，年化数字为极端外推，"
                      f"读作区间收益更稳妥")
    return {
        "freq": freq, "amount_per_period": amount, "periods": len(invest_dates),
        "first_invest": invest_dates[0]["date"], "last_invest": invest_dates[-1]["date"],
        "total_invested": round(invested, 2), "final_value": round(final_value, 2),
        "profit": round(profit, 2),
        "return_pct": round(profit / invested * 100, 4),
        # ⚠️ 朴素年化=总倍数按全窗口年化，忽略资金分期投入；偏差方向取决于路径（先涨后跌
        # 偏高、稳步上行偏低），仅供参考——分期投入真年化看 irr_xirr_pct
        "naive_annualized_pct": round(((final_value / invested) ** (1 / years) - 1) * 100, 4),
        "irr_xirr_pct": round(xirr * 100, 4) if xirr is not None else None,
        "xirr_note": (None if xirr is not None else
                      "XIRR 无解：期末市值≈0（全亏）、窗口过短，或真实年化超出求解域上限"
                      f"（>{XIRR_MAX_RATE * 100:.0f}%），分期真年化不适用/不可信"),
        "short_window_note": short_note,
    }


def position_trend(snapshot: dict, recent: int = 4) -> dict:
    """股票仓位史规则解读（零网络，V0.4.0 B2）。输入 snapshot.stock_position_history。

    recent: 头尾各取 recent 期均值对比，捕捉加减仓（>5pp 判定）。
    """
    hist = [h for h in (snapshot.get("stock_position_history") or [])
            if isinstance(h.get("position_pct"), (int, float))]
    if len(hist) < 2:
        raise ValueError("仓位史不足 2 期（E2 Data_fundSharesPositions 缺失或基金过新），拒绝解读")
    vals = [h["position_pct"] for h in hist]
    latest = vals[-1]
    k = max(1, min(recent, len(vals) // 2))
    head = sum(vals[:k]) / k
    tail = sum(vals[-k:]) / k
    label = ("仓位过低（股基/指增常态 80~95%，需核对基金类型或在建仓期）" if latest < 60 else
             "贴上限运行（≥95%）" if latest >= 95 else "仓位正常区间")
    if tail - head > 5:
        label += "；近几期加仓"
    elif head - tail > 5:
        label += "；近几期减仓"
    return {"latest_pct": round(latest, 2), "min_pct": round(min(vals), 2),
            "max_pct": round(max(vals), 2), "mean_pct": round(sum(vals) / len(vals), 2),
            "periods": len(vals), "first_date": hist[0]["date"], "last_date": hist[-1]["date"],
            "label": label}


def peer_rank(snapshot: dict, lookback_days: int = 365) -> dict:
    """同类百分位解读（零网络，V0.4.0 B2）。输入 snapshot.peer_rank_history（日频）。

    值 = 超越同类百分比（高=排名靠前；官方主页定义与 95.06 实锚复核 2026-11-25）。
    输出最新值 + lookback 天前对照与变化。
    """
    hist = [h for h in (snapshot.get("peer_rank_history") or [])
            if isinstance(h.get("percentile"), (int, float))]
    if not hist:
        raise ValueError("同类排名史缺失（E2 Data_rateInSimilarPersent），拒绝解读")
    _assert_ascending_dates(hist, "peer_rank_history（latest 会取错点）")
    latest = hist[-1]
    cutoff = (datetime.fromisoformat(latest["date"]) - timedelta(days=lookback_days)).isoformat()
    past = [h for h in hist if h["date"] <= cutoff]
    year_ago = past[-1] if past else None
    vals = [h["percentile"] for h in hist]
    label = ("同类前列（≥80 分位）" if latest["percentile"] >= 80 else
             "同类落后（≤30 分位）" if latest["percentile"] <= 30 else "同类中游")
    return {"latest_date": latest["date"], "latest_percentile": latest["percentile"],
            "year_ago_percentile": year_ago["percentile"] if year_ago else None,
            "delta_pp": (round(latest["percentile"] - year_ago["percentile"], 2)
                         if year_ago else None),
            "min_percentile": min(vals), "max_percentile": max(vals),
            "points": len(vals), "label": label}


def relative_performance(snapshot: dict, benchmark: str = "沪深300",
                         min_overlap: int = 60) -> dict:
    """基金 vs E2 内置基准曲线的区间相对表现（零网络，V0.4.0 B3）。

    基准来自 snapshot.benchmark_curves（Data_grandTotal，窗口约近半年）：值为窗口
    起点以来累计收益%。基金与基准按共同拥有的起止日期计算区间收益，基准侧
    同样使用累计财富比值，避免新基金或净值缺失导致两边窗口不同：
    区间收益（基金/基准/相对差）+ 日频 β（基金日收益对基准日收益 OLS）+ 相关系数。
    benchmark 可传任意现存曲线名（"沪深300"/"同类平均"/基金简称）。
    """
    curves = {c["name"]: c for c in (snapshot.get("benchmark_curves") or [])}
    if benchmark not in curves:
        raise ValueError(f"基准曲线 {benchmark!r} 不存在；可用: {sorted(curves) or '（snapshot 无 benchmark_curves——E2 缺失或缓存为旧版本，force_refresh 重拉）'}")
    pts = curves[benchmark]["points"]
    if len(pts) < 2:
        raise ValueError(f"基准曲线 {benchmark!r} 点数不足")
    b_by_date = {p["date"]: p["return_pct"] for p in pts}
    dates = sorted(b_by_date)
    b_ret = {}
    for i in range(1, len(dates)):
        c0, c1 = b_by_date[dates[i - 1]], b_by_date[dates[i]]
        if c0 <= -100.0:
            raise ValueError(f"基准曲线 {benchmark!r} 在 {dates[i - 1]} 累计值 {c0}%（≤-100，"
                             f"数值异常），拒绝计算")
        b_ret[dates[i]] = (100.0 + c1) / (100.0 + c0) - 1.0
    _assert_ascending_dates(snapshot["nav_history"], "relative_performance")
    adj = adjusted_nav(snapshot["nav_history"])
    f_ret = {adj[i]["date"]: adj[i]["adj_nav"] / adj[i - 1]["adj_nav"] - 1.0
             for i in range(1, len(adj))}
    common = sorted(set(b_ret) & set(f_ret))
    if len(common) < min_overlap:
        raise ValueError(f"与基准曲线重叠仅 {len(common)} 个交易日（要求≥{min_overlap}；"
                         f"基准窗口仅约半年，新基金/区间错位可能不足）")
    xs = [b_ret[d] for d in common]
    ys = [f_ret[d] for d in common]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    beta = sxy / sxx if sxx > 1e-18 else None
    corr = sxy / math.sqrt(sxx * syy) if sxx > 1e-18 and syy > 1e-18 else None
    bench_flat, fund_flat = sxx <= 1e-18, syy <= 1e-18
    adj_by_date = {a["date"]: a["adj_nav"] for a in adj}
    common_levels = sorted(set(b_by_date) & set(adj_by_date))
    if len(common_levels) < 2:
        raise ValueError(f"基金与基准曲线 {benchmark!r} 的共同净值日期不足 2 个，无法计算区间收益")
    start_date, end_date = common_levels[0], common_levels[-1]
    if b_by_date[start_date] <= -100.0 or b_by_date[end_date] <= -100.0:
        raise ValueError(f"基准曲线 {benchmark!r} 在共同窗口端点累计值 ≤-100%，拒绝计算")
    fund_ret_pct = (adj_by_date[end_date] / adj_by_date[start_date] - 1.0) * 100.0
    bench_ret_pct = ((100.0 + b_by_date[end_date])
                     / (100.0 + b_by_date[start_date]) - 1.0) * 100.0
    beta_note = None
    if bench_flat and fund_flat:
        beta_note = "基准与基金曲线窗口内均近乎横盘（方差≈0），β/相关性不可辨识——相对收益仍有效"
    elif bench_flat:                                  # corr=None 的根因也是基准侧，勿误报基金横盘
        beta_note = "基准曲线窗口内近乎横盘（方差≈0），β 不可辨识——相对收益仍有效，β 请勿解读"
    elif fund_flat:
        beta_note = "基金净值窗口内近乎横盘（方差≈0），相关性不可辨识，β 数值意义同样有限"
    return {"benchmark": benchmark,
            "window": {"start": start_date, "end": end_date, "days": n},
            "fund_return_pct": round(fund_ret_pct, 2),
            "benchmark_return_pct": round(bench_ret_pct, 2),
            "relative_return_pct": round(fund_ret_pct - bench_ret_pct, 2),
            "beta": round(beta, 3) if beta is not None else None,
            "correlation": round(corr, 3) if corr is not None else None,
            "beta_note": beta_note}


def holding_map(snapshot: dict, topn: int = 10) -> dict:
    """持仓穿透桥（零网络）：把重仓股代码整理成可直接喂 a-stock-data 的形态。

    不直接调用 a-stock-data（避免硬依赖），返回调用说明 + 代码清单。
    """
    codes = [c for c in snapshot.get("top_stocks", []) if c][:topn]
    if not codes:
        raise ValueError(f"{snapshot.get('name')} 无股票持仓代码"
                         f"（债基/货基或 missing_datasets 含 stockCodes），无从穿透")
    return {
        "fund": snapshot.get("name"), "stock_codes": codes,
        "usage": ("以下 a-stock-data 端点可直接消费这些代码：\n"
                  "  tencent_quote(codes)               # 实时价/PE/PB/市值（腾讯通道不封IP）\n"
                  "  eastmoney_concept_blocks(code)     # 个股概念板块归属\n"
                  "  baostock_valuation_history(code,s,e) # 估值历史分位\n"
                  "  §6.7 sw_industry_as_of(...)        # 申万行业归属（历史口径）"),
        "note": "零网络桥接视图；一键穿透（含行情+板块+聚合）直接用本层 penetrate()",
    }
```

## 层 4 · 跨域穿透层（模块 L6，V0.2.0：重仓股 → 股票域 → 白话地图）

```python
# 通道来源与防封纪律均沿用 a-stock-data：腾讯批量行情（不封IP，1请求）+
# 东财 push2 slist 板块归属（1请求/股，当日磁盘缓存）。push2 走独立限流线
# EM_MIN_INTERVAL（与 ttfund_get 分管不同子域，峰值合计 ≈1.7 req/s，远低于风控阈值）。

EM_MIN_INTERVAL = 1.0
EM_SESSION = requests.Session()
EM_SESSION.trust_env = False                          # 直连：绕过系统代理（坑P12）
EM_SESSION.headers.update({"User-Agent": UA})
EM_REQUEST_COUNT = [0]                              # 股票域东财请求计数（审计用）
TENCENT_REQUEST_COUNT = [0]                         # 腾讯行情请求计数（审计用；腾讯不封不设限流）
SINA_REQUEST_COUNT = [0]                            # 新浪行情请求计数（V0.7.5 备胎通道，审计用）
SINA_MIN_INTERVAL = 0.5                             # legacy/teardown 模式的 F1 地板（与 provider 分账同值）
TENCENT_MIN_INTERVAL = 0.2                          # teardown 地板（与 provider 分账同值；V0.7.5 前腾讯线 teardown 裸奔）
CB_MIN_INTERVAL = 1.5                               # 中债 teardown 地板（V0.7.5 起独立常量，不再误用东财 TT_MIN_INTERVAL）
_SINACONN_COOLDOWN = 3600                           # sina 连接级熔断停机 1h（P26 同款：RST/超时无状态码，F2 看不见）
_sina_conn_fails = [0]


def em_get(url: str, params: Optional[dict] = None,
           headers: Optional[dict] = None, timeout: int = 15, **kwargs):
    """股票域东财统一入口（push2 系）：跨进程串行限流(F1) + 熔断(F2) + 会话复用。

    与 ttfund_get 分管不同子域（各自独立的节流线/熔断线），但同属东财风控面——
    em 线触发熔断时建议同时降低 tt 线频率（风控按 IP 不按子域精确区分）。
    Sprint 2：协调核心激活时改走 Gateway（与 tt 线共享 eastmoney 配额账），
    2xx 中性结算不清零，业务成功由调用方显式 _cbreak_success("em")。
    """
    if _REQUEST_GATEWAY is not None:
        resp = _REQUEST_GATEWAY.get(
            url, params=params, headers=headers, timeout=timeout, retries=1,
            retry_statuses=(),                         # 仅网络异常重试（legacy 口径）
            session=_SessionAdapter(EM_SESSION, _em_hit), **kwargs)
        _release_pending(resp, "em-passthrough")
        return resp
    for attempt in (1, 2):                            # 瞬态抖动重试一次（与 ttfund_get 对齐）
        _cbreak_check("em")
        _cross_proc_throttle("em", EM_MIN_INTERVAL)
        try:
            EM_REQUEST_COUNT[0] += 1
            return EM_SESSION.get(url, params=params, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2.0)


# ── push2 系连接级熔断（V0.7.1，坑 P26：IP 缓释桶限流）─────────────────
# 与 F2（403/429 显式风控）分工：这里管连接层失败（RST/超时/空体/非 JSON——
# 无状态码，F2 看不见）。2026-11-12 五发探针实测：IP 缓释桶惩罚期容量极小
# （3 连发即放空重触），诊断期 50+ 连发无人拦截即此缺口。连续 2 败即停机
# 3600s——停机期零网络直通 RuntimeError，既有降级链自动接管（国债→中债收盘、
# kline→腾讯备胎、concept_blocks→penetrate 单股降级）；期满自然恢复，
# 每小时真实请求 ≤2 次，不自己放空桶。
_PUSH2_JITTER = (0.5, 3.0)          # flock 地板之上叠加的随机抖动（打掉节拍器特征；测试置 (0,0)）
_push2_conn_fails = [0]             # 连续连接级失败计数（成功清零；调用级口径）
_PUSH2CONN_COOLDOWN = 3600          # 停机 1 小时


def _push2_conn_file() -> Path:
    return FUND_CACHE_DIR / ".cbreak_push2conn"


def _push2_conn_check():
    """连接级熔断检查：停机期内零网络直接 RuntimeError（降级链自动接管）。"""
    f = _push2_conn_file()
    if f.exists():
        try:
            until = float((f.read_text().split() or ["0"])[0])
        except (ValueError, OSError):                # 与写侧对称：IO 故障视为无标记
            until = 0.0
        if until > time.time():
            remain = int(until - time.time())
            raise RuntimeError(
                f"push2 系连接级熔断中（疑似 IP 缓释桶额度耗尽，已自动停机保护），"
                f"剩 {remain}s。停机期零网络——降级链自动接管（国债→中债收盘、"
                f"kline→腾讯备胎）；继续请求只会延长封锁，稍后再试。")
        f.unlink(missing_ok=True)                    # 期满自动复位
        _push2_conn_fails[0] = 0


def _push2_conn_fail(why: str):
    """记录一次连接级失败；连续 2 次写停机标记（本次调用照常抛错，下次起零网络）。"""
    _push2_conn_fails[0] += 1
    if _push2_conn_fails[0] >= 2:
        try:
            _push2_conn_file().parent.mkdir(parents=True, exist_ok=True)
            # 标记内容「到期时间戳 + 触发原因」——运维 cat 即知何时为何停机
            _push2_conn_file().write_text(f"{time.time() + _PUSH2CONN_COOLDOWN:.0f} {why}")
        except OSError:
            pass
        _push2_conn_fails[0] = 0


def _push2_get(url: str, params: Optional[dict] = None, context: str = "") -> dict:
    """push2/push2his 专用「单发即弃」通道（坑 P26：IP 缓释桶限流）。

    2026-11-12 五发探针定案：封锁=IP 级缓释桶——健康期容量大（4 连发数周无事），
    惩罚期（烧穿后）容量极小（3 连发即放空、第 4 发 RST）且恢复慢（小时级续量）；
    12:16 三连过（同栈同 UA）证伪「TLS 指纹永久黑名单」，12:22/12:23 两连挂定案
    重触机制。对策（不跟指纹识别军备竞赛）：单发即弃（1 调用=1 网络命中）+
    flock 跨进程地板 + 随机抖动 + 连接级熔断（2 连败停 1h）——桶满正常用、桶空
    自动退避，调用方无感（降级链接管）。403/429 → RiskControlError 计 F2 熔断
    不重试不降级（F6 契约）。

    Sprint 2：协调核心激活时网络命中改走 Gateway（retries=0 保持单发即弃；
    tt/em/push2 共享 eastmoney 配额账）；403/429 由网关分类计熔断，其余响应
    中性结算，业务成功仍归调用方 _cbreak_success("em")。"""
    _push2_conn_check()                              # 连接级熔断：停机期零网络直通
    if _REQUEST_GATEWAY is None:
        _cbreak_check("em")                          # F2：403/429 显式风控熔断（独立标记）
        _cross_proc_throttle("em", EM_MIN_INTERVAL)  # F1 跨进程地板（P12 直连）
    if _PUSH2_JITTER[1] > 0:
        # 抖动打掉节拍器特征。注：sleep 在 flock 释放后——跨进程相邻请求发起间隔
        # 下界=EM_MIN_INTERVAL-jitter_max（非严格 1.0s），单发即弃+连接级熔断兜底
        # 下的有意取舍（单进程内间隔仍恒 ≥1.5s）
        time.sleep(random.uniform(*_PUSH2_JITTER))
    sess = requests.Session()
    sess.trust_env = False                           # P12 直连（不读系统/环境代理）
    sess.headers.update({"User-Agent": UA})          # 与 EM_SESSION 同 UA——重探时
    try:                                            # UA 层拦截不被误判为指纹层
        if _REQUEST_GATEWAY is not None:
            r = _REQUEST_GATEWAY.get(
                url, params=params, timeout=10, retries=0,
                headers={"Referer": "https://quote.eastmoney.com/"},
                session=_SessionAdapter(sess, _em_hit))
            _release_pending(r, "push2-passthrough")
        else:
            EM_REQUEST_COUNT[0] += 1
            r = sess.get(url, params=params, timeout=10,
                         headers={"Referer": "https://quote.eastmoney.com/"})
    except requests.RequestException as e:
        _push2_conn_fail(f"{type(e).__name__} {context}")
        raise RuntimeError(f"push2 通道失败（{context}）: {type(e).__name__}: {str(e)[:60]}")
    finally:
        sess.close()
    if r.status_code in (403, 429):
        _cbreak_signal("em", f"HTTP {r.status_code} push2 {context}")
        raise RiskControlError(          # 风控信号不重试不降级（对齐 em_get 调用方语义）
            f"HTTP {r.status_code}（push2 {context}）：疑似{_line_label('em')}风控信号，已计入熔断")
    if r.status_code == 200 and r.text.strip():
        try:
            d = r.json()
        except ValueError:
            _push2_conn_fail(f"非JSON {context}")
            raise RuntimeError(f"push2 通道失败（{context}）: 非JSON: {r.text[:60]!r}")
        _push2_conn_fails[0] = 0                     # 成功清零（孤立抖动不累积停机）
        return d
    _push2_conn_fail(f"HTTP {r.status_code}/空体 {context}")
    raise RuntimeError(f"push2 通道失败（{context}）: HTTP {r.status_code} {r.text[:60]!r}")


def _tencent_prefix(code: str, kind: str = "stock") -> str:
    """6 位代码 → 腾讯行情前缀（sh/sz/bj），kind=stock/index/auto。

    默认按股票路由：000016 等同码证券中，sz000016 是深股、sh000016 是沪指，
    股票路径查询全局指数集合会静默返回错误证券。指数调用方须显式 kind="index"，
    或直接传 sh/sz 前缀；kind="auto" 仅保留给确需旧式自动识别的调用方。
    ⚠️ 92 北交所号段必须先于 9x 判断；204xxx 沪市质押式回购必须走 sh。
    """
    if kind not in ("stock", "index", "auto"):
        raise ValueError(f"kind 只支持 stock/index/auto，收到 {kind!r}")
    c = str(code).lower().strip()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if c.startswith("92") or c[:1] in ("4", "8"):
        return f"bj{c}"
    if c.startswith("204"):                            # 沪市回购 GC 族（204007=GC007）
        return f"sh{c}"
    if kind in ("index", "auto") and c in _SH_INDEX_Q:
        return f"sh{c}"
    if c[:1] in ("5", "6", "9"):
        return f"sh{c}"
    return f"sz{c}"


def _tencent_raw(url: str, headers: Optional[dict] = None, timeout: int = 10) -> bytes:
    """腾讯通道统一取原始字节（P12 直连 opener）。

    Sprint 2：协调核心激活时走许可生命周期——urllib opener 响应无 HTTP 状态码，
    进不了 Gateway 状态机，故手动 acquire/start/finish（中性结算，业务裁决归
    调用方）；acquire 同时提供跨进程 0.2s 地板（此前腾讯线无跨进程节流）。
    TENCENT_REQUEST_COUNT 计真实网络命中：在许可取得之后自增（acquire 被拒
    的尝试不进账本，与「账本=真实上游命中」口径一致）。
    V0.7.5 补两道防线：①403/429 计 tencent 风控信号并抛 RiskControlError——
    此前 HTTPError 是 OSError 子类，被 tencent_quote 当瞬态重试（F6「风控码
    绝不重试」在全库的破口）；②teardown 模式补 F1 地板 + F2 熔断镜像
    （此前裸奔，与 sina/东财线双标）。连接级失败保持中性结算不设独立熔断：
    腾讯定位"不封 IP"，主路挂时由 sina 备胎互备接管（设计分工非缺口）。"""
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if _COORDINATOR is None:
        _cbreak_check("tencent")
        _cross_proc_throttle("tencent", TENCENT_MIN_INTERVAL)
        TENCENT_REQUEST_COUNT[0] += 1
        try:
            return opener.open(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                _cbreak_signal("tencent", f"HTTP {e.code} tencent")
                raise RiskControlError(
                    f"HTTP {e.code}（腾讯）：疑似{_line_label('tencent')}风控信号，已计入熔断")
            raise
    permit = _COORDINATOR.acquire(url)
    _COORDINATOR.mark_network_started(permit)
    TENCENT_REQUEST_COUNT[0] += 1
    try:
        raw = opener.open(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        permit.failure(reason=f"tencent-http-{e.code}", response_received=True)
        if e.code in (403, 429):
            _cbreak_signal("tencent", f"HTTP {e.code} tencent")
            raise RiskControlError(
                f"HTTP {e.code}（腾讯）：疑似{_line_label('tencent')}风控信号，已计入熔断")
        raise
    except BaseException:
        permit.failure(reason="tencent-transport")
        raise
    permit.failure(reason="tencent-passthrough", response_received=True)
    return raw


def tencent_quote(codes: list[str]) -> dict[str, dict]:
    """腾讯批量实时行情（通道内嵌自 a-stock-data §1.2）：价/涨跌/换手/PE/PB/市值。

    腾讯通道实测不封 IP，N 只 = 1 请求，是穿透层的行情主力。
    返回 {纯6位代码: {...}}；非交易时段返回最后成交快照。
    """
    if not codes:
        return {}
    url = "https://qt.gtimg.cn/q=" + ",".join(_tencent_prefix(c) for c in codes)
    data = None
    for attempt in (1, 2):                            # 瞬态 SSL EOF/连接重试一次（实测网络抖动）
        try:
            data = _tencent_raw(url, headers={"User-Agent": "Mozilla/5.0"}).decode("gbk")
            break
        except urllib.error.HTTPError:
            raise                                     # 有状态码的失败不属瞬态重试（403/429 已在 _tencent_raw 转 RiskControlError）
        except OSError:                            # 含 URLError（其子类，HTTPError 已上移排除）
            if attempt == 2:
                raise
            time.sleep(2.0)                        # 退避加长：SSL EOF 瞬态窗口常 >1s，1s 内
                                                  # 二次重试大概率同窗双败（冷跑实测教训）
    result = {}
    _var_lines = 0
    for line in data.strip().split(";"):
        if "=" not in line or '"' not in line:
            continue
        if "_pv_none" in line:                       # 全无效批次的占位行：不计结构守卫，保 miss→{} 契约
            continue
        _var_lines += 1
        key = line.split("=")[0].split("_")[-1]
        v = line.split('"')[1].split("~")
        if len(v) < 53:
            continue

        def f(i):
            return float(v[i]) if v[i] else 0.0

        result[key[2:]] = {
            "name": v[1], "price": f(3), "last_close": f(4), "change_pct": f(32),
            "turnover_pct": f(38), "pe_ttm": f(39),
            "float_mcap_yi": f(44), "mcap_yi": f(45), "pb": f(46),
        }
    if _var_lines and not result:
        # 结构守卫（V0.7.5）：响应有行但 0 行解析成功 = 疑似上游字段结构变更
        # （当前要求 ≥53 段）——拒绝静默空。注意与无效码语义分界：腾讯对无效码
        # 不回行（2026-11-17 实测），全空响应属无效码 → 静默 {} 是 qdii_premium
        # 「miss 标 None」契约的一部分，不在此守卫范围。
        raise RuntimeError(
            f"腾讯行情响应含 {_var_lines} 行但 0 行解析成功——疑似字段结构变更"
            f"（解析要求 ≥53 段），拒绝静默空；请核对 qt.gtimg.cn 返回格式")
    if result:
        _cbreak_success("tencent")
    return result


def _sina_conn_file() -> Path:
    return FUND_CACHE_DIR / ".cbreak_sinaconn"


def _sina_conn_check():
    """新浪连接级熔断检查：停机期内零网络直接 RuntimeError（P26 同款防线）。

    新浪封禁的另一形态是连接层 RST/超时（无状态码）——F2 看不见、空体守卫
    也到不了；连续 2 次即停机 1h，防止中性结算下配额照烧、IP 持续挨打。"""
    f = _sina_conn_file()
    if f.exists():
        try:
            until = float((f.read_text().split() or ["0"])[0])
        except (ValueError, OSError):                # 与写侧对称：IO 故障视为无标记
            until = 0.0
        if until > time.time():
            remain = int(until - time.time())
            raise RuntimeError(
                f"新浪通道连接级熔断中（疑似 IP 级封锁/RST，已自动停机保护），剩 {remain}s。"
                "停机期零网络——腾讯通道（tencent_quote）是互备对象；继续请求只会延长封锁。")
        f.unlink(missing_ok=True)                    # 期满自动复位
        _sina_conn_fails[0] = 0


def _sina_conn_fail(why: str):
    """记录一次连接级失败；连续 2 次写停机标记（本次调用照常抛错，下次起零网络）。"""
    _sina_conn_fails[0] += 1
    if _sina_conn_fails[0] >= 2:
        try:
            _sina_conn_file().parent.mkdir(parents=True, exist_ok=True)
            # 标记内容「到期时间戳 + 触发原因」——运维 cat 即知何时为何停机
            _sina_conn_file().write_text(f"{time.time() + _SINACONN_COOLDOWN:.0f} {why}")
        except OSError:
            pass
        _sina_conn_fails[0] = 0


def _sina_raw(url: str, timeout: int = 10) -> bytes:
    """新浪通道统一取原始字节（P12 直连 opener；hq.sinajs.cn 必须带 Referer，
    无 Referer 实测空响应/拒绝——故 Referer 为通道内置头，不留给调用方忘带）。

    Sprint 2 同款许可生命周期（provider=sina）：acquire 提供 0.5s 跨进程地板 +
    120/300s 配额；HTTP 403/429 → 计风控信号并抛 RiskControlError（F2/F6 契约，
    不降级不重试）；空体 → RuntimeError（疑似限流，拒绝静默空）。
    V0.7.5 补连接级熔断：RST/超时/拒连连续 2 次 → 停机 1h 零网络（P26 同款，
    新浪封禁形态常无状态码，F2 与空体守卫均不可见）。"""
    _sina_conn_check()
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if _COORDINATOR is None:
        # legacy/teardown：F1 地板 + F2 磁盘熔断（对照 _push2_get legacy 分支）——
        # teardown 是文档明示的生产模式，全库唯一有封禁前科的通道不允许零防线
        _cbreak_check("sina")
        _cross_proc_throttle("sina", SINA_MIN_INTERVAL)
        SINA_REQUEST_COUNT[0] += 1
        try:
            raw = opener.open(req, timeout=timeout).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                _cbreak_signal("sina", f"HTTP {e.code} sina")
                raise RiskControlError(
                    f"HTTP {e.code}（新浪）：疑似{_line_label('sina')}风控信号，已计入熔断")
            raise
        except (urllib.error.URLError, OSError):     # 连接层（拒连/RST/超时），无状态码
            _sina_conn_fail(f"sina-conn-transport")
            raise
        if not raw:
            _cbreak_signal("sina", "sina-empty-body")
            raise RuntimeError("新浪行情返回空体（疑似限流/封禁），拒绝静默空")
        _sina_conn_fails[0] = 0                      # 成功复位连接级计数
        return raw
    permit = _COORDINATOR.acquire(url)
    _COORDINATOR.mark_network_started(permit)
    SINA_REQUEST_COUNT[0] += 1
    try:
        raw = opener.open(req, timeout=timeout).read()
    except urllib.error.HTTPError as e:
        permit.failure(reason=f"sina-http-{e.code}", response_received=True)
        if e.code in (403, 429):
            _cbreak_signal("sina", f"HTTP {e.code} sina")   # 镜像+协调器信号（触闸自带抛错）
            raise RiskControlError(
                f"HTTP {e.code}（新浪）：疑似{_line_label('sina')}风控信号，已计入熔断")
        raise
    except (urllib.error.URLError, OSError) as e:    # 连接层：RST/超时/拒连——F2 看不见（P26 同款）
        permit.failure(reason=f"sina-transport-{type(e).__name__}")
        _sina_conn_fail(f"sina-conn-{type(e).__name__}")
        raise
    except BaseException:
        permit.failure(reason="sina-transport")
        raise
    permit.failure(reason="sina-passthrough", response_received=True)
    if not raw:
        _cbreak_signal("sina", "sina-empty-body")   # 连续空体同样会触闸（镜像+协调器）
        raise RuntimeError("新浪行情返回空体（疑似限流/封禁），拒绝静默空")
    _sina_conn_fails[0] = 0                          # 成功复位连接级计数
    return raw


def _parse_sina_body(text: str) -> dict[str, dict]:
    """解析新浪行情响应体（纯函数，零网络）。

    新浪债券/股票行格式（GBK）：var hq_str_shXXXX="名称,今开,昨收,最新,最高,最低,
    买一价,卖一价,...,日期,时间,00(,空段)"; ——债券盘初未成交时「最新」为 0，
    live 参考取买一价（bid1）。返回 {6位代码: {name, open, prev_close, last,
    bid1, ask1, date, time}}；残行（字段不足 32 或空名）静默跳过。
    ⚠️ 沪深行尾结构不一致（实测沪 34 段带空尾段、深 33 段）——日期/时间按值
    定位（尾部唯一 YYYY-MM-DD 段及其后一段），不用固定尾索引。"""
    result = {}
    for line in text.strip().splitlines():
        if "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        v = line.split('"')[1].split(",")
        if len(v) < 32 or not v[0]:
            continue

        def f(i):
            try:
                return float(v[i]) if v[i] else None
            except ValueError:
                return None

        date = time = None
        for idx in range(len(v) - 1, max(len(v) - 8, -1), -1):
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v[idx] or ""):
                date = v[idx]
                time = v[idx + 1] if idx + 1 < len(v) else None
                break
        result[key[2:]] = {
            "name": v[0], "open": f(1), "prev_close": f(2), "last": f(3),
            "bid1": f(6), "ask1": f(7), "date": date, "time": time,
        }
    return result


def sina_quote(codes: list[str]) -> dict[str, dict]:
    """新浪批量实时行情（V0.7.5 备胎通道，定位=腾讯互备低频使用）。

    ⚠️ 防封纪律（provider=sina 独立分账）：120 请求/300s、间隔 0.5s、单并发
    ——hq.sinajs.cn 对高频爬虫有封禁前科，此配额只为「腾讯挂时兜底」设计，
    不要当主通道高频轮询。批量 ≤50 只/请求（URL 长度安全线），输入去重。
    债券盘初「最新」可能为 0（未成交），取 bid1 作 live 参考价。
    ⚠️ 代码必须带 sh/sz 前缀（如 sh118078）——裸 6 位码无法可靠路由市场
    （沪转债 11x vs 深公司债 112 同头，V0.7.3 裸码路由同类坑），显式报错。
    超过 50 只自动按 50 分块串行请求（分块间隔由 provider 配额节流，不静默丢码）。"""
    if not codes:
        return {}
    codes = [str(c).strip() for c in codes]           # 非 str 输入（int 等）先归一（V0.8.1：
                                                      # 原 len(c) 对非 str 先抛 TypeError，
                                                      # else 分支的 str(c) 兜底永远走不到）
    codes = [c[:2].lower() + c[2:] if (len(c) == 8 and c[:2].lower() in ("sh", "sz"))
             else c
             for c in codes]                          # 再归一前缀大小写
    codes = list(dict.fromkeys(codes))                # 归一后去重（SH/sh 不双发）
    bad = [c for c in codes if not (len(c) == 8 and c[:2] in ("sh", "sz"))]
    if bad:
        raise ValueError(
            f"sina_quote 代码须带 sh/sz 前缀（如 sh118078，大小写均可），收到非法: {bad[:3]}——"
            "裸 6 位码路由易错市场（V0.7.3 坑），不猜")
    result: dict[str, dict] = {}
    for i in range(0, len(codes), 50):
        chunk = codes[i:i + 50]
        url = "https://hq.sinajs.cn/list=" + ",".join(chunk)
        raw_text = _sina_raw(url).decode("gbk", errors="replace")
        parsed = _parse_sina_body(raw_text)
        if not parsed:
            # 软封锁守卫（V0.7.5）：新浪真实封禁形态 = HTTP 200 + 非空体 + 全空
            # var 行（hq_str_xx="";）——绕过空体守卫与 403 分流，漏判即烧 IP。
            # V0.8.1 收敛误伤：体含空 var 行才计风控信号——chunk 内代码全无效/
            # 退市时新浪回「无 var 行」的干净空体，属正常未命中不计连击；两种
            # 形态仍不可完全区分（全无效码也可能带回空 var 行），保留少量误伤
            # 面不漏判封禁（信号 5min 衰减、连续 3 次才熔断）。
            if re.search(r'hq_str_\w+="";', raw_text):
                _cbreak_signal("sina", "sina-all-miss-chunk")
                raise RuntimeError(
                    f"新浪行情 chunk 全未命中（{len(chunk)} 码 0 行，含空 var 行）——"
                    "软封锁嫌疑，已计入 sina 熔断（连续 3 次冷却 10 分钟）；"
                    "腾讯通道是主源互备对象")
            raise RuntimeError(
                f"新浪行情 chunk 全未命中（{len(chunk)} 码 0 行，无 var 行）——"
                "代码无效或已退市，不计风控连击；请核对 sh/sz 前缀与代码有效性")
        result.update(parsed)
        # 结构性成功即清零 sina 连击（parsed 非空由上方守卫保证；sina 只回请求码的行，
        # 不存在"非空但全非本批码"形态——原 any() 条件恒真，按审查意见改为无条件）
        _cbreak_success("sina")
    return result


def em_market_code(code: str) -> int:
    """东财 secid 市场号：沪=1，深/北=0（规则照抄 a-stock-data，勿用 startswith('6')）。

    ⚠️ 92 北交所现行号段先于 9x 判断（V3.7.0 实测 920982 → m=0，与深市共用 0）。
    """
    c = str(code).lower()
    if c.startswith("92") or c[:1] in ("4", "8"):     # 北交所：920 现行 + 4x/8x 老段
        return 0
    if c.startswith("sh") or c[:1] in ("5", "6", "9"):
        return 1
    return 0


def concept_blocks(stock_code: str, use_cache: bool = True) -> list[str]:
    """个股板块标签（通道内嵌自 a-stock-data §3.3 东财 slist，一次请求拿全）。

    板块混合 行业/概念/地域（'白酒Ⅲ'/'酿酒概念'/'贵州板块'），名称自解释；
    当日磁盘缓存 penetration/{code}.json（存原始标签，清洗规则演进不使缓存失效）。
    """
    if not re.fullmatch(r"\d{6}", str(stock_code)):
        raise ValueError(f"股票代码格式错误: {stock_code!r}（需 6 位数字，防异常上游数据拼入缓存路径）")
    rel = f"penetration/{stock_code}.json"
    if use_cache:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    params = {"fltt": "2", "invt": "2",
              "secid": f"{em_market_code(stock_code)}.{stock_code}",
              "spt": "3", "pi": "0", "pz": "200", "po": "1",
              "fields": "f12,f14,f3,f128"}
    # V0.7.1 迁入 _push2_get（同域吃 IP 缓释桶，自动获得连接级熔断+抖动；
    # 403/429→RiskControlError 语义不变）
    d = _push2_get("https://push2.eastmoney.com/api/qt/slist/get", params,
                   f"板块 {stock_code}")
    diff = (d.get("data") or {}).get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    tags = [it.get("f14", "") for it in items if it.get("f14")]
    if not tags:
        raise RuntimeError(f"{stock_code} 板块归属为空（secid 路由或风控），拒绝静默空")
    _cbreak_success("em")
    _cache_write(rel, tags)
    return tags


# 噪声停用词：指数成分/交易机制/地域板块——不是"这只基金在押注什么"的信号。
# ⚠️ 取舍边界：国企改革/央企/一带一路 对多数基金是壳噪声，但对相应主题基金
# 恰是真实主题——本表按"通用穿透"取舍（宁滤勿噪），主题基金专用分析请自行调整。
_TAG_STOP = re.compile(
    r"概念|板块|融资融券|转融券|沪股通|深股通|MSCI|标普|标准普尔|富时|罗素|道琼斯"
    r"|上证|深证|沪深|中证|国证|创业板|科创板|北交所|HS\d|SH\d|SZ\d|央视|QFII|RQFII"
    r"|同花顺|东财|基金重仓|机构重仓|预盈预增|预亏预减|股权激励|回购|增持|壳资源"
    r"|次新股|高送转|含可转债|B转H|AB股|GDR|老字号|国企改革|央企|国资|PPP|一带一路")
_SUFFIX_ROMAN = re.compile(r"[ⅠⅡⅢⅣⅤ]$|I+$")


def _clean_tag(tag: str):
    """板块名 → 主题词；噪声返回 None。'白酒Ⅲ'→'白酒'，'计算机设备II'→'计算机设备'。

    后缀剥离后长度 <2 的产物（如 'AI' 被 I+$ 剥成 'A'）也返回 None——单字符不构成主题词。
    """
    t = tag.strip()
    if not t or len(t) < 2 or _TAG_STOP.search(t):
        return None
    cleaned = _SUFFIX_ROMAN.sub("", t).strip()
    return cleaned if len(cleaned) >= 2 else None


def penetrate(query: str, topn: int = 10) -> dict:
    """重仓穿透主入口（R9 场景 04）：基金 → 重仓股权重 → 股票域行情+板块 → 白话地图。

    请求预算（P-B 修正）：1(持仓 jjcc，当日缓存 F4) + 1(腾讯批量) + N(板块，当日缓存)
    → 冷启动 ≈13 请求；当日重跑 = 1 请求（仅腾讯，持仓与板块全缓存命中）。
    基金名从 fund_list 主键表直查（0 请求）——不再为拿名字拉 436KB 快照（P-A）。
    单股失败不中断整体：跳过该股的主题聚合，记入 failed_stocks（含 reason 与权重）。
    """
    code = resolve_fund_code(query)
    name = next((f["name"] for f in fund_list() if f["code"] == code), code)
    hold = fund_holdings(code)
    if not hold:
        raise RuntimeError(f"{query} 无股票持仓记录（债基/货基？）——"
                           f"股票穿透需要股票重仓；债券穿透请用 fund_snapshot().top_bonds")
    latest = max((h["period"] or "" for h in hold))    # 防个别行 period=None 崩 max；按日期取最新
    pool = [h for h in hold if h["period"] == latest][:topn]

    failed_stocks: list[dict] = []                     # per-stock 降级名单（含权重）
    picks = []
    for h in pool:
        if h.get("nav_pct") is None:                   # 权重解析失败（None 防 TypeError）
            failed_stocks.append({"code": h["stock_code"], "name": h["stock_name"],
                                  "weight": None, "reason": "占净值比缺失"})
            continue
        picks.append(h)
    if not picks:
        raise RuntimeError(f"{query} 最新报告期 {latest} 全部重仓股权重缺失，无法穿透")
    weight_sum = sum(h["nav_pct"] for h in picks)

    quotes = tencent_quote([h["stock_code"] for h in picks])   # 股票域行情（1 请求）

    theme = defaultdict(float)                         # 主题词 → 累计净值权重
    for h in picks:
        try:
            tags = concept_blocks(h["stock_code"])
        except (RiskControlError, CircuitOpenError):
            raise                                  # 风险型信号不得伪装成单股数据缺失
        except Exception as e:                     # 单股数据/连接故障：跳过该股主题，不中断
            failed_stocks.append({"code": h["stock_code"], "name": h["stock_name"],
                                  "weight": h["nav_pct"],
                                  "reason": f"板块获取失败: {str(e)[:60]}"})
            continue
        # 🔴 每股先去重再累计：同一股票的 白酒Ⅱ/白酒Ⅲ 清洗后都是"白酒"，
        #    不去重会把权重翻倍（实测白酒基金曾算出 259%）
        uniq = {k for k in (_clean_tag(t) for t in tags) if k}
        for key in uniq:
            theme[key] += h["nav_pct"]

    # 平局裁决必须确定：同权重时短词优先（"白酒"2字 胜 "食品饮料"4字，行业分级里
    # 短词通常是更聚焦的主题），再按字典序——否则 set 遍历序受哈希随机化影响，
    # 同一基金每次穿透 Top1 主题会在 白酒/食品饮料 间漂移（实测踩过）
    top_themes = sorted(theme.items(), key=lambda x: (-x[1], len(x[0]), x[0]))[:6]
    pe_list = sorted(q["pe_ttm"] for q in quotes.values() if q["pe_ttm"] > 0)
    pe_med = None
    if pe_list:                                        # 标准中位数：偶数取中间两值均值
        n = len(pe_list)
        pe_med = pe_list[n // 2] if n % 2 else (pe_list[n // 2 - 1] + pe_list[n // 2]) / 2

    # 未参与主题聚合的权重 = 板块失败股权重 + 权重缺失股按 0 计（无从取数）
    unclassified = round(sum(f["weight"] for f in failed_stocks if f["weight"]), 2)
    detail = [{**h, "quote": quotes.get(h["stock_code"], {})} for h in picks]
    return {
        "fund": name,
        "period": latest, "topn_weight_pct": round(weight_sum, 2),
        "detail": detail,
        "themes": [(k, round(v, 2)) for k, v in top_themes],
        "pe_median_profitable": pe_med,                # 仅盈利股（PE>0）
        "failed_stocks": failed_stocks,
        "unclassified_weight_pct": unclassified,
        "degraded": bool(failed_stocks) and not top_themes,
    }


def plain_text_map(p: dict) -> str:
    """穿透结果 → 白话地图文本（"这只基金主要是白酒约86%"）。"""
    top = p["themes"][0] if p["themes"] and p["themes"][0][1] >= 5 else None
    degraded = p.get("degraded", False)
    summary = (f"一句话：板块通道不可用（{len(p['failed_stocks'])}/{len(p['detail'])} "
               "重仓股取数失败），未能聚合主题——结论不可用。" if degraded else
               f"一句话：这只基金主要是{top[0]}（相关持仓权重约 {top[1]:.0f}%）。"
               if top else "一句话：持仓分散，无明显单一主题。")
    lines = [
        f"《{p['fund']} 里装了什么》（{p['period']} 季报，前十大占净值 {p['topn_weight_pct']}%）",
        "",
        summary,
        "",
        f"{'股票':<12}{'权重':>7}{'现价':>9}{'今日':>8}{'PE':>9}{'PB':>7}",
    ]
    for h in p["detail"]:
        q = h.get("quote") or {}
        pe = q.get("pe_ttm", 0)
        pe_txt = "亏损" if pe < 0 else f"{pe:.1f}"
        lines.append(f"{h['stock_name']:<12}{h['nav_pct']:>6.1f}%{q.get('price', 0):>9.2f}"
                     f"{q.get('change_pct', 0):>7.1f}%{pe_txt:>9}{q.get('pb', 0):>7.2f}")
    if len(p["themes"]) > 1:
        lines.append("\n主题词排行: " + "、".join(f"{k} {v:.0f}%" for k, v in p["themes"][:5]))
    if p.get("pe_median_profitable"):
        lines.append(f"盈利股 PE 中位数 {p['pe_median_profitable']:.1f} 倍"
                     f"（已剔除亏损股；{'高于' if p['pe_median_profitable'] > 30 else '不高于'}30 倍参考线）")
    if p.get("failed_stocks"):
        names = "、".join(f["name"] for f in p["failed_stocks"])
        lines.append(f"⚠ {len(p['failed_stocks'])} 只未参与主题聚合"
                     f"（合计权重 {p.get('unclassified_weight_pct', 0):.1f}%）: {names}")
    return "\n".join(lines)
```

## 层 5 · 宏观利率层（模块 L7，V0.3.0：中债曲线族 + 东财利率报表 + 腾讯回购代理）

```python
# 服务基金域分析：债基利率敏感度解释 / 理财波动地图 / 期限匹配 / 信用利差监控。
# 三条通道：中债官网（曲线族主源，1请求=1条曲线1年日频）· 东财 datacenter
# （SHIBOR/国债日频/基准利率，报表名见坑P10）· 腾讯（GC007 回购代理）。
# 中债/东财均走 ttfund_get 统一限流；push2 实时走既有 em_get 股票域线。

CB_BASE = "https://yield.chinabond.com.cn/cbweb-mn"
CB_REFERER = "https://yield.chinabond.com.cn/cbweb-mn/yield_main?locale=zh_CN"


def _cb_request(path: str, params: Optional[dict] = None, method: str = "GET",
                timeout: int = 30) -> bytes:
    """中债专用通道：curl（坑P11：中债 TLS 指纹墙——requests/urllib 稳定
    SSL EOF，curl 稳定 200，A/B 实测 2026-11-06）。

    V0.7.5 接协调器 curl transport（Sprint 3+ backlog 兑现）：chinabond 分账
    （120/300s·1.5s·在途≤2·3 连击熔断 10min）由 Gateway 统一执行；HTTP
    403/429 在网关层转 RiskControlError（不重试不降级，F6）；curl 进程失败/
    超时 → OSError 传输失败入账（外层瞬态重试一次，风控码除外）；空体 →
    风控信号 + RuntimeError（疑似限流，拒绝静默空）。2xx 附带之待裁决
    permit 由本层中性结算——业务清零走 cb_curve_tree/cb_yield_curve 解析
    成功后的 _cbreak_success("cb")（Sprint 1 口径，与 sina/tencent 同款）。
    teardown 模式：legacy curl + F1 地板（CB_MIN_INTERVAL 独立常量，V0.7.5
    前误用东财 TT_MIN_INTERVAL）+ F2 熔断镜像 + 空体信号。ycDefId 入 URL
    前过白名单校验（上游不变）；list 参数无 shell 注入面。"""
    from urllib.parse import urlencode
    import subprocess
    url = f"{CB_BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    if _REQUEST_GATEWAY is not None:
        for attempt in (1, 2):                          # curl 瞬态重试一次（每次独立配额；风控码绝不重试）
            try:
                resp = _REQUEST_GATEWAY.curl_request(
                    method, url, timeout=timeout,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": CB_REFERER,
                             "Accept": "*/*"})
            except (RiskControlError, CircuitOpenError):
                raise                                  # 风险型/熔断期：不重试不降级
            except (OSError, subprocess.TimeoutExpired) as e:
                # V0.8.1 补 TimeoutExpired 分流：网关侧 runner 已留 +10s 余量，仅
                # curl 挂死超余量时到达——与 legacy 分支同口径包装重试/报错
                if attempt == 2:
                    raise RuntimeError(f"中债 curl 传输失败: {e}") from e
                time.sleep(1.5)
                continue
            if resp.status_code >= 400:                 # 非风控终态（403/429 已在网关层转 RiskControlError 上抛）
                _release_pending(resp, f"cb-http-{resp.status_code}")   # V0.8.1：终态先结算挂起许可
                raise RuntimeError(f"中债 HTTP {resp.status_code}: {url[:90]}")
            if not resp.content:
                _release_pending(resp, "cb-empty-body")  # 先中性结算再信号（与 _sina_raw 同序，防挂起许可占坑至租约到期）
                _cbreak_signal("cb", "cb-empty-body")
                raise RuntimeError("中债响应空体（疑似限流），拒绝静默空")
            _release_pending(resp, "cb-passthrough")    # 中性结算：业务裁决归 cb_* 调用方（LeaseError 静默=已中性清理）
            return resp.content
        raise RuntimeError("unreachable")
    for attempt in (1, 2):                              # curl 也有瞬态，重试一次
        _cbreak_check("cb")
        _cross_proc_throttle("cb", CB_MIN_INTERVAL)    # F1：独立常量（V0.7.5 前误用东财 TT_MIN_INTERVAL）
        TT_REQUEST_COUNT[0] += 1
        TT_REQUEST_COUNT[1]["yield.chinabond.com.cn"] = \
            TT_REQUEST_COUNT[1].get("yield.chinabond.com.cn", 0) + 1
        cmd = ["curl", "-sS", "--max-time", str(timeout), "-X", method,
               "-H", "User-Agent: Mozilla/5.0", "-H", f"Referer: {CB_REFERER}",
               "-H", "Accept: */*", "-w", "\\n%{http_code}", url]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        except FileNotFoundError:
            raise RuntimeError("中债通道需要系统 curl（TLS 指纹墙，requests 不可用，"
                               "见坑P11）——当前环境未安装 curl。")
        except subprocess.TimeoutExpired:
            if attempt == 2:
                raise RuntimeError(f"中债 curl 超时（>{timeout}s）: {url[:90]}")
            time.sleep(1.5)
            continue
        if proc.returncode == 0 and proc.stdout:
            body, separator, tail = proc.stdout.rpartition(b"\n")
            if not separator or not tail.strip().isdigit():
                raise RuntimeError("中债 curl 响应缺 HTTP 状态行，拒绝按正文解析")
            status = int(tail.strip())
            if status in (403, 429):
                _cbreak_signal("cb", f"HTTP {status}")
                raise RiskControlError(f"中债 HTTP {status}（teardown），已计入熔断")
            if not 200 <= status < 300:
                raise RuntimeError(f"中债 HTTP {status}: {body[:60]!r}")
            if body:
                return body
            _cbreak_signal("cb", "cb-empty-body")
            raise RuntimeError("中债 curl 返回空体（疑似限流），拒绝静默空")
        if proc.returncode == 0 and not proc.stdout:
            _cbreak_signal("cb", "cb-empty-body")      # rc=0 空体：疑似限流，拒绝静默空
            raise RuntimeError("中债 curl 返回空体（疑似限流），拒绝静默空")
        if attempt == 2:
            raise RuntimeError(
                f"中债 curl 通道失败（rc={proc.returncode}，{len(proc.stdout)}B）: "
                f"{proc.stderr.decode('utf-8', 'ignore')[:120] or url[:100]}")
        time.sleep(1.5)
    raise RuntimeError("unreachable")

# 关键曲线种子 ID（2026-11-06 实测从 queryTree 提取；cb_curve_tree() 可拉全族校准）
_CB_CURVES = {
    "treasury":      ("中债国债收益率曲线", "2c9081e50a2f9606010a3068cae70001"),
    "enterprise_aaa": ("中债企业债收益率曲线(AAA)", "2c9081e50a2f9606010a309f4af50111"),
    "enterprise_aa": ("中债企业债收益率曲线(AA)", "2c90818812b319130112c279222836c3"),
    "ncd_aaa":       ("中债商业银行同业存单收益率曲线(AAA)", "8308218D1D030E0DE0540010E03EE6DA"),
    "ncd_aa_plus":   ("中债商业银行同业存单收益率曲线(AA＋)", "8308218DB7D90E0DE0540010E03EE6DA"),
    "ncd_aa":        ("中债商业银行同业存单收益率曲线(AA)", "8308218E52AF0E0DE0540010E03EE6DA"),
    "ncd_a_plus":    ("中债商业银行同业存单收益率曲线(A＋)", "8308218F885B0E0DE0540010E03EE6DA"),
    "ncd_a":         ("中债商业银行同业存单收益率曲线(A)", "8308219023310E0DE0540010E03EE6DA"),
}


def _term_years(term: str) -> Optional[float]:
    """中债标准期限码 → 年：'0d'→0.0，'3m'→0.25，'2y'→2.0；无法解析返回 None。"""
    t = str(term).strip().lower()
    try:
        if t.endswith("d"):
            return round(float(t[:-1]) / 365, 6)
        if t.endswith("m"):
            return round(float(t[:-1]) / 12, 6)
        if t.endswith("y"):
            return float(t[:-1])
    except ValueError:
        pass
    return None


def _parse_cb_xlsx(content: bytes) -> list[dict]:
    """中债 downYearBzqx 的 OOXML → [{date, term, term_years, yield_pct}]。

    坑P9：单元格是 inlineStr（无 sharedStrings），zipfile+regex 直接解；
    行结构 (日期, 期限码, 收益率) 三元组，表头行自然被日期正则排除。
    """
    z = zipfile.ZipFile(io.BytesIO(content))
    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8", "ignore")
    pts = []
    for row in re.findall(r"<row[^>]*>(.*?)</row>", sheet, re.S):
        cells = [_html.unescape(c).strip()
                 for c in re.findall(r"<t[^>]*>([^<]*)</t>", row)]
        if len(cells) >= 3 and re.fullmatch(r"\d{4}/\d{2}/\d{2}", cells[0]):
            try:
                pts.append({"date": cells[0].replace("/", "-"), "term": cells[1],
                            "term_years": _term_years(cells[1]),
                            "yield_pct": float(cells[2])})
            except ValueError:
                continue                                    # 非数据行（空值等），跳过
    return pts


def _cb_curve_id(curve: str) -> tuple[str, str]:
    """曲线键 → (名称, ycDefId)。种子表优先，miss 则拉曲线树按名称模糊匹配。"""
    if curve in _CB_CURVES:
        return _CB_CURVES[curve]
    tree = cb_curve_tree()                                 # name → id（含全族 40+ 条）
    hits = {n: i for n, i in tree.items() if curve in n}
    if len(hits) != 1:
        raise ValueError(f"未知曲线 {curve!r}：请用 _CB_CURVES 键（{sorted(_CB_CURVES)}）"
                         f"或曲线树中的完整名称；模糊命中 {len(hits)} 条不猜。")
    return next(iter(hits.items()))


def _cb_validated_id(curve: str) -> tuple[str, str]:
    """_cb_curve_id + 32 位十六进制校验（防树缓存污染拼入 URL/缓存路径，TypeError 一并拦）。"""
    name, cid = _cb_curve_id(curve)
    if not isinstance(cid, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", cid):
        raise ValueError(f"曲线 ID 非法: {cid!r}（需 32 位十六进制，来源 {name!r}）")
    return name, cid


def cb_curve_tree(force_refresh: bool = False) -> dict[str, str]:
    """中债曲线树全族 name→ycDefId（40+ 条：国债/国开/企业债AAA~AA-/城投/存单/ABS…）。

    端点 POST /yc/queryTree；磁盘缓存 30 天（曲线族极少变化）。
    """
    if not force_refresh:
        cached = _cache_read("cb_curve_tree.json", max_age_hours=30 * 24)
        if cached is not None:
            return cached
    raw = _cb_request("/yc/queryTree", {"locale": "zh_CN"}, method="POST")
    root = json.loads(raw)
    if not isinstance(root, list):
        root = root.get("data") or []
    out: dict[str, str] = {}

    def walk(nodes):
        for n in nodes:
            if isinstance(n, dict):
                if n.get("children"):
                    walk(n["children"])
                elif n.get("id"):
                    out[str(n.get("name", ""))] = n["id"]

    walk(root)
    if len(out) < 10:                                      # 正常 40+，骤降=结构变更
        raise RuntimeError(f"中债曲线树仅 {len(out)} 条叶子，疑似结构变更，拒绝缓存脏数据")
    _cbreak_success("cb")                       # V0.7.5：业务校验通过 → 清零 chinabond 连击（Sprint 1 口径）
    _cache_write("cb_curve_tree.json", out)
    return out


def cb_yield_curve(curve: str = "treasury", year: Optional[int] = None,
                   force_refresh: bool = False) -> dict:
    """中债收益率曲线·年文件（坑P8：只走 downYearBzqx GET，不碰 searchYc）。

    1 请求 = 1 条曲线 1 年的日频全期限（0d~50y）；当日磁盘缓存命中 0 请求。
    返回 {curve, year, fetched_at, source, points: [{date, term, term_years, yield_pct}]}。
    """
    name, cid = _cb_validated_id(curve)             # 32-hex 校验先于缓存路径构造
    year = int(year or date.today().year)
    rel = f"cb_curves/{cid}_{year}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached
    params = {"locale": "zh_CN", "year": str(year), "ycDefId": cid,
              "qxll": "0,", "zblx": "txy", "dxbj": "0",
              "workTime": date.today().isoformat(),
              "wrjibile": "true", "wrjxCBFlag": ""}
    content = _cb_request("/yc/downYearBzqx", params)
    pts = _parse_cb_xlsx(content)
    if not pts:
        raise RuntimeError(f"中债 {name} {year} 年文件解析出 0 个数据点"
                           f"（{len(content)} 字节）——参数或上游结构变更，拒绝静默空")
    _cbreak_success("cb")                       # V0.7.5：业务校验通过 → 清零 chinabond 连击（Sprint 1 口径）
    snap = {"curve": name, "year": year, "points": pts,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "source": "chinabond/downYearBzqx"}
    _cache_write(rel, snap)
    return snap


def cb_curve_series(curve: str, term: str = "10y", year: Optional[int] = None) -> list[dict]:
    """单期限时序：[{date, yield_pct}]（日期升序）。term 用中债期限码：'1y'/'3y'/'10y'…"""
    snap = cb_yield_curve(curve, year)
    rows = [p for p in snap["points"] if p["term"] == term]
    if not rows:
        raise ValueError(f"{snap['curve']} {snap['year']} 无期限 {term!r}，"
                         f"可用: {sorted({p['term'] for p in snap['points']})[:12]}")
    return [{"date": p["date"], "yield_pct": p["yield_pct"]} for p in rows]


def credit_spread(rating: str = "AA", term: str = "3y",
                  year: Optional[int] = None) -> list[dict]:
    """信用利差 = 中债企业债(rating) − 国债，同日期同期限对齐后本地算差（2 请求/年/日缓存）。

    rating 支持 AAA/AAA-/AA+/AA/AA-（全角＋转半角后匹配曲线树中文名）。
    """
    cn = f"中债企业债收益率曲线({rating.replace('+', '＋')})"
    corp = cb_yield_curve(cn, year)
    gov = cb_yield_curve("treasury", year)
    g = {p["date"]: p["yield_pct"] for p in gov["points"] if p["term"] == term}
    corp_pts = [p for p in corp["points"] if p["term"] == term]
    # P-E：先分清「期限不存在」与「日期对不上」——报错文案直接决定排查方向
    if not corp_pts:
        raise ValueError(f"企业债曲线({rating}) 无 {term!r} 期限数据点。可选期限: "
                         f"{sorted({p['term'] for p in corp['points']})[:12]}")
    if not g:
        raise ValueError(f"国债曲线无 {term!r} 期限数据点。可选期限: "
                         f"{sorted({p['term'] for p in gov['points']})[:12]}")
    out = [{"date": p["date"], "spread_pct": round(p["yield_pct"] - g[p["date"]], 4)}
           for p in corp_pts if p["date"] in g]
    if not out:
        raise ValueError(f"企业债({rating}) 与国债在 {term} 无共同日期（两条曲线日期错位），无法算利差")
    return out


def _dc_query(report: str, sort_col: str, page_size: int = 60,
              filter_str: str = "", pages: int = 1,
              cache_key: Optional[str] = None) -> tuple[list[dict], bool]:
    """东财 datacenter 宏观报表统一查询，返回 (记录, 是否来自缓存)。"""
    if cache_key:
        cached = _cache_read(f"macro/{cache_key}.json", max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached, True
    rows: list[dict] = []
    for pg in range(1, pages + 1):
        params = {"reportName": report, "columns": "ALL", "pageSize": str(page_size),
                  "pageNumber": str(pg), "sortColumns": sort_col, "sortTypes": "-1",
                  "source": "WEB", "client": "WEB"}
        if filter_str:
            params["filter"] = filter_str
        r = ttfund_get("https://datacenter-web.eastmoney.com/api/data/v1/get",
                       params=params,
                       headers={"Referer": "https://data.eastmoney.com/cjsj/"})
        assert_not_risk_control(r, "tt", "L7 宏观")
        r.raise_for_status()
        d = r.json()
        if not d.get("success", False):
            raise RuntimeError(f"东财 {report} 查询失败: {d.get('message')}")
        batch = (d.get("result") or {}).get("data") or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
    return rows, False


_EM_TSY_FIELDS = {"EMM00588704": "cn_2y", "EMM00166462": "cn_5y",
                  "EMM00166466": "cn_10y", "EMM00166469": "cn_30y",
                  "EMG00001306": "us_2y", "EMG00001308": "us_5y",
                  "EMG00001310": "us_10y", "EMG00001312": "us_30y"}


def treasury_yield_daily(days: int = 30) -> list[dict]:
    """国债收益率·日频（东财 RPTA_WEB_TREASURYYIELD，中债口径，**新→旧序**）：中美 2/5/10/30Y+利差。

    days>500 时按 500 截断（单页上限）；中债↔东财同口径（三源互证实测分毫不差）。
    """
    rows, from_cache = _dc_query("RPTA_WEB_TREASURYYIELD", "SOLAR_DATE",
                                 page_size=min(days, 500), cache_key=f"tsy_{min(days, 500)}")
    out = []
    for r in rows[:days]:
        rec = {"date": str(r.get("SOLAR_DATE", ""))[:10]}
        rec.update({v: r.get(k) for k, v in _EM_TSY_FIELDS.items()})
        if all(rec.get(c) for c in ("cn_2y", "cn_10y")):
            rec["cn_10y_2y"] = round(rec["cn_10y"] - rec["cn_2y"], 4)
        out.append(rec)
    if not out:
        raise RuntimeError("东财国债日频返回空（RPTA_WEB_TREASURYYIELD），拒绝静默空")
    if not from_cache:
        _cache_write(f"macro/tsy_{min(days, 500)}.json", rows)
        _cbreak_success("tt")
    return out


def shibor_history(period: Optional[str] = None, days: int = 30) -> list[dict]:
    """SHIBOR 历史（东财 RPT_IMP_INTRESTRATEN，恒定过滤「上海银行同业拆借市场」）。

    🔴 坑（实测）：不加市场过滤时 Chibor（中国银行同业拆借市场）会混入同币种同期限
    序列且数值跳变 ~24bp——本函数恒定锁定上海市场（SHIBOR 本尊）。
    period=None 返回全期限混合；传 '隔夜(O/N)' / '1周(1W)' / '3月(3M)' / '1年(1Y)' 等得单序列。
    days 语义：单序列≈天数；混合模式每自然日 ~8 行（8期限），仅按 days×8 估页取前若干行。
    """
    if period and ('"' in period or "\\" in period):   # 括号是合法期名组成(1Y)，只拦引号断串
        raise ValueError(f"period 含非法字符: {period!r}")
    flt = '(CURRENCY_CODE="CNY")(MARKET="上海银行同业拆借市场")'
    if period:
        flt += f'(REPORT_PERIOD="{period}")'
    window = min(days * (1 if period else 8), 500)
    rows, from_cache = _dc_query("RPT_IMP_INTRESTRATEN", "REPORT_DATE",
                                 page_size=window, filter_str=flt,
                                 cache_key=f"shibor_{period or 'all'}_{window}")
    out = [{"date": str(r.get("REPORT_DATE", ""))[:10], "period": r.get("REPORT_PERIOD"),
            "market": r.get("MARKET"), "rate_pct": r.get("IR_RATE"),
            "change_pct": r.get("CHANGE_RATE")} for r in rows]
    if not out:
        raise RuntimeError(f"SHIBOR 查询返回空（period={period!r}），拒绝静默空")
    if not from_cache:
        _cache_write(f"macro/shibor_{period or 'all'}_{window}.json", rows)
        _cbreak_success("tt")
    if period:                                             # 双保险：按日期去重
        seen, dedup = set(), []
        for r in sorted(out, key=lambda x: x["date"], reverse=True):
            if r["date"] not in seen:
                seen.add(r["date"])
                dedup.append(r)
        return dedup
    return out


def benchmark_rate_history() -> list[dict]:
    """一年期存/贷款基准利率历次调整（东财 RPT_ECONOMY_DEPOSIT_RATE，2004 至今）。

    字段语义（实测锚定 2015-10-24/2012-07-06/2008-11-27 三次央行调息）：
      DEPOSIT_RATE_BB/SB = **调整前**一年定存/一年贷款；BA/SA = **调整后**；
      B/S = 变动**百分点**（非 bp，-0.25 即降 25bp）。⚠️ 本表无活期/短贷字段，勿被字段名 B/BB/S/SB 字面误导。
      ⚠️ 这是基准利率史非 LPR 报价（东财无 LPR 报表，实测 9501）。
    附每次调整次日沪深涨跌（next_sh/sz_pct）。
    """
    rows, from_cache = _dc_query("RPT_ECONOMY_DEPOSIT_RATE", "REPORT_DATE", page_size=100,
                                 cache_key="deposit_rate_all")
    out = [{"report_date": str(r.get("REPORT_DATE", ""))[:10],
            "deposit_1y_prev": r.get("DEPOSIT_RATE_BB"),
            "deposit_1y": r.get("DEPOSIT_RATE_BA"),
            "deposit_change_pp": r.get("DEPOSIT_RATE_B"),
            "loan_1y_prev": r.get("LOAN_RATE_SB"), "loan_1y": r.get("LOAN_RATE_SA"),
            "loan_change_pp": r.get("LOAN_RATE_S"),
            "next_sh_pct": r.get("NEXT_SH_RATE"), "next_sz_pct": r.get("NEXT_SZ_RATE")}
           for r in rows]
    if len(out) < 5:
        raise RuntimeError(f"基准利率史仅 {len(out)} 条（正常 30+），疑似上游结构变更")
    if not from_cache:
        _cache_write("macro/deposit_rate_all.json", rows)
        _cbreak_success("tt")
    return out


def repo_rate_gc() -> dict:
    """GC007 交易所质押式回购利率（腾讯通道，1 请求）——DR007 的代理指标。

    ⚠️ 口径：交易所 vs 银行间（DR007）。走势相关性高，绝对水平有差；
    需要 DR007 本尊须等中国货币网（本网络环境被墙）或付费源。
    """
    q = tencent_quote(["204007"]).get("204007")
    if not q or not q.get("price"):
        raise RuntimeError("GC007(204007) 行情获取失败，拒绝静默空")
    return {"date": datetime.now(_BJ_TZ).strftime("%Y-%m-%d"), "rate_pct": q["price"],
            "change_pct": q.get("change_pct"), "prev_close": q.get("last_close"),
            "proxy_for": "DR007", "source": "tencent/GC007-交易所口径"}


def cn_treasury_realtime() -> dict:
    """国债收益率盘中（push2 全球债券行情 CN2Y/5Y/10Y/30Y）。

    push2 系为 IP 缓释桶限流（坑P26：惩罚期 3 连发即放空）——健康形态写 **300s
    短缓存**（5 分钟内重复调用 0 请求，不让同型四连发放空桶）；失败自动降级
    【中债最新收盘】（cb_yield_curve 既有 curl 通道+当日缓存，不轰炸重试），
    as_of 标注 "盘中(push2)" / "中债最新收盘(降级)"。V0.7.5：降级结果写**独立
    短缓存键**（不污染健康缓存）——此前降级不写缓存，push2 处于"软失败未触发
    连接级熔断"窗口时每次调用重打 4 发（P26 教训：诊断行为本身烧 IP 额度）；
    独立键 300s 到期自动重试主路，保持"尽快恢复主路"契约。
    值域守卫：任一期限收益率 ∉(0,15) → RuntimeError 拒绝静默脏值
    （缩放错位/上游喂零一并兜住，ulist 式全零源即死于此）。"""
    rel = "treasury/realtime_push2.json"
    degraded_rel = "treasury/realtime_degraded.json"   # V0.7.5：降级独立短缓存键
    cached = _cache_read(rel, max_age_hours=300 / 3600)
    if cached is not None:
        return cached
    cached = _cache_read(degraded_rel, max_age_hours=300 / 3600)
    if cached is not None:
        return cached
    out = {}
    degraded = False
    try:
        for term, sec in [("2y", "CN2Y"), ("5y", "CN5Y"), ("10y", "CN10Y"), ("30y", "CN30Y")]:
            d = _push2_get("https://push2.eastmoney.com/api/qt/stock/get",
                           {"secid": f"171.{sec}", "fields": "f43,f58,f169,f170"},
                           f"国债实时 {sec}").get("data") or {}
            if not d or not d.get("f43"):
                raise RuntimeError(f"{sec} 实时行情为空（f43 缺失），拒绝静默空")
            y = d["f43"] / 10000
            if not (0 < y < 15):
                raise RuntimeError(f"{sec} 收益率异常 {y}（f43={d['f43']}），拒绝静默脏值")
            out[term] = {"yield_pct": y,
                         "change": (d.get("f169") or 0) / 10000,
                         "change_pct": (d.get("f170") or 0) / 100}
        out["source"] = "eastmoney/push2-globalbond"
        out["as_of"] = "盘中(push2)"
    except (RiskControlError, CircuitOpenError):
        raise                                          # 风险型信号绝不降级（F6 契约）
    except RuntimeError as e:
        # P26：push2 缓释桶额度耗尽/连接级熔断——降级中债最新收盘（cb_yield_curve
        # 当日缓存通道，失败不重试轰炸）；盘中/收盘口径差异由 as_of 显式标注
        degraded = True
        pts = cb_yield_curve("treasury")["points"]
        last_date = pts[-1]["date"]
        by_term = {q["term"]: q for q in pts if q["date"] == last_date}
        for term in ("2y", "5y", "10y", "30y"):
            if term not in by_term:
                raise RuntimeError(f"中债降级缺 {term} 期限（{last_date}）") from e
            y = by_term[term]["yield_pct"]
            if not (isinstance(y, (int, float)) and not isinstance(y, bool) and 0 < y < 15):
                # V0.8.1：降级值过同款值域守卫——中债脏值不得借降级键驻留 300s
                raise RuntimeError(f"中债降级 {term} 收益率异常 {y!r}，拒绝写降级缓存") from e
            out[term] = {"yield_pct": y,
                         "change": None, "change_pct": None}
        out["source"] = f"chinabond/收盘降级（push2 冷却: {str(e)[:40]}）"
        out["as_of"] = f"中债最新收盘(降级,{last_date})"
    out["time"] = datetime.now(_BJ_TZ).isoformat(timespec="seconds")
    if not degraded:
        _cbreak_success("em")
    # 健康写主键；降级写独立键（同 300s）——掐掉降级期重打 push2 的窗口，
    # 到期自动重试主路（V0.7.5，原"降级不写缓存"契约升级）
    _cache_write(rel if not degraded else degraded_rel, out)
    return out
```

## 层 6 · 全市场数据层（V0.5：rankhandler 排行底座 + 新发 + 公司画像 + 巨潮抱团度/行业配置）

```python
# 防封设计（第一约束，2026-11-10 实测钉死）：
#   · rankhandler / FundNewIssue / company 与 E1/E2 同域（fund.eastmoney.com）→ tt 线
#     全套继承（F1 锁 / F2 熔断 / 1.5s 串行 / 会话复用）。rankhandler 的风控形态不是
#     403 而是 200+"无访问权限"（坑P19）→ 内容级断言 assert_rankhandler 计入熔断。
#   · cninfo 独立第四线：.throttle_cninfo 磁盘锁 + F2 熔断 + 独立账本
#     CNINFO_REQUEST_COUNT；enckey=AES(秒级时间戳) 本地算（坑P20，subprocess node
#     标准库，同 P11 curl 先例零新依赖）；451 token 过期=钥匙时效问题非风控→刷新重试。
#   · 预算契约：全市场排行 = 1 请求（pn=25000 实测 24792 行）+ 20h 缓存，类型/排序
#     筛选全在缓存上本地做；新发全量 ≈7 页；抱团度/行业配置 = 1 请求/季度长效缓存
#     （季末后120天内短缓存，其后长缓存）。fund_rank 不暴露翻页参数——逐页爬 496 次的封禁路径
#     从函数签名上不存在。

CNINFO_REQUEST_COUNT = [0]                     # 第四线账本（审计用）
CNINFO_MIN_INTERVAL = 1.5                      # cninfo 线最小间隔（秒）
_CNINFO_AES_KEY = "1234567887654321"           # 巨潮公开 cninfo.js 中的固定密钥（坑P20）
_NODE_ENCKEY_TMPL = (
    "const crypto=require('crypto');"
    "const c=crypto.createCipheriv('aes-128-cbc',"
    "Buffer.from('%s'),Buffer.from('%s'));"
    "console.log(Buffer.concat([c.update(String(%d),'utf8'),c.final()]).toString('base64'));"
)


def _cninfo_enckey(ts: Optional[int] = None) -> str:
    """cninfo Accept-Enckey：AES-128-CBC(秒级时间戳) 的 Base64（坑P20）。

    密钥/IV 是巨潮公开 cninfo.js 里的固定串；用系统 node 标准库计算（与中债
    subprocess curl 同为先例，零新 Python 依赖）。ts 可注入——确定性测试用。
    """
    import subprocess
    if ts is None:
        ts = int(time.time())
    out = subprocess.run(
        ["node", "-e", _NODE_ENCKEY_TMPL % (_CNINFO_AES_KEY, _CNINFO_AES_KEY, ts)],
        capture_output=True, text=True, timeout=10)
    key = out.stdout.strip()
    if out.returncode != 0 or not key.endswith("="):
        raise RuntimeError(f"enckey 计算失败（需系统 node）: {out.stderr[:80]}")
    return key


def assert_rankhandler(resp, context: str = "rankhandler"):
    """rankhandler 家族内容级风控断言；业务成功由完整记录校验后另行确认。"""
    assert_not_risk_control(resp, "tt", context)
    text = resp.content.decode("utf-8", errors="replace")[:500]
    _assert_not_risk_content(text, "tt", context)


def _cninfo_post(sysapi: str, params: dict, context: str) -> list:
    """cninfo 专题统计统一 POST：第四线节流 + enckey + 451 刷新重试一次。

    451/"token null" = enckey 时效问题（本地时钟漂移或秒边界）非风控——刷新钥匙
    重试一次，仍拒才报错；403/429 仍走 assert_not_risk_control 计熔断。
    Sprint 2：协调核心激活时改走 Gateway（cninfo 独立配额账；451 重试=新许可
    独立计配额），2xx 中性结算，业务成功由调用方 _cbreak_success("cninfo")。
    """
    for attempt in (1, 2):
        if _REQUEST_GATEWAY is None:
            _cbreak_check("cninfo")
            _cross_proc_throttle("cninfo", CNINFO_MIN_INTERVAL)
        key = _cninfo_enckey()
        try:
            if _REQUEST_GATEWAY is not None:
                r = _REQUEST_GATEWAY.post(
                    f"https://webapi.cninfo.com.cn/api/sysapi/{sysapi}",
                    params=params, timeout=20, retries=0,
                    headers={"Accept-Enckey": key,
                             "Referer": "https://webapi.cninfo.com.cn/",
                             "X-Requested-With": "XMLHttpRequest"},
                    session=_SessionAdapter(TT_SESSION, _cninfo_hit))
                _release_pending(r, "cninfo-passthrough")
            else:
                CNINFO_REQUEST_COUNT[0] += 1
                r = TT_SESSION.post(f"https://webapi.cninfo.com.cn/api/sysapi/{sysapi}",
                                    params=params, timeout=20,
                                    headers={"Accept-Enckey": key,
                                             "Referer": "https://webapi.cninfo.com.cn/",
                                             "X-Requested-With": "XMLHttpRequest"})
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.0)
            continue
        assert_not_risk_control(r, "cninfo", context)
        try:
            d = r.json()
        except ValueError as e:                     # 非反爬 JSON（HTML 拦截页等）→ 带上下文报错
            raise RuntimeError(f"cninfo {context} 返回非 JSON: {r.content[:80]!r}") from e
        msg = str(d.get("resultmsg") or "")
        if d.get("resultcode") == 451 or "token" in msg:
            if attempt == 1:                        # 钥匙时效：刷新重试（非风控不计熔断）
                continue
            raise RuntimeError(f"cninfo enckey 两次被拒（{context}）：检查本机时钟是否漂移")
        recs = d.get("records")
        if not recs:
            raise RuntimeError(f"cninfo {context} 返回空 records: {str(d)[:120]}")
        return recs
    raise RuntimeError(f"cninfo {context} 重试路径未达（不可达分支）")


def _norm_rdate(rdate: str = "") -> str:
    """报告期季末归一化："20260930"/"2026-09-30" → "20260930"；空=最近已发布季末。"""
    s = re.sub(r"-", "", str(rdate or "").strip())
    if not s:
        return _latest_quarter_end().replace("-", "")
    valid = {(m, d) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))}
    if not re.fullmatch(r"\d{8}", s) or (int(s[4:6]), int(s[6:8])) not in valid:
        raise ValueError(f"rdate 须为季末日（0331/0630/0930/1231 之一），收到 {rdate!r}")
    return s


def _latest_quarter_end(today: Optional[date] = None) -> str:
    """≤today 的最近季末日（跨年自动回退到上年 12-31）。"""
    d = today or date.today()
    cands = [date(d.year, m, dd) for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31))]
    cands.append(date(d.year - 1, 12, 31))
    return max(c for c in cands if c <= d).isoformat()


def _quarter_cache_ttl_hours(rd8: str, today: Optional[date] = None) -> int:
    """季末后 120 天内使用短缓存，避免跨季末把未披露完的数据过早固化。"""
    current = today or date.today()
    report_date = datetime.strptime(rd8, "%Y%m%d").date()
    if report_date > current:
        raise ValueError(f"rdate 是未来季末（{rd8}，当前 {current.isoformat()}），拒绝查询")
    age_days = (current - report_date).days
    return (PROVISIONAL_QUARTER_TTL_SECONDS // 3600 if age_days <= 120 else 90 * 24)


# ── ① 全市场排行 / 初筛底座 ──────────────────────────────────────

_RANK_SC = {"ytd": "1nzf", "1m": "1yzf", "3m": "3yzf", "6m": "6yzf",
            "1y": "1n", "2y": "2n", "3y": "3n", "since": "ln"}
_RANK_FT = {"all", "zs", "gp", "hh", "zq", "qdii", "fof"}
_RANK_SORT_KEY = {"ytd": "ret_ytd_pct", "1m": "ret_1m_pct", "3m": "ret_3m_pct",
                  "6m": "ret_6m_pct", "1y": "ret_1y_pct", "2y": "ret_2y_pct",
                  "3y": "ret_3y_pct", "since": "ret_since_pct"}
# rankhandler 行不带类型 → 本地过滤用 E1 中文类型映射到类别桶
# （REIT/货币归 zq、商品归 zs 仅为七桶覆盖；跨类收益量级不可直接比较）
_TYPE_BUCKETS = (("指数型", "zs"), ("QDII", "qdii"), ("FOF", "fof"), ("股票型", "gp"),
                 ("混合型", "hh"), ("债券型", "zq"), ("REIT", "zq"),
                 ("Reits", "zq"), ("商品", "zs"), ("货币", "zq"))


def _bucket_of(fund_type) -> Optional[str]:
    if not fund_type:
        return None
    for prefix, bucket in _TYPE_BUCKETS:
        if prefix in fund_type:
            return bucket
    return None


def _rank_row(raw: str, type_map: dict) -> dict:
    """rankhandler CSV 行 → dict。字段序经官方 syl 四点校准（161725：近1月/3月/6月/
    1年分毫不差，2026-11-10）；[17][18][24] 语义不明不映射（[19]=原费率）。"""
    f = raw.split(",")

    def num(i):
        try:
            v = f[i] if i < len(f) else ""
            return float(v) if v not in ("", "--") else None
        except ValueError:
            return None

    def s(i):
        return f[i] if i < len(f) else ""

    code = s(0)
    fee = None
    if len(f) > 19 and f[19].endswith("%"):
        try:
            fee = float(f[19][:-1])
        except ValueError:
            pass
    return {"code": code, "name": s(1), "pinyin": s(2), "nav_date": s(3) or None,
            "nav": num(4), "ac_nav": num(5), "daily_pct": num(6),
            "ret_1w_pct": num(7), "ret_1m_pct": num(8), "ret_3m_pct": num(9),
            "ret_6m_pct": num(10), "ret_1y_pct": num(11), "ret_2y_pct": num(12),
            "ret_3y_pct": num(13), "ret_ytd_pct": num(14), "ret_since_pct": num(15),
            "founded": s(16) or None, "fee_pct": fee,
            "fund_type": type_map.get(code)}


def _rank_fetch(ft: str, sc: str, pn: int) -> list:
    """rankhandler 单次拉取（tt 线 + Referer 鉴权 + allRecords 强校验防截断）。"""
    today_iso = date.today().isoformat()
    r = ttfund_get("https://fund.eastmoney.com/data/rankhandler.aspx",
                   params={"op": "ph", "dt": "kf", "ft": ft, "pi": 1, "pn": pn,
                           "sd": (date.today() - timedelta(days=7)).isoformat(),
                           "ed": today_iso, "sc": sc, "st": "desc"},
                   headers={"Referer": "https://fund.eastmoney.com/data/fundranking.html"})
    assert_rankhandler(r, f"rankhandler ft={ft} sc={sc}")
    text = r.content.decode("utf-8", errors="replace")
    rows = re.findall(r'"(\d{6},[^"]*)"', text)
    m = re.search(r"allRecords:(\d+)", text)
    total = int(m.group(1)) if m else 0
    if not rows or not total:
        raise RuntimeError(f"rankhandler 返回空（ft={ft}）: {text[:100]!r}")
    # 全量模式必须总数一致；TopN 的 allRecords 是类别总数，只校验本页应收条数。
    expected = total if pn == 25000 else min(pn, total)
    if len(rows) != expected:
        raise RuntimeError(f"rankhandler 校验失败 allRecords={total} 实收 {len(rows)}，"
                           f"应收 {expected}（静默截断拒绝，pn={pn}）")
    _cbreak_success("tt")
    return rows


def fund_rank(top: Optional[int] = None, fund_type: str = "all", sort: str = "ytd",
              ascending: bool = False, force_refresh: bool = False) -> list[dict]:
    """全市场基金排行 / 初筛底座（坑P19；全量 = 1 请求）。

    两种形态（无翻页参数——逐页爬 496 次的封禁路径从签名上不存在）：
      top=None（默认）：全市场一次拉（pn=25000 实测 24792 行），20h 缓存；
        fund_type/sort/ascending 全部在缓存上【本地】执行——组合筛选 0 请求
      top=N（1~100）：服务端单页（ft+sc 下推，取该类前 N 名），当日缓存；
        ascending=True 表示【前 N 名窗口内】升序展示（服务端仍按该字段取前 N）
    fund_type: all/zs/gp/hh/zq/qdii/fof（本地过滤经 E1 类型映射）；
    sort: ytd/1m/3m/6m/1y/2y/3y/since；None 值恒排末尾。
    """
    if fund_type not in _RANK_FT:
        raise ValueError(f"fund_type 须为 {sorted(_RANK_FT)}，收到 {fund_type!r}")
    if sort not in _RANK_SC:
        raise ValueError(f"sort 须为 {sorted(_RANK_SC)}，收到 {sort!r}")
    key_field = _RANK_SORT_KEY[sort]
    if top is None:
        rel = "rank/full_market.json"
        cached = None if force_refresh else _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            rows = cached
        else:
            type_map = {f["code"]: f["type"] for f in fund_list()}
            rows = [_rank_row(raw, type_map) for raw in _rank_fetch("all", "1nzf", 25000)]
            _cache_write(rel, rows)
        if fund_type != "all":
            rows = [r for r in rows if _bucket_of(r.get("fund_type")) == fund_type]
        # None 恒排末尾：降序把 None 映射 -inf、升序映射 +inf，再统一 reverse
        nil = float("-inf") if not ascending else float("inf")
        rows.sort(key=lambda r: r.get(key_field) if r.get(key_field) is not None else nil,
                  reverse=not ascending)
        return rows
    top = int(top)
    if not 1 <= top <= 100:
        raise ValueError("top 只支持 1~100（更大需求走全量模式 top=None 本地排序）")
    # 缓存键含 top+sort：服务端窗口（按 sc 字段取前 N 名）随两者变化，键必须同变；
    # 仅 ascending 不入键——升/降序在缓存读取后本地重排兑现
    rel = f"rank/top_{fund_type}_{sort}_{top}.json"
    rows = None if force_refresh else _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
    if rows is None:
        type_map = {f["code"]: f["type"] for f in fund_list()}
        # _RANK_FT 是合法 ft 值集合（fund_type 已校验在其中）；服务端恒 desc，
        # ascending 语义由下方本地重排兑现
        rows = [_rank_row(raw, type_map)
                for raw in _rank_fetch(fund_type, _RANK_SC[sort], top)]
        _cache_write(rel, rows)
    nil = float("-inf") if not ascending else float("inf")
    rows = sorted(rows, key=lambda r: r.get(key_field) if r.get(key_field) is not None else nil,
                  reverse=not ascending)
    return rows


# ── ② 新发基金 ────────────────────────────────────────────────────

def fund_new_issues(force_refresh: bool = False) -> list[dict]:
    """在发/新成立基金全量（FundNewIssue.aspx，tt 线 ≈7 页×1 请求，20h 缓存）。

    返回 [{code,name,company,company_id,fund_type,issue_period,fee_pct,manager}]，
    record 总数强校验（坑P2 同款防线，实测 record=332/pages=7）。
    顺带产出「公司名→公司ID」映射（fund_company 消歧用）。
    """
    rel = "new_issues_v2.json"       # v2：保留合法零费率，避免旧缓存把 0.0 写成 None
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return cached
    out, total = [], 0
    for page in range(1, 11):                        # 上限 10 页（500 条）防失控
        r = ttfund_get("https://fund.eastmoney.com/data/FundNewIssue.aspx",
                       params={"dt": 14, "page": f"{page},50", "order": "issuedate"},
                       headers={"Referer": "https://fund.eastmoney.com/data/xinfound.html"})
        assert_rankhandler(r, f"新发 page{page}")
        ntext = r.content.decode("utf-8", errors="replace")
        m = re.search(r"datas:\s*(\[.*?\])\s*,\s*record", ntext, re.S)
        if not m:
            raise RuntimeError(f"新发响应无 datas（page{page}）: {r.text[:80]!r}")
        try:
            arrs = json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"新发 datas 解析失败（page{page}）: {e}") from e
        for a in arrs:
            def g(i):
                return a[i] if len(a) > i else ""
            out.append({"code": g(0), "name": g(1), "company": g(2), "company_id": g(3),
                        "fund_type": g(4), "issue_period": g(5),
                        "fee_pct": _to_float((g(6) or "").replace("%", "")),
                        "manager": g(7) or None})
        mt = re.search(r"record:(\d+)", ntext)
        if not mt:                              # record 缺席=强校验失效，拒绝静默跳过
            raise RuntimeError(f"新发响应无 record 总数（page{page}），截断校验不可用")
        total = int(mt.group(1))
        if len(out) >= total:
            break
        if not arrs:
            break
    if len(out) != total:
        raise RuntimeError(f"新发分页校验失败 record={total} 实收 {len(out)}（截断拒绝）")
    if not out:
        raise RuntimeError("新发列表为空，拒绝静默返回")
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out


# ── ③ 基金公司画像 ────────────────────────────────────────────────

def _company_id_from_fund(fund_query: str) -> tuple:
    """基金 → (公司ID, 公司名)：F10 概况页（jbgk）服务端直出的 company/{id} 直链。"""
    code = resolve_fund_code(fund_query)
    r = ttfund_get(f"https://fundf10.eastmoney.com/jbgk_{code}.html",
                   headers={"Referer": "https://fundf10.eastmoney.com/"})
    assert_rankhandler(r, f"jbgk {code}")
    # ⚠️ 链接锚文本是通用的"基金公司"字样而非公司名——只取 ID，名字走详情页 title
    m = re.search(r'company/(\d{8})\.html',
                  r.content.decode("utf-8", errors="replace"))
    if not m:
        raise RuntimeError(f"jbgk 页未找到公司直链（{code}）——上游结构变更")
    _cbreak_success("tt")
    return m.group(1), None


def fund_company(query: str, force_refresh: bool = False) -> dict:
    """基金公司画像（company/{id}.html 服务端直出，24h 缓存，1 请求）。

    query 三种形态：8 位数字公司ID（如 80065113）/ 基金代码或名称（6 位码是基金，
    经 jbgk 直链转公司，1 额外请求）/ 公司名（best-effort：先查新发缓存免请求命中；
    未命中则按基金名路径尝试——公司名≠基金名，多半显式报错，绝不静默错配）。
    返回 {company_id,name,scope_yi 管理规模(亿),fund_count 旗下基金数,
    manager_count 经理人数,funds:[{code,name}]}（旗下基金服务端表格去重）。
    """
    q = str(query).strip()
    jbgk_name = None
    if re.fullmatch(r"\d{8}", q):                   # 公司ID 恒 8 位（6 位是基金代码）
        cid = q
    else:
        cid = None
        # best-effort：新发缓存里的公司名→ID（免请求）；风控信号绝不吞——
        # 已计入熔断的信号若被 pass 掉，后续仍会连发 jbgk+company 两请求打枪口
        try:
            for it in fund_new_issues():
                if it.get("company") == q:
                    cid = it["company_id"]
                    break
        except (RiskControlError, CircuitOpenError):
            raise
        except Exception:
            pass
        if cid is None:                              # 走基金→jbgk→公司直链
            cid, jbgk_name = _company_id_from_fund(q)
    rel = f"company/{cid}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24)
        if cached is not None:
            return cached
    r = ttfund_get(f"https://fund.eastmoney.com/company/{cid}.html",
                   headers={"Referer": "https://fund.eastmoney.com/company/default.html"})
    assert_rankhandler(r, f"公司 {cid}")
    h = r.content.decode("utf-8", errors="replace")
    if len(h) < 10000:                               # 跳转页/空壳防线（E2 同款）
        raise RuntimeError(f"公司页异常（{cid} 仅 {len(h)}B）")
    def _num_near(label: str, unit: str) -> Optional[float]:
        """标签段内去 HTML 标签/空白/千分位逗号后取「label+数字+unit」。数字常被
        <span> 包裹；同名 label 会先出现在导航链接里（无数字），须逐个出现位置
        扫描直到命中。"""
        for i in (mm_.start() for mm_ in re.finditer(re.escape(label), h)):
            seg = re.sub(r"<[^>]+>|\s+|,", "", h[i:i + 300])
            m = re.search(re.escape(label) + r"[:：]?([0-9.]+)" + unit, seg)
            if m:
                return float(m.group(1))
        return None

    mt = re.search(r"<title>([^<\-]{2,25})", h)
    name = ((mt.group(1).strip() if mt else "").split("_")[0].split("-")[0]
            .strip().rstrip("主页").strip()) or None
    ms_v = _num_near("管理规模", "亿元")
    mf_v = _num_near("基金数量", "只")
    mm_v = _num_near("经理人数", "人")
    seen, funds = set(), []
    for code2, name2 in re.findall(
            r'href="https?://fund\.eastmoney\.com/(\d{6})\.html"[^>]*>([^<]{2,30})</a>', h):
        n2 = name2.strip()
        if code2 in seen or n2.isdigit() or n2 in ("基金品种", "净值", "持仓", "档案"):
            continue
        seen.add(code2)
        funds.append({"code": code2, "name": n2})
    out = {"company_id": cid, "name": name or jbgk_name,
           "scope_yi": ms_v,
           "fund_count": int(mf_v) if mf_v is not None else None,
           "manager_count": int(mm_v) if mm_v is not None else None,
           "funds": funds}
    if out["scope_yi"] is None and out["fund_count"] is None:
        raise RuntimeError(f"公司页字段解析失败（{cid}）——上游结构变更，拒绝半空返回")
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out


# ── ④ 抱团度 / 行业配置（cninfo 第四线）──────────────────────────

def fund_crowding(rdate: str = "", force_refresh: bool = False) -> list[dict]:
    """季度基金重仓股聚合（cninfo 官方专题统计 p_sysapi1112，坑P20）→ 抱团度原料。

    rdate 报告期季末（"20260930"/"2026-09-30" 皆可，空=最近已发布季末）。
    返回 [{code,name,fund_count 被多少只基金持有,shares_wan 持股总数(万),
    market_value_wan 持股市值(万元),date}] 按 fund_count 降序——TOP50 排名与
    TOP10 平均家数（市场抱团温度计）直接从这张表算。季末后 120 天内 1 小时
    短缓存，其后 90 天长缓存。实测 2026Q3：3088 只重仓股，Top1 中际旭创 2424 家。
    ⚠️ 披露/修正窗口按报告期天数判断，跨季末不会把上一季过早转成长缓存；
    未来季末显式拒绝，force_refresh 可强制刷新。
    """
    rd8 = _norm_rdate(rdate)
    rel = f"crowding/{rd8}.json"
    ttl_hours = _quarter_cache_ttl_hours(rd8)       # force_refresh 也必须先拒绝未来季末
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=ttl_hours)
        if cached is not None:
            return cached
    recs = _cninfo_post("p_sysapi1112",
                        {"rdate": f"{rd8[:4]}-{rd8[4:6]}-{rd8[6:]}"}, f"基金重仓股 {rd8}")
    out = []
    for x in recs:
        try:
            out.append({"code": x.get("SECCODE"), "name": x.get("SECNAME"),
                        "fund_count": int(float(x.get("F001N") or 0)),
                        "shares_wan": float(x.get("F002N") or 0),
                        "market_value_wan": float(x.get("F003N") or 0),
                        "date": (x.get("ENDDATE") or "")[:10]})
        except (TypeError, ValueError):
            continue
    if len(out) < 100:
        raise RuntimeError(f"基金重仓股聚合仅解析出 {len(out)} 行（{rd8}），疑似结构变更")
    out.sort(key=lambda r: -r["fund_count"])
    _cbreak_success("cninfo")
    _cache_write(rel, out)
    return out


def fund_industry_allocation(rdate: str = "", force_refresh: bool = False) -> list[dict]:
    """季度全市场基金行业配置聚合（cninfo p_sysapi1113，证监会行业门类口径）。

    返回 [{industry_code 门类码,industry 门类名,fund_count 持有基金家数,
    market_value_yi 持股市值(亿),weight_pct 占比%,date}] 按市值降序。
    实测 2026Q3：19 个门类（制造业 4.6 万亿/11.94% 居首）。同 fund_crowding 节流缓存，
    披露窗口内最新季聚合同样可能不完整（见 fund_crowding ⚠️）。
    """
    rd8 = _norm_rdate(rdate)
    rel = f"industry/{rd8}.json"
    ttl_hours = _quarter_cache_ttl_hours(rd8)       # force_refresh 也必须先拒绝未来季末
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=ttl_hours)
        if cached is not None:
            return cached
    recs = _cninfo_post("p_sysapi1113",
                        {"rdate": f"{rd8[:4]}-{rd8[4:6]}-{rd8[6:]}"}, f"行业配置 {rd8}")
    out = []
    for x in recs:
        try:
            out.append({"industry_code": x.get("F001V"), "industry": x.get("F002V"),
                        "fund_count": int(float(x.get("F003N") or 0)),
                        "market_value_yi": float(x.get("F004N") or 0),
                        "weight_pct": float(x.get("F005N") or 0),
                        "date": (x.get("ENDDATE") or "")[:10]})
        except (TypeError, ValueError):
            continue
    if len(out) < 10:
        raise RuntimeError(f"行业配置仅解析出 {len(out)} 行（{rd8}），疑似结构变更")
    out.sort(key=lambda r: -r["market_value_yi"])
    _cbreak_success("cninfo")
    _cache_write(rel, out)
    return out
```

## 层 7 · 经理画像与专项组装层（V0.6：持续性/规模/固收+/溢价/重仓探测/经理三件套）

```python
# 探针结论（2026-11-10 实测钉死）：
#   · rankhandler 行【无规模列】——[17][18]=自定义区间收益、[19][20]=申购费原价/折扣价、
#     [22]=优惠费率（161725/110022 双锚校准）；单基金规模走移动端 ENDNAV（209.47亿精确锚）
#   · 经理数据两源全通：主页 manager/{id}.html 服务端直出（mgrlists 在管清单+任职+规模+回报）、
#     dt=14 排行（4384 位，pn≤50 且服务端仅姓名序）；姓名→ID 无公开索引（坑P23：
#     dt=15 目录页忽略 pi/pn 恒返回拼音前10，FundSearchAPI 不索引经理名——二次实测 2026-11-11）
#   · FOF 底层基金明细五源实测无结构化接口（坑P24）——只有季报 PDF 有，诚实边界


def _tencent_kline_page(pre: str, end_date: Optional[str] = None,
                        bars: Optional[int] = None, fq: str = "qfq") -> list[dict]:
    """腾讯 fqkline 单页拉取（V0.7.6 抽取为翻页协议原子操作）。

    param 第 3/4 槽为起止日期：end_date 空=最新页；给定=以该日为页末锚
    （服务端回含锚日的行，与上一页最早行重叠 +1，供缝合校验）。
    页内升序 [{date, close}]；仅支持带 sh/sz 前缀的 6 位码。
    bars 缺省 _TENCENT_KLINE_CAP（体内解析——常量定义在本函数之后，默认参数
    def 时求值会 NameError）。
    fq：默认 "qfq" 前复权（既有调用方行为不变）；"" = 不复权（V0.8.4 公募 REITs
    层——腾讯前复权是**减法复权**，除息日前复权价 = 不复权价 − 分红，算分红再投资
    收益须用不复权收盘价自算；param 尾槽留空，返回键为 day）。"""
    if fq not in ("qfq", ""):
        raise ValueError(f"fq 只支持 'qfq'（前复权）或 ''（不复权），收到 {fq!r}")
    if bars is None:
        bars = _TENCENT_KLINE_CAP
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={pre},day,,{end_date if end_date else ''},{int(bars)},{fq}")
    raw = None
    for attempt in (1, 2):                        # 瞬态重试1次（对齐 tencent_quote 同通道）
        try:
            raw = _tencent_raw(url, headers={"Referer": "https://gu.qq.com/"}).decode("utf-8")
            break
        except urllib.error.HTTPError:
            raise                                 # 有状态码的失败不属瞬态重试（V0.7.5
                                                 # tencent_quote 同口径）；HTTPError 是
                                                 # OSError 子类，须先于其捕获防误重试
        except OSError as e:
            if attempt == 2:
                raise RuntimeError(f"腾讯日K请求失败({pre}): {e}") from e
            time.sleep(0.5)
    try:
        d = json.loads(raw)
    except ValueError as e:
        raise RuntimeError(f"腾讯日K非JSON({pre}): {raw[:80]!r}") from e
    node = (d.get("data") or {}).get(pre) or {}
    rows = node.get("qfqday") or node.get("day") or []
    return [{"date": x[0], "close": float(x[2])} for x in rows if len(x) >= 3]


def _stitch_tencent_pages(pages: list[list[dict]]) -> list[dict]:
    """翻页缝合（纯函数，零网络）：页序=新→旧，页内升序。

    每道页缝「日期+收盘」双校验：下一页页尾必须恰为当前最早行（服务端 +1
    重叠协议，2026-11-17 全史翻页实测逐缝吻合）——断档/越界/收盘漂移任一
    即整批拒绝（拒绝静默错数据）；最终重复日期防御性复核。"""
    if not pages or not pages[0]:
        raise RuntimeError("腾讯翻页缝合失败：首页为空，拒绝拼接")
    merged = list(pages[0])
    for i, seg in enumerate(pages[1:], start=2):
        if not seg:
            break                                    # 防御分支：翻页层已拦截中段空页并
                                                     # 整批拒绝（+1 协议下空页必为异常）；
                                                     # 独立调用本函数时按历史到头截断
        tail = seg[-1]
        anchor = merged[0]
        if tail["date"] != anchor["date"]:
            raise RuntimeError(
                f"腾讯翻页缝合失败：第{i}页页尾 {tail['date']} ≠ 锚 {anchor['date']}"
                "（断档或越界），整批拒绝")
        if abs(tail["close"] - anchor["close"]) > 1e-9:
            raise RuntimeError(
                f"腾讯翻页缝合失败：{anchor['date']} 缝收盘不一致"
                f"（{anchor['close']} vs {tail['close']}），整批拒绝")
        merged = [r for r in seg if r["date"] < anchor["date"]] + merged
    if len(merged) != len({r["date"] for r in merged}):
        raise RuntimeError("腾讯翻页缝合失败：合并后存在重复日期，整批拒绝")
    return merged


def _tencent_kline_paginated(sym: str, lmt: int = 2600) -> list[dict]:
    """腾讯日K翻页全史（V0.7.6）：fqkline 单页 800 上限（P25）按「页末锚」
    向历史翻页缝合——7~8 发串行拼 20+ 年全史（实测：茅台 6044 根/沪深300
    5250 根，qfq 因子跨请求 0 漂移，2026-11-17）。

    停止条件：短页（<800=历史到头）/ 集满 lmt / 页数安全阀 _TENCENT_PAGE_CAP /
    锚不前进（疑似上游异常）。锚停滞且未集满 lmt → 拒绝静默截断报错。
    串行翻页（协调器 0.2s 地板自持）；TENCENT_REQUEST_COUNT 随 _tencent_raw
    逐页入账；缓存归调用方（kline_daily 层），本函数不落盘。
    拒绝静默截断三防线（V0.7.6 审查加固）：锚停滞 / 页数阀耗尽（末页满页
    即历史未到头）/ 非首页空页（+1 重叠协议下锚定页必含锚日 ≥1 行，空页
    必为异常）——三者未集满 lmt 即整批拒绝。集满口径按去重计（每页 +1
    重叠行不计入）。"""
    pages: list[list[dict]] = []
    end: Optional[str] = None
    stalled = False
    for _ in range(_TENCENT_PAGE_CAP):
        seg = _tencent_kline_page(sym, end)
        if not seg:
            if pages and sum(len(p) for p in pages) - (len(pages) - 1) < lmt:
                raise RuntimeError(               # 首页空→缝合层拒；中段空页=协议异常
                    f"腾讯翻页异常（{sym} 锚定页返回 0 行，违反 +1 重叠协议），整批拒绝")
            break
        pages.append(seg)
        if len(seg) < _TENCENT_KLINE_CAP:
            break                                    # 短页=历史到头
        collected = sum(len(p) for p in pages) - (len(pages) - 1)   # 去重口径
        if collected >= lmt:
            break                                    # 集满即停（重叠行由缝合去重）
        new_end = seg[0]["date"]
        if end is not None and new_end >= end:
            stalled = True                           # 锚不前进：同一页反复回（上游异常形态）
            break
        end = new_end
    rows = _stitch_tencent_pages(pages)
    # 页数阀耗尽：末页仍是满页=历史未到头且未集满——静默截断零信号不可接受
    exhausted = len(pages) >= _TENCENT_PAGE_CAP and len(pages[-1]) >= _TENCENT_KLINE_CAP
    if len(rows) < lmt and (stalled or exhausted):
        raise RuntimeError(
            f"腾讯翻页{'锚停滞' if stalled else '页数阀耗尽'}（{sym} 已集 {len(rows)}/{lmt} 根）"
            "——疑似上游异常或超出安全阀容量，拒绝静默截断")
    _cbreak_success("tencent")
    return rows[-lmt:]


def _tencent_kline(stock_code: str, bars: int = 90,
                   force_refresh: bool = False) -> list[dict]:
    """腾讯日K前复权收盘序列 [{date, close}]（nav_divergence 重仓股区间收益用）。

    腾讯线（不封 IP）+ P12 直连 opener 同款 + TENCENT_REQUEST_COUNT 审计入账；
    仅支持 6 位 A 股码（港股 5 位码由调用方过滤）；当日缓存。
    V0.7.6：单页拉取抽取为 _tencent_kline_page（翻页协议共用）。"""
    pre = _tencent_prefix(stock_code)
    rel = f"kline/{pre}_{int(bars)}.json"   # bars 入键：窗口根数随基金/日期漂移，防跨窗口串缓存
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    out = _tencent_kline_page(pre, bars=bars)
    if len(out) < 2:
        raise RuntimeError(f"腾讯日K空返回({pre})")
    _cbreak_success("tencent")
    _cache_write(rel, out)
    return out


def rank_persistence(top: int = 20, fund_type: str = "all",
                     force_refresh: bool = False) -> list[dict]:
    """业绩持续性矩阵（零请求）：排行缓存上算各周期同类分位与评分。

    周期 = 1m/3m/6m/ytd/1y/2y/3y；分位在【同类别桶内】算（vs 全市场更公平）；
    纳入门槛：≥4 个周期有值且必须含 ytd 与 1y（过短的新基不参与）。
    score_pct = 各周期分位均值；consistent = 全部已覆盖周期 ≥75 分位。
    bucket_n = 所属类别桶有值基金数——桶过小（<5）时恒高/低分位参考意义有限，自行降权。
    top=None 返回全部（按 score 降序）。组合初筛的「长牛」候选直接取前 N。"""
    if top is not None and top < 1:
        raise ValueError(f"top 须 ≥1 或 None（全部），收到 {top!r}")
    rows = fund_rank(fund_type=fund_type, force_refresh=force_refresh)
    horizons = {"1m": "ret_1m_pct", "3m": "ret_3m_pct", "6m": "ret_6m_pct",
                "ytd": "ret_ytd_pct", "1y": "ret_1y_pct", "2y": "ret_2y_pct",
                "3y": "ret_3y_pct"}
    # 按类别桶分组算分位（本地，零请求）
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(_bucket_of(r.get("fund_type")) or "_", []).append(r)
    import bisect
    pct_by_code: dict[str, dict] = {}
    size_by_code: dict[str, int] = {}
    for group in buckets.values():
        for h, key in horizons.items():
            vals = sorted(x.get(key) for x in group if x.get(key) is not None)
            if not vals:
                continue
            for x in group:
                v = x.get(key)
                if v is None:
                    continue
                # 不高于该值的占比（含并列）：桶内最高=100、最低≈1/n（排名分位口径）
                pct = bisect.bisect_right(vals, v) / len(vals) * 100.0
                pct_by_code.setdefault(x["code"], {})[h] = round(pct, 1)
                size_by_code[x["code"]] = len(group)
    out = []
    for r in rows:
        p = pct_by_code.get(r["code"]) or {}
        if "ytd" not in p or "1y" not in p or len(p) < 4:
            continue                       # 纳入门槛：含 ytd+1y 且 ≥4 周期
        score = sum(p.values()) / len(p)
        out.append({"code": r["code"], "name": r["name"], "fund_type": r.get("fund_type"),
                    "score_pct": round(score, 1), "horizon_pct": p,
                    "covered": len(p), "bucket_n": size_by_code.get(r["code"]),
                    "consistent": all(v >= 75.0 for v in p.values())})
    out.sort(key=lambda x: -x["score_pct"])
    return out if top is None else out[:int(top)]


_MOB_COMMON = {"deviceid": "Wap", "plat": "Wap", "product": "EFund", "version": "6.2.8"}


def fund_scale(query: str, force_refresh: bool = False) -> dict:
    """单基金最新规模（1 请求）：移动端 ENDNAV 精确值，20h 缓存。

    rankhandler/E1 都没有规模列（坑P22）——这是全市场唯一免费单基金规模源；
    ENDNAV 为元精确值（161725 锚 20946896763.35 = 209.47 亿分毫不差）。
    全市场【规模榜】无聚合源 → 榜单场景由调用方对候选集逐只富集（预算自控），
    规模重叠预警同理（两只基金 = 2 请求）。"""
    code = resolve_fund_code(query)
    rel = f"scale/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    r = ttfund_get("https://fundmobapi.eastmoney.com/FundMNewApi/FundMNBasicInformation",
                   params={**_MOB_COMMON, "FCODE": code}, timeout=15)
    assert_not_risk_control(r, "tt", f"fund_scale {code}")
    try:
        d = (r.json() or {}).get("Datas") or {}
    except ValueError as e:
        raise RuntimeError(f"fund_scale {code} 返回非JSON: {r.content[:80]!r}") from e
    endnav = d.get("ENDNAV")
    try:
        endnav_f = float(endnav)
    except (TypeError, ValueError):
        endnav_f = None
    if not endnav_f or endnav_f <= 0:
        raise RuntimeError(f"fund_scale {code} 无有效 ENDNAV（未披露或结构变更）：{endnav!r}")
    name = next((f["name"] for f in fund_list() if f["code"] == code), None)
    out = {"code": code, "name": name, "scale_yi": round(endnav_f / 1e8, 2),
           "company": d.get("JJGS"), "source": "eastmoney/fundmobapi"}
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out


def bond_plus_profile(query: str, force_refresh: bool = False) -> dict:
    """固收+画像（零新请求）：权益仓位带 + 回撤控制带 + 利率敏感度标签。

    口径：股票占净比（资产配置最新披露期）→ 偏债(<30) / 平衡(30~60) / 偏股(≥60)；
    回撤带按 fund_metrics 最大回撤绝对值：≤5 优 / ≤10 良 / ≤20 中 / >20 弱；
    债券占净比 ≥50% 附加利率敏感标注（联动 cb_yield_curve / credit_spread）。"""
    snap = fund_snapshot(query, force_refresh=force_refresh)
    aa = snap.get("asset_allocation") or []
    if not aa:
        raise RuntimeError(f"固收+画像需要资产配置（{snap.get('name')}）——Data_assetAllocation 缺失")
    latest = aa[-1]
    stock_pct = latest.get("股票占净比")
    bond_pct = latest.get("债券占净比")
    if stock_pct is None:
        raise RuntimeError(f"资产配置缺「股票占净比」（{snap.get('name')}）——上游结构变更")
    m = fund_metrics(snap)
    mdd = abs(m.get("max_drawdown_pct") or 0.0)
    grade = "偏股" if stock_pct >= 60 else ("平衡" if stock_pct >= 30 else "偏债")
    dd_band = ("优" if mdd <= 5 else "良" if mdd <= 10 else "中" if mdd <= 20 else "弱")
    rate_note = None
    if (bond_pct or 0) >= 50:
        rate_note = f"债券占净比 {bond_pct}%——利率敏感，联动 cb_yield_curve / credit_spread 评估债底"
    pos = snap.get("stock_position_history") or []
    return {"code": snap["_meta"]["code"], "name": snap.get("name"),
            "as_of": latest.get("_date"), "stock_pct": stock_pct, "bond_pct": bond_pct,
            "grade": grade, "max_drawdown_pct": m.get("max_drawdown_pct"),
            "volatility_ann_pct": m.get("annualized_volatility_pct"),
            "drawdown_band": dd_band, "rate_note": rate_note,
            "position_latest_pct": pos[-1]["position_pct"] if pos else None}


def qdii_premium(codes: list) -> list[dict]:
    """QDII/ETF 场内溢价（N 只=2 请求）：腾讯市价 ÷ 最新净值 − 1。

    ⚠️ 口径：E6 净值为 T-1/T-2（坑P16 日历差），行情剧烈时段溢价会被高估——
    结果必须连同 nav_date 一起解读。仅对场内可交易码有意义；场外码直接拒绝，
    防止 000001 等代码被股票路由到同名证券后生成假溢价。
    输入按首次出现顺序去重；返回按溢价降序，净值缺失的只标注 error 不中断。"""
    codes = list(dict.fromkeys(str(c).strip() for c in codes if str(c).strip()))
    if not codes:
        raise ValueError("codes 不能为空")
    if len(codes) > 50:
        raise ValueError(f"单次 ≤50 只（批量净值上限），收到 {len(codes)}")
    listed_prefixes = {"50", "51", "52", "56", "58", "15", "16", "18"}
    bad = [c for c in codes if not (len(c) == 6 and c.isdigit() and c[:2] in listed_prefixes)]
    if bad:
        raise ValueError(f"非场内基金码: {bad}；放行前缀为 {sorted(listed_prefixes)}，"
                         "拒绝借用同名股票行情计算假溢价")
    # V0.8.4（P36）：508/180 号段的公募 REIT 能过号段白名单，但 E6 净值对 REIT 停在
    # 发行价（180101 恒 2.31@2021）——按它算出约 -40% 的假溢价。拒绝并指路腾讯口径。
    reits = [c for c in codes if _is_reit(c)[0]]
    if reits:
        raise ValueError(f"{reits} 是公募 REIT：东财净值对 REIT 停在发行价，算出的折溢价是假的。"
                         "请用 reit_profile(code)（腾讯半年频净值，带净值日期）")
    q = tencent_quote(codes)
    e6 = {x["code"]: x for x in fund_realtime_nav(codes)}
    out = []
    for c in codes:
        tq = q.get(c) or {}
        navrow = e6.get(c) or {}
        price, nav = tq.get("price"), navrow.get("nav")
        row = {"code": c, "name": tq.get("name") or navrow.get("name"),
               "price": price, "nav": nav, "nav_date": navrow.get("nav_date")}
        if price and nav:
            row["premium_pct"] = round((float(price) / float(nav) - 1) * 100, 2)
            row["note"] = (f"净值 {navrow.get('nav_date')}（T-1/T-2 口径），"
                           "剧烈行情时段溢价可能被高估")
        else:
            row["premium_pct"] = None
            row["note"] = "市价或净值缺失（非场内码/未披露）"
        out.append(row)
    out.sort(key=lambda x: -(x["premium_pct"] if x["premium_pct"] is not None else -9e9))
    return out


def nav_divergence(query: str, force_refresh: bool = False) -> dict:
    """重仓探测：净值收益 vs 上季重仓加权收益的背离度（疑似调仓信号）。

    窗口 = 报告期当天或之前最后一个基金净值日 → 最新净值日；股票必须使用
    同一基点日期，缺共同基点则跳过，避免首个交易日被单边漏算造成虚假背离。
    重仓股走腾讯日K前复权；force_refresh 同时刷新快照、持仓和K线。
    重仓收益按实际可用覆盖度归一；coverage<40% 或最新股票仓位<80% 时仅返回
    参考数值，不给“持仓一致/疑似调仓”结论，避免把资产仓位差误判为调仓。
    预算：冷启 ≤12 请求（快照1+持仓1+K线≤10）；当日重跑 0~2。
    |背离|≤5pp 持仓一致 / ≤10pp 轻微背离（关注）/ >10pp 疑似调仓或风格漂移。
    仅支持 6 位 A 股重仓码（港股 5 位码跳过并在 skipped 标注）。
    ⚠️ 披露滞后：季报持仓天然滞后，背离≠一定调仓（也可能是持仓内涨跌分化）。"""
    snap = fund_snapshot(query, force_refresh=force_refresh)
    adj = adjusted_nav(snap["nav_history"])
    hold = fund_holdings(query, force_refresh=force_refresh)
    if not hold:
        raise RuntimeError(f"无股票持仓记录（{snap.get('name')}）——货币基金或上游空表，重仓探测不适用")
    period = max((h.get("period") or "" for h in hold), default="")
    if not period:
        raise RuntimeError(f"持仓记录缺报告期（{snap.get('name')}），拒绝猜测重仓窗口")
    rows = [h for h in hold if h.get("period") == period]
    fund_base = next((a for a in reversed(adj) if a["date"] <= period), None)
    if fund_base is None:
        raise RuntimeError(f"报告期 {period} 当天或之前无净值基点（{snap.get('name')}）")
    base_date = fund_base["date"]
    end_date = adj[-1]["date"]
    if end_date <= base_date:
        raise RuntimeError(f"报告期 {period} 之后无净值（{snap.get('name')}），无法算窗口收益")
    fund_ret = (adj[-1]["adj_nav"] / fund_base["adj_nav"] - 1) * 100
    days_span = (date.fromisoformat(end_date) - date.fromisoformat(base_date)).days
    bars = min(days_span + 20, 250)
    weighted, coverage, skipped, used = 0.0, 0.0, [], 0
    for h in rows:
        w = (h.get("nav_pct") or 0) / 100.0
        code6 = str(h.get("stock_code") or "")
        if not re.fullmatch(r"\d{6}", code6):
            skipped.append({"code": code6, "name": h.get("stock_name"), "reason": "非6位A股码"})
            continue
        k = _tencent_kline(code6, bars=bars, force_refresh=force_refresh)
        stock_base = next((x for x in k if x["date"] == base_date and x["close"] > 0), None)
        seg = [x for x in k if base_date < x["date"] <= end_date]
        if stock_base is None or not seg or seg[-1]["date"] != end_date:
            skipped.append({"code": code6, "name": h.get("stock_name"),
                            "reason": f"K线缺共同基点 {base_date} 或共同终点 {end_date}"})
            continue
        r_i = (seg[-1]["close"] / stock_base["close"] - 1) * 100
        weighted += w * r_i
        coverage += w
        used += 1
    if used == 0 or coverage <= 0:
        raise RuntimeError(f"重仓股无一可取K线或有效权重（{snap.get('name')}）——拒绝静默返回")
    implied = weighted / coverage
    div = fund_ret - implied
    positions = [x.get("position_pct") for x in (snap.get("stock_position_history") or [])
                 if x.get("position_pct") is not None]
    stock_position = positions[-1] if positions else None
    if coverage < 0.40:
        verdict = f"覆盖度不足（前十占净值 {coverage:.0%}），仅供参考"
    elif stock_position is not None and stock_position < 80:
        verdict = f"股票仓位不足（最新披露 {stock_position:.1f}%），仅供参考"
    else:
        verdict = ("持仓一致" if abs(div) <= 5
                   else "轻微背离（关注）" if abs(div) <= 10 else "疑似调仓/风格漂移")
    note = "季报披露天然滞后，背离≠一定调仓（持仓内涨跌分化同表象）；重仓收益已按覆盖度归一"
    if stock_position is None:
        note += "；股票仓位缺失，未做低仓位结论降级"
    return {"code": snap["_meta"]["code"], "name": snap.get("name"),
            "period": period, "window": {"start": base_date, "end": end_date,
                                         "days": days_span},
            "fund_return_pct": round(fund_ret, 2),
            "holdings_weighted_return_pct": round(implied, 2),
            "divergence_pp": round(div, 2), "verdict": verdict,
            "coverage_pct": round(coverage * 100, 1), "stocks_used": used,
            "stock_position_pct": stock_position,
            "skipped": skipped, "note": note}


def _manager_page(mid: str) -> dict:
    """经理主页解析（manager/{id}.html 服务端直出，坑P23 形态）：1 请求。"""
    r = ttfund_get(f"https://fund.eastmoney.com/manager/{mid}.html",
                   headers={"Referer": "https://fund.eastmoney.com/"})
    assert_rankhandler(r, f"经理主页 {mid}")
    h = r.content.decode("utf-8", errors="replace")
    if len(h) < 10000:
        raise RuntimeError(f"经理主页异常（{mid} 仅 {len(h)}B）——ID 无效或结构变更")
    flat = re.sub(r"<[^>]+>|\s+", "", h)
    mt = re.search(r"<title>([^<_\-]{2,20})", h)
    name = (mt.group(1).strip() if mt else "").strip()
    tenure = re.search(r"累计任职时间[:：]?([0-9年又天]+)", flat)
    scale = re.search(r"现任基金[^0-9]{0,6}([0-9.]+)亿", flat)
    ret = re.search(r"任期回报[:：]?([\-0-9.]+)%", flat)
    mg = re.search(r"var\s+mgrlists\s*=\s*(\[.*?\]);", h, re.S)
    funds = []
    if mg:
        funds = [{"code": c, "name": n, "type": t} for c, n, t in
                 re.findall(r"\{valueField:'(\d{6})',name:'([^']+)',type:'(\d+)'\}", mg.group(1))]
    if not name or not funds:
        raise RuntimeError(f"经理主页解析失败（{mid}）：name={name!r} 在管清单={len(funds)}——上游结构变更")
    out = {"manager_id": mid, "name": name,
           "tenure": tenure.group(1) if tenure else None,
           "scale_yi": float(scale.group(1)) if scale else None,
           "return_since_pct": float(ret.group(1)) if ret else None,
           "funds": funds, "fund_count": len(funds),
           "source": "eastmoney/manager-page"}
    _cbreak_success("tt")
    return out


def fund_manager(query: str, force_refresh: bool = False) -> dict:
    """基金经理画像（1 请求，24h 缓存）：ID 直查或基金码转接现任经理。形态：
    经理ID（8 位数字，非 80 开头）/ 基金代码或名称（快照现任经理转接——快照已
    缓存则 0 请求；经理ID 由 pingzhongdata Data_currentFundManager 直出）。
    返回 {manager_id,name,tenure 累计任职时间,scale_yi 现任基金总规模,
    return_since_pct 任期回报,funds 在管清单,fund_count}。
    经理姓名→ID 无公开结构化索引（旧名单页 404、建议接口退化为固定样本、
    排行接口仅姓名序且全量 88 页超预算；dt=15 目录页实测恒返回拼音前 10 条
    且忽略 pi/pn——不可查询，坑P23）——请用基金码转接或经理ID。
    80 开头的 8 位 ID 是公司，走 fund_company。"""
    q = str(query).strip()
    if re.fullmatch(r"\d{8}", q):
        if q.startswith("80"):
            raise ValueError(f"{q} 是 80 开头的公司ID——公司画像请用 fund_company")
        mid = q
    else:
        snap = fund_snapshot(q)
        mgr = snap.get("manager") or {}
        mid = mgr.get("manager_id")
        if not mid:
            raise RuntimeError(f"未找到「{q}」的现任经理（Data_currentFundManager 缺失）"
                               "——基金可能清算或经理空缺")
    rel = f"manager/{mid}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24)
        if cached is not None:
            return cached
    out = _manager_page(mid)
    _cache_write(rel, out)
    return out


def manager_rank(pages: int = 1, force_refresh: bool = False) -> list[dict]:
    """经理排行（坑P23：服务端仅姓名序，50 条/页，record 强校验）。

    pages×50 位（record≈4384 校验防截断）。行含在管清单/任职天数/任期回报/
    在管规模——规模与回报列是【已取页内】字段，本地排序只对已取子集有意义，
    不等于全市场按规模排行（那需要 88 页，预算不允许；诚实边界）。
    缓存 20h。"""
    pages = int(pages)
    if not 1 <= pages <= 88:
        raise ValueError(f"pages 须 1~88（record≈4384 ÷ 50），收到 {pages}")
    rel = f"manager/rank_p{pages}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    out, record = [], None
    for pi in range(1, pages + 1):
        r = ttfund_get("https://fund.eastmoney.com/Data/FundDataPortfolio_Interface.aspx",
                       params={"dt": 14, "mc": "returnjson", "pt": 2, "sc": "abbname",
                               "st": "desc", "pi": pi, "pn": 50},
                       headers={"Referer": "https://fund.eastmoney.com/"})
        assert_rankhandler(r, f"经理排行 p{pi}")
        text = r.content.decode("utf-8", errors="replace")
        if record is None:
            mr = re.search(r"record:(\d+)", text)
            if not mr:
                raise RuntimeError("经理排行响应无 record 总数——截断校验不可用")
            record = int(mr.group(1))
        # 行形态 ["a","b",...]：组内引号成对出现，需精确匹配引号组序列（坑P23）
        raw_rows = re.findall(r'\[("[^"]*"(?:,"[^"]*")*)\]', text)
        for rr in raw_rows:
            f = json.loads("[" + rr + "]")
            if len(f) < 12:
                continue
            scale_yi = None
            ms = re.search(r"([0-9.]+)亿", f[10])
            if ms:
                scale_yi = float(ms.group(1))
            out.append({"manager_id": f[0], "name": f[1], "company_id": f[2],
                        "company": f[3],
                        "fund_codes": f[4].split(",") if f[4] else [],
                        "fund_names": f[5].split(",") if f[5] else [],
                        "tenure_days": int(f[6]) if f[6].isdigit() else None,
                        "return_pct": _to_float(f[7].rstrip("%")),
                        "best_fund_code": f[8], "best_fund_name": f[9],
                        "scale_yi": scale_yi})
    expected = min(50 * pages, record)
    if len(out) != expected:
        raise RuntimeError(f"经理排行校验失败 record={record} 实收 {len(out)}，应收 {expected}")
    if len({x["manager_id"] for x in out}) != len(out):
        raise UpstreamFormatError(f"经理排行存在重复 manager_id（record={record}，实收 {len(out)}），"
                                  "疑似上游分页钳制或重复拼页，拒绝缓存")
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out
```

## 层 7 · 规模全历史（V0.6.2，F10 规模变动 gmbd 通道）

```python
# pingzhongdata 的规模史只有 ~5 期（图表数据）；gmbd 是 F10「规模变动」页同一
# 数据源的全量版（募集期→最新季报，实测 000312 53 期），多出期间申购/赎回流量
# 列——容量衰减判定与"申购驱动 vs 净值驱动"分析的原料。列序动态映射（P-C 同款
# 防线），新→旧源序反转为旧→新输出（与 snapshot.scale_history 口径一致）。


def _js_string_after(s: str, anchor: str) -> Optional[str]:
    """anchor 后第一个 JS 双引号字符串的值（逐字符扫描，转义序列成对跳过）。

    gmbd 响应的 content 后还跟着基金元数据 JSON（含大量引号），贪婪正则会越界——
    fund_holdings 的 `content:"(.*)"` 模式在 gmbd 上不可复用。
    契约：返回 JS 字面量**原文**（转义序列不解码）——消费方按正则消费 HTML 无碍。"""
    i = s.find(anchor)
    if i < 0:
        return None
    i = s.find('"', i + len(anchor))
    if i < 0:
        return None
    out, j = [], i + 1
    while j < len(s):
        ch = s[j]
        if ch == "\\" and j + 1 < len(s):              # 尊重转义（\" 等成对取用）
            out.append(s[j:j + 2])
            j += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        j += 1
    return "".join(out)


def fund_scale_history(query: str, force_refresh: bool = False) -> list[dict]:
    """基金规模全历史（F10 规模变动 gmbd 通道，1 请求，当日缓存）。

    返回 [{date, subscribe_yi 期间申购(亿份), redeem_yi 期间赎回(亿份),
    shares_yi 期末总份额(亿), nav_yi 期末净资产(亿), nav_change_pct 净资产变动率}]
    按【日期升序】（源序新→旧已反转，与 snapshot.scale_history 口径一致）；
    募集期行无申赎流量（---→None）。1 请求 ≈ N 季全史，容量衰减判定从
    "近 5 期对比"升级为全历史对比；期间申赎流量还可支撑"申购驱动 vs 净值驱动"
    归因分析。当日缓存 20h。"""
    code = resolve_fund_code(query)
    rel = f"scale_history/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    r = ttfund_get("https://fundf10.eastmoney.com/FundArchivesDatas.aspx",
                   params={"type": "gmbd", "mode": 0, "code": code},
                   headers={"Referer": f"https://fundf10.eastmoney.com/tsdata_{code}.html"})
    assert_not_risk_control(r, "tt", f"gmbd {code}")
    r.raise_for_status()
    html = _js_string_after(r.text, "content:")
    if not html:
        raise RuntimeError(f"gmbd 响应无 content（{code}）：{r.text[:120]!r}")
    table = re.search(r"<table[^>]*>.*?</table>", html, re.S)
    if not table:
        raise RuntimeError(f"gmbd 规模表为空（{code}）——上游结构变更或该基金无规模披露")
    thead = re.search(r"<thead[^>]*>(.*?)</thead>", table.group(0), re.S)
    heads = [re.sub(r"<[^>]+>|\s+|&nbsp;", "", c)
             for c in re.findall(r"<th[^>]*>(.*?)</th>", thead.group(1) if thead else "",
                                 re.S)]

    def col(keyword: str) -> Optional[int]:
        """表头含 keyword 且带单位词（亿份/亿元）才算命中——防「期间申购确认日」类
        前缀列静默错列（可选列护不住 None 防线，就护在匹配条件上）。"""
        for i, h in enumerate(heads):
            if keyword in h and ("亿份" in h or "亿元" in h):
                return i
        return None

    idx = {k: col(k) for k in
           ("期间申购", "期间赎回", "期末总份额", "期末净资产")}
    # 全名匹配：裸词「变动率」会被「份额变动率」类新列抢走（复审建议，防错列）
    idx["净资产变动率"] = next(
        (i for i, h in enumerate(heads) if "净资产变动率" in h), None)
    if not heads or idx["期末总份额"] is None or idx["期末净资产"] is None:
        raise RuntimeError(f"gmbd 必需列定位失败（{code}）：{heads}——上游结构变更，拒绝盲取")

    def num(text: str) -> Optional[float]:
        t = re.sub(r"<[^>]+>|\s+|&nbsp;", "", text).strip().replace(",", "")
        if t in ("", "---", "--", "—"):
            return None                                  # 缺失形态（募集期行）
        try:
            return float(t.rstrip("%"))
        except ValueError:
            raise RuntimeError(                          # 不可解析≠缺失，显式报错不静默丢值
                f"gmbd 数值解析失败（{code}，表头 {heads}）：{text!r}") from None

    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S):
        cells = [re.sub(r"<[^>]+>|\s+|&nbsp;", "", c)
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
        if not cells or not re.match(r"\d{4}-\d{2}-\d{2}", cells[0].strip()):
            continue                                   # 表头行/空行跳过
        def cell_at(k):
            i = idx[k]
            return num(cells[i]) if i is not None and i < len(cells) else None
        rate = cell_at("净资产变动率")
        out.append({"date": cells[0].strip(),
                    "subscribe_yi": cell_at("期间申购"),
                    "redeem_yi": cell_at("期间赎回"),
                    "shares_yi": cell_at("期末总份额"),
                    "nav_yi": cell_at("期末净资产"),
                    "nav_change_pct": rate})
    if len(out) < 2:
        raise RuntimeError(f"gmbd 规模记录不足（{code} 仅 {len(out)} 期）——新基金或结构变更")
    out.sort(key=lambda x: x["date"])                  # 源序新→旧 → 反转为升序
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out
```

## 层 7 · 费率全表（V0.7.0，F10 购买信息 jjfl 通道）

```python
# E2 快照的 fees 只有申购费原/现价两个标量；管理费/托管费/销售服务费/赎回阶梯
# （持有期越久越便宜——决定"拿多久赎回才划算"）只在 F10 jjfl 页。2026-11-12
# 005001 实测钉死：表5=运作费三率（键值行）、表6=认购费率（适用金额→费率）、
# 表7=申购费率（适用金额→原|优惠）、表8=赎回费率（适用期限→费率）。分派按
# 表头特征而非索引——上游插表/调序不静默错列（P-C 同款防线）。


def fund_fees(query: str, force_refresh: bool = False) -> dict:
    """基金费率全表（F10 购买信息 jjfl 页，1 请求，当日缓存）。

    返回 {code, name,
      management_fee_pct / custody_fee_pct / sales_service_fee_pct,  # 运作费（每年%）
      founding_tiers,   # 认购费档位 [{range, fee_text}]（募集期买入；老基金为空）
      purchase_tiers,   # 申购费档位 [{range, origin_pct, discount_pct}]（原价|天天优惠）
      redemption_tiers} # 赎回阶梯 [{holding, fee_pct}]——按持有期递减，≥730天通常免
    数值字段解析失败为 None（档位行保留原文 fee_text）；百分号剥离。
    实测 005001：管理1.20/托管0.20/销售服务0.00；申购 <200万 1.50|0.15；
    赎回 <7天1.50 → 7-30天0.75 → 30-365天0.50 → 1-2年0.25 → ≥2年0.00。"""
    code = resolve_fund_code(query)
    rel = f"fees/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    r = ttfund_get(f"https://fundf10.eastmoney.com/jjfl_{code}.html",
                   headers={"Referer": "https://fundf10.eastmoney.com/"})
    assert_not_risk_control(r, "tt", f"jjfl {code}")
    r.raise_for_status()
    h = r.content.decode("utf-8", errors="replace")
    m = re.search(r"<title>([^<\-]{2,40})", h)
    name = ((m.group(1).strip() if m else "").split("(")[0]      # 剥 "(005001)基金费率" 尾
            .split("_")[0].split("-")[0].strip()) or None

    def _cells(tr):
        return [re.sub(r"<[^>]+>|&nbsp;|\s+", "", c)
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]

    def _pct(text):
        mm = re.search(r"([0-9.]+)%", text or "")
        return float(mm.group(1)) if mm else None

    out = {"code": code, "name": name,
           "management_fee_pct": None, "custody_fee_pct": None,
           "sales_service_fee_pct": None,
           "founding_tiers": [], "purchase_tiers": [], "redemption_tiers": []}
    for tb in re.findall(r"<table[^>]*>(.*?)</table>", h, re.S):
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S)
        if not rows:
            continue
        flat = [c for tr in rows for c in _cells(tr) if c]
        # 运作费键值行：平铺序列里 标签→值 相邻
        if "管理费率" in flat and not any("适用" in c for c in flat[:2]):
            for key, field in (("管理费率", "management_fee_pct"),
                               ("托管费率", "custody_fee_pct"),
                               ("销售服务费率", "sales_service_fee_pct")):
                if key in flat:
                    i = flat.index(key)
                    if i + 1 < len(flat):
                        out[field] = _pct(flat[i + 1])
            continue
        head = _cells(rows[0])
        if not head or "适用金额" not in head[0]:
            if head and "适用期限" in head[0]:          # 赎回阶梯
                for tr in rows[1:]:
                    c = _cells(tr)
                    if len(c) >= 2 and c[0]:
                        out["redemption_tiers"].append(
                            {"holding": c[0], "fee_pct": _pct(c[1]),
                             "fee_text": c[1] or None})
            continue
        # 认购 vs 申购：表头第二列是否含 "原费率"
        is_purchase = len(head) > 1 and "原费率" in head[1]
        for tr in rows[1:]:
            c = _cells(tr)
            if len(c) < 2 or not c[0]:
                continue
            if is_purchase:
                origin = discount = None
                mm = re.match(r"([0-9.]+)%\|([0-9.]+)%", c[1] or "")
                if mm:
                    origin, discount = float(mm.group(1)), float(mm.group(2))
                out["purchase_tiers"].append(
                    {"range": c[0], "origin_pct": origin,
                     "discount_pct": discount, "fee_text": c[1] or None})  # 固定费档(每笔N元)保原文
            else:
                out["founding_tiers"].append(
                    {"range": c[0], "fee_text": c[1] or None})
    if (out["management_fee_pct"] is None and out["custody_fee_pct"] is None
            and not out["redemption_tiers"]):
        raise RuntimeError(f"jjfl 费率页解析为空（{code}）——上游结构变更，拒绝半空返回")
    _cbreak_success("tt")
    _cache_write(rel, out)
    return out
```

```python
# 2026-11-18 实测钉死：
#   · api.fund.eastmoney.com/f10/JJGG 必须带 fundf10 Referer——裸 UA 请求是
#     200+ErrCode-999+Data="" 的「假成功」形态；type=页面 tab：0 全部公告/1 发行
#     运作/2 分红送配/3 定期报告/4 人事调整/5 基金销售/6 其他公告（NEWCATEGORY
#     镜像 type），源序新→旧；行字段 ID=AN202610271830986993 形态。
#   · PDF 直链规律 https://pdf.dfcfw.com/pdf/H2_{清单ID}_1.pdf——_1 附件号不可省
#     （少了 _1 拿到的是挑战壳）。场外/上市基金全覆盖：巨潮 column=fund 仅上市
#     基金（LOF/ETF），交银趋势 519702 场外在巨潮三路全零、东财直链 636KB 实证。
#   · pdf.dfcfw.com 前有腾讯 EdgeOne 反爬：裸请求 200 + Content-Type 伪装
#     application/pdf，但 body 是 988B <script> 挑战（静态计算 __tst_status /
#     EO_Bot_Ssid 两 cookie 后重放即真 PDF）。挑战 JS 无时间无随机 → node vm
#     沙箱 eval 捕获 document.cookie 赋值（同 P20 enckey 的 node 先例；vm 上下文
#     无 require/fs/net，timeout 防死循环）。挑战按会话/IP 计费：解一次后同
#     会话后续 PDF 直连命中（实测连续 3 份仅首份触发）。

_REPORT_CATEGORIES = {          # fund_reports category 键 → JJGG type（=页面 tab）
    "all": 0, "issuing": 1, "dividend": 2, "periodic": 3,
    "staff": 4, "sales": 5, "other": 6,
}
_REPORT_KIND_RES = [            # 定期报告标题 → 报告期分类
    (re.compile(r"(\d{4})年第([1-4])季度报告"), "Q{1}"),
    (re.compile(r"(\d{4})年中期报告"), "interim"),
    (re.compile(r"(\d{4})年年度报告"), "annual"),
]


def _classify_report_title(title: str) -> dict:
    """报告标题 → {year, kind}；kind: Q1~Q4 / interim(中期) / annual(年度) / other。

    「年度报告摘要」行同样命中 annual（子串匹配）；other 是未覆盖形态
    （如更新招募提示），诚实标注不猜。"""
    for rex, tmpl in _REPORT_KIND_RES:
        m = rex.search(title or "")
        if m:
            kind = tmpl.format(m.group(1), m.group(2)) if "{1}" in tmpl else tmpl
            return {"year": int(m.group(1)), "kind": kind}
    return {"year": None, "kind": "other"}


def fund_reports(query: str, category: str = "periodic", limit: int = 20,
                 force_refresh: bool = False) -> dict:
    """基金公告/定期报告清单（f10/JJGG，1 请求，当日缓存）。

    category（=公告页 tab）: periodic(默认)=定期报告·季度/中期/年度 | all 全部公告
    | issuing 发行运作 | dividend 分红送配 | staff 人事调整 | sales 基金销售
    | other 其他公告。
    返回 {code, category, total, reports:[{code, report_id, title, short_title,
    date, year, kind, pdf_url}]}，源序【新→旧】（定期报告法定 1 年 4 期：
    Q1/Q3 季报+中期+年度，部分基金加披 Q2/Q4 季报——交银趋势实测 6 期/年；
    limit 默认 20≈近 3~5 年（越界自动钳入 1~100）；固定抓满 100 条再本地切片——缓存与 limit
    解耦，先小后大的调用序列不拿残缺缓存；更早年份看 total 判断）。kind 分类见
    _classify_report_title；pdf_url 是东财 CDN 直链（H2_{ID}_1.pdf，场外/上市
    全覆盖——巨潮仅上市基金，坑P32）——下载走 fund_report_pdf()（有 EdgeOne
    反爬挑战，勿裸 requests）。
    """
    if category not in _REPORT_CATEGORIES:
        raise ValueError(f"category 需为 {sorted(_REPORT_CATEGORIES)} 之一: {category!r}")
    limit = max(1, min(int(limit), 100))
    ctype = _REPORT_CATEGORIES[category]
    code = resolve_fund_code(query)
    rel = f"reports/{code}_c{ctype}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=SNAPSHOT_TTL_HOURS)
        if cached is not None:
            return {**cached, "reports": (cached.get("reports") or [])[:limit]}
    # 固定抓满 pageSize=100（服务端上限）再本地切片——缓存与 limit 解耦，
    # 先小 limit 后大 limit 的调用序列不会拿到残缺缓存
    r = ttfund_get("https://api.fund.eastmoney.com/f10/JJGG",
                   params={"fundcode": code, "pageIndex": 1, "pageSize": 100,
                           "type": ctype},
                   headers={"Referer": f"https://fundf10.eastmoney.com/jjgg_{code}_{ctype}.html"})
    assert_not_risk_control(r, "tt", f"jjgg {code}")
    r.raise_for_status()
    j = r.json()
    rows = j.get("Data")
    if not isinstance(rows, list):                 # ErrCode-999 假成功形态 Data=""
        raise RuntimeError(f"jjgg 响应异常（{code} Data 非 list，"
                           f"ErrCode={j.get('ErrCode')}）——缺 Referer 或上游改版")
    reports = []
    for x in rows:
        rid = str(x.get("ID") or "")
        if not rid:
            continue
        title = (x.get("TITLE") or "").strip()
        raw_date = str(x.get("PUBLISHDATE") or "")
        date = raw_date[:10] if re.match(r"\d{4}-\d{2}-\d{2}", raw_date) else None
        reports.append({"code": code, "report_id": rid, "title": title,
                        "short_title": (x.get("ShortTitle") or "").strip() or None,
                        "date": date,
                        **_classify_report_title(title),
                        "pdf_url": f"https://pdf.dfcfw.com/pdf/H2_{rid}_1.pdf"})
    if not reports and (j.get("TotalCount") or 0) > 0:
        raise RuntimeError(f"jjgg 清单解析为空（{code} total={j.get('TotalCount')}"
                           f"）——上游结构变更，拒绝空返回")
    _cbreak_success("tt")
    out = {"code": code, "category": category,
           "total": j.get("TotalCount") if isinstance(j.get("TotalCount"), int)
           else len(reports),
           "reports": reports}                    # 缓存存全量（≤100），返回按 limit 切
    _cache_write(rel, out)
    return {**out, "reports": reports[:limit]}


_EO_VM_TMPL = (
    "const vm=require('vm');"
    "const chunks=[];"
    "const ctx=vm.createContext({"
    "document:{set cookie(v){chunks.push(String(v));}},"
    "location:{href:'https://pdf.dfcfw.com/'},"
    "setTimeout:function(){}},"
    "{codeGeneration:{strings:false,wasm:false}});"   # 沙箱内禁 eval/new Function
    "vm.runInContext(process.argv[1],ctx,{timeout:1000});"
    "console.log(JSON.stringify(chunks));"
)
_EO_COOKIE_CACHE: list[str] = []      # 进程级挑战解（挑战按会话/IP 计费，解一次复用）


def _edgeone_solve(script_html: str) -> dict:
    """解 pdf.dfcfw.com 腾讯 EdgeOne 挑战 → {cookie 名: 值}（坑P32）。

    挑战 JS 是纯静态计算（无时间无随机），node vm 沙箱 eval 捕获 document.cookie
    赋值。沙箱上下文只给 document/location/setTimeout 三个桩（无 require/fs/net/
    process），runInContext timeout 防死循环，codeGeneration 双保险禁沙箱内二次
    eval/new Function；挑战体作 argv 数据位传入（`--` 分隔——以 - 开头也不会被
    node 当 CLI 选项解析）。残留面（诚实记录）：vm 非硬安全边界，桩对象可经
    constructor 链取宿主 Function 逃逸——本场景威胁模型是反爬脚本的意外副作用
    而非定向攻击，收窄到该程度即可；若上游换成需要 eval 的挑战会显式报错不静默。
    与 P20 enckey 同为 node 标准库先例，零新 Python 依赖。挑战体异样（改版）→
    显式 RuntimeError 不静默。"""
    import subprocess
    body = (script_html or "").replace("<script>", "").replace("</script>", "")
    if not body.strip():
        raise RuntimeError("EdgeOne 挑战体为空——上游改版预警")
    try:
        out = subprocess.run(["node", "-e", _EO_VM_TMPL, "--", body],
                             capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise RuntimeError("需要系统 node（EdgeOne 挑战计算，同巨潮 enckey 依赖）"
                           "——未找到 node 可执行文件") from None
    except (subprocess.TimeoutExpired, OSError) as e:
        raise RuntimeError(f"EdgeOne 挑战求解 node 进程异常: "
                           f"{type(e).__name__} {str(e)[:80]}") from None
    if out.returncode != 0:
        raise RuntimeError(f"EdgeOne 挑战求解失败（node vm）: {out.stderr[:120]}")
    pairs = {}
    try:
        for c in json.loads(out.stdout.strip().splitlines()[-1]):
            c = c.split(";")[0].strip()               # V0.8.1：剥「; path=/」等属性段
                                                      # （防上游挑战带属性时 Cookie 头掺垃圾对）
            if "=" in c:
                k, v = c.split("=", 1)
                pairs[k.strip()] = v
    except (json.JSONDecodeError, IndexError) as e:
        raise RuntimeError(f"EdgeOne 挑战输出不可解析: {out.stdout[:120]!r}") from e
    if not {"__tst_status", "EO_Bot_Ssid"} <= set(pairs):
        raise RuntimeError(f"EdgeOne 挑战 cookie 不全: {sorted(pairs)}——上游改版预警")
    return pairs


def fund_report_pdf(report, dest_dir=None, force_refresh: bool = False) -> str:
    """下载单份基金报告 PDF（1~2 请求；本地同名文件已存在则 0 请求直返路径）。

    report: fund_reports 返回的行 dict（用其 pdf_url；文件名
            {code}_{date}_{kind}_{报告ID尾6}.pdf——尾段防「年报与年报摘要」
            同日同 kind 撞名后 exists-skip 静默拿错文件）或直接 pdf_url 字符串
            （文件名取 URL 尾段）。
    dest_dir: 保存目录（默认当前工作目录），不存在自动创建。
    ⚠️ pdf.dfcfw.com 前有腾讯 EdgeOne 反爬挑战（坑P32）：裸 requests 拿到的是
    988B <script> 挑战壳（Content-Type 伪装 application/pdf）——本函数自动
    node 解挑战重放（需系统 node，同巨潮 enckey 依赖）；挑战按会话计费，
    同进程后续下载通常直连。返回本地文件路径（str）。
    """
    if isinstance(report, dict):
        url = str(report.get("pdf_url") or "")
        rid = str(report.get("report_id") or "")
        main = re.sub(r"[^0-9A-Za-z_.-]", "_",
                      f"{report.get('code') or 'fund'}_{report.get('date') or 'na'}_"
                      f"{report.get('kind') or 'ann'}")[:74]
        # 先截主部再拼 rid 尾 6 位——防撞名段永不进截断窗（复审建议）；
        # 手工构造的行请带 report_id，缺省时回退无尾段形态（撞名保护失效）
        stem = main + (f"_{rid[-6:]}" if rid else "")
    else:
        url = str(report or "")
        stem = re.sub(r"[^0-9A-Za-z_.-]", "_",
                      url.rsplit("/", 1)[-1].split("?")[0].removesuffix(".pdf")
                      or "report")[:80]
    if not url.startswith("https://pdf.dfcfw.com/"):
        raise ValueError("仅支持 pdf.dfcfw.com 报告直链（用 fund_reports 取行内 "
                         f"pdf_url）: {url[:80]!r}")
    dest = Path.cwd() if dest_dir is None else Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / (stem + ".pdf")
    if path.exists() and not force_refresh:
        with path.open("rb") as existing:
            magic = existing.read(4)
        if path.stat().st_size >= 1000 and magic == b"%PDF":
            return str(path)
    headers = {"Referer": "https://fundf10.eastmoney.com/"}
    if _EO_COOKIE_CACHE:
        headers["Cookie"] = "; ".join(_EO_COOKIE_CACHE)

    def _fetch(h):
        resp = ttfund_get(url, headers=h, timeout=60)
        assert_not_risk_control(resp, "tt", f"报告PDF {stem}")
        resp.raise_for_status()
        return resp.content

    body = _fetch(headers)
    if body[:4] != b"%PDF":
        if not body.lstrip()[:8].startswith(b"<script>"):
            raise RuntimeError(f"报告 PDF 响应非预期（{stem}，{len(body)}B）: "
                               f"{body[:80]!r}——直链失效或上游改版")
        cookies = _edgeone_solve(body.decode("utf-8", errors="replace"))
        _EO_COOKIE_CACHE[:] = [f"{k}={v}" for k, v in cookies.items()]
        body = _fetch({**headers, "Cookie": "; ".join(_EO_COOKIE_CACHE)})
        if body[:4] != b"%PDF":
            raise RuntimeError(f"EdgeOne 挑战后仍未取得 PDF（{stem}，{len(body)}B）: "
                               f"{body[:60]!r}")
    if len(body) < 1000:                           # 真 PDF 200KB+；残包/空包拒绝落盘
        raise RuntimeError(f"报告 PDF 疑似不完整（{stem} 仅 {len(body)}B），未保存")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.part")
    try:
        temporary.write_bytes(body)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    _cbreak_success("tt")
    return str(path)
```

## 层 8 · 基准指数层（V0.6.1，quant-excess 端点层收编：指数名查表 + 日线原语）

```python
# 收编自 quant-excess 端点层（实战两轮审查的代码）：指数=EM push2his 主源
# （_push2_get 线：单发即弃+连接级熔断+抖动，F6 分流）+ 腾讯 fqkline 备胎；
# 股票=复用 _tencent_kline（腾讯不封 IP）。
# 缓存键含 lmt——窗口拉大时旧缓存不得静默截断。RiskControlError 绝不降级。

# 跟踪指数名 → EM secid（市场前缀：1=沪 0=深 2=中证指数公司；名称源自 F10 tsdata 官方字段）
BENCH_SECID = {
    "沪深300": "1.000300", "沪深300指数": "1.000300",
    "中证500": "1.000905", "中证500指数": "1.000905",
    "中证800": "1.000906", "中证1000": "1.000852", "中证1000指数": "1.000852",
    "中证红利": "1.000922", "上证红利": "1.000015",
    "创业板综合": "0.399102", "创业板综": "0.399102",
    "上证50": "1.000016", "科创50": "1.000688",
    "中证A500": "1.000510",                       # 2024-09 新发宽基（实测 K线可达）
    "创业板指": "0.399006", "创业板指数": "0.399006",
    "深证100": "0.399330", "中证全指": "1.000985", "国证2000": "0.399303",
    # V0.7.5 扩表（腾讯通道实测 2026-11-17）：市场基准锚点与大综指补齐
    "上证指数": "1.000001", "上证综指": "1.000001",
    "深证成指": "0.399001", "深证综指": "0.399106", "上证180": "1.000010",
    "中证2000": "2.932000",
    # ── 东财行业板块指数 100 个（V0.7.6，clist t:2 快照 2026-11-17；secid 形态
    # 90.BKxxxx，腾讯无对应码 → EM 单源无备胎。板块指数为东财编制口径，非申万
    # 官方 801xxx——行业基金对标够用，申万口径走 a-stock-data。快照会随上游
    # 增改漂移：未命中名称时用 secid 直传形态（如 "90.BK1036"）。映射经 kline
    # name 双样本交叉验证（BK0475=银行Ⅱ/BK1036=半导体，2026-11-17）。──
    "一般零售": "90.BK0482",
    "专业服务": "90.BK1043",
    "专用设备": "90.BK0910",
    "中药Ⅱ": "90.BK1040",
    "交通运输": "90.BK1210",
    "仪器仪表": "90.BK0458",
    "传媒": "90.BK0486",
    "保险Ⅱ": "90.BK0474",
    "元件": "90.BK0459",
    "光伏设备": "90.BK1031",
    "光学光电子": "90.BK1038",
    "公用事业": "90.BK0427",
    "其他电子Ⅱ": "90.BK1223",
    "其他电源设备Ⅱ": "90.BK1034",
    "农化制品": "90.BK0731",
    "农林牧渔": "90.BK0433",
    "出版": "90.BK1218",
    "化学制品": "90.BK0538",
    "化学制药": "90.BK0465",
    "化学原料": "90.BK1019",
    "化学纤维": "90.BK0471",
    "医疗器械": "90.BK1041",
    "医疗服务": "90.BK0727",
    "医药商业": "90.BK1042",
    "医药生物": "90.BK1216",
    "半导体": "90.BK1036",
    "商贸零售": "90.BK1213",
    "国防军工": "90.BK1204",
    "基础化工": "90.BK1206",
    "塑料": "90.BK0454",
    "多元金融": "90.BK0738",
    "家居用品": "90.BK0440",
    "家用电器": "90.BK0456",
    "小金属": "90.BK1027",
    "工程咨询服务Ⅱ": "90.BK0726",
    "工程机械": "90.BK0739",
    "广告营销": "90.BK1220",
    "建筑材料": "90.BK1208",
    "建筑装饰": "90.BK1209",
    "影视院线": "90.BK1222",
    "房地产": "90.BK1202",
    "房地产开发": "90.BK0451",
    "房地产服务": "90.BK1045",
    "教育": "90.BK0740",
    "数字媒体": "90.BK1221",
    "有色金属": "90.BK0478",
    "服装家纺": "90.BK1225",
    "机械设备": "90.BK1205",
    "橡胶": "90.BK1018",
    "水泥": "90.BK0424",
    "汽车": "90.BK1211",
    "汽车服务": "90.BK1016",
    "汽车零部件": "90.BK0481",
    "消费电子": "90.BK1037",
    "游戏Ⅱ": "90.BK1046",
    "煤炭": "90.BK0437",
    "燃气Ⅱ": "90.BK1028",
    "物流": "90.BK0422",
    "环保": "90.BK0728",
    "玻璃玻纤": "90.BK0546",
    "生物制品": "90.BK1044",
    "电力": "90.BK0428",
    "电力设备": "90.BK1200",
    "电子": "90.BK1201",
    "电子化学品Ⅱ": "90.BK1039",
    "电机Ⅱ": "90.BK1030",
    "电池": "90.BK1033",
    "电网设备": "90.BK0457",
    "电视广播Ⅱ": "90.BK1219",
    "石油石化": "90.BK0464",
    "社会服务": "90.BK1214",
    "纺织制造": "90.BK1224",
    "纺织服饰": "90.BK0436",
    "综合": "90.BK1217",
    "综合Ⅱ": "90.BK0539",
    "美容护理": "90.BK1035",
    "能源金属": "90.BK1015",
    "航空机场": "90.BK0420",
    "航运港口": "90.BK0450",
    "装修建材": "90.BK0476",
    "装修装饰Ⅱ": "90.BK0725",
    "计算机": "90.BK1207",
    "计算机设备": "90.BK0735",
    "证券Ⅱ": "90.BK0473",
    "贵金属": "90.BK0732",
    "贸易Ⅱ": "90.BK0484",
    "软件开发": "90.BK0737",
    "轻工制造": "90.BK1212",
    "通信": "90.BK1215",
    "通信服务": "90.BK0736",
    "通信设备": "90.BK0448",
    "通用设备": "90.BK0545",
    "钢铁": "90.BK0479",
    "铁路公路": "90.BK0421",
    "银行Ⅱ": "90.BK0475",
    "非金属材料Ⅱ": "90.BK1020",
    "非银金融": "90.BK1203",
    "风电设备": "90.BK1032",
    "食品饮料": "90.BK0438",
    "饰品": "90.BK0734",
}
# 沪市指数码集合（_tencent_prefix 路由用，V0.7.1）：从 BENCH_SECID 市场 1 值域
# 自动派生（单一事实源——此前手工维护漏了 000922/000906 等，裸 6 位中证沪指码
# 曾被误路由 sz000922 深股=静默错数据，sh000016 同族）。V0.7.5 上证180 入表后
# 000010 已随派生覆盖，原显式保留移除。
_SH_INDEX_Q = {v.split(".")[1] for v in BENCH_SECID.values() if v.startswith("1.")}
_SECID_RE = re.compile(r"([012]\.\d{6}|90\.BK\d{4})")   # V0.7.6 放行东财板块指数 90.BKxxxx
_TENCENT_KLINE_CAP = 800                       # 腾讯 fqkline 实测：≤800 全量返回（param=800 实返
                                               # 801 根，服务端+1 无害）；1000~2000 静默截断~641；
                                               # ≥2100 param error（sh600519 扫描，P25）
_TENCENT_PAGE_CAP = 10                         # 翻页安全阀：10 页 × 800 = 8000 根 ≈ 32 年（V0.7.6）


def _kline_resolve(query: str) -> tuple:
    """kline_daily 入参归一 → ("index", secid) | ("stock", 代码或带前缀符号)。纯函数离线可测。

    前缀形态保留市场信息（审查实测：sh000016 若剥前缀会被按深市重推导成深康佳A——
    沪指 000xxx 与深股 000xxx 大面积碰撞，静默错数据）；sh/sz 前缀 + 码值命中
    BENCH_SECID 值域时路由回 index 走 EM 主源；399xxx 深指号段无股票冲突直路由指数。"""
    q = str(query).strip()
    if q in BENCH_SECID:
        return "index", BENCH_SECID[q]
    if _SECID_RE.fullmatch(q):
        return "index", q
    if re.fullmatch(r"399\d{3}", q):
        return "index", "0." + q
    if re.fullmatch(r"\d{6}", q):
        return "stock", q
    m = re.fullmatch(r"(sh|sz|bj)(\d{6})", q.lower())
    if m:
        pref, code = m.group(1), m.group(2)
        secid = ("1." if pref == "sh" else "0.") + code
        if secid in set(BENCH_SECID.values()):
            return "index", secid
        return "stock", f"{pref}{code}"
    _names = sorted(set(k for k in BENCH_SECID if "指数" not in k))
    supported = ("、".join(_names[:20]) + f" 等 {len(_names)} 个指数/板块名（完整清单见 BENCH_SECID）"
                 if len(_names) > 20 else "、".join(_names))
    raise ValueError(
        f"kline_daily 不识别 {query!r}——支持：指数名（{supported}…）、secid（如 1.000300）、"
        f"6位股票代码或 sh/sz/bj 前缀形式")


def _secid_tencent_sym(secid: str) -> Optional[str]:
    """EM secid → 腾讯指数符号；市场 2（中证指数公司发布）无腾讯代码 → None 不降级。"""
    mkt, code = secid.split(".")
    if mkt == "1":
        return f"sh{code}"
    if mkt == "0":
        return f"sz{code}"
    return None


def kline_daily(query: str, lmt: int = 2600, use_tencent_fallback: bool = True,
                force_refresh: bool = False) -> list[dict]:
    """指数/股票日线收盘序列 [{date, close, source?}] 升序（基准指数层原语，坑P25）。

    query 三形态：指数名（BENCH_SECID，如 "沪深300"/"中证红利"/"国证2000"）/
    EM secid（如 "1.000300"）/ 6 位股票代码（复用 _tencent_kline 腾讯通道）。
    指数 = EM push2his 主源（_push2_get 线 + F6 分流）+ 腾讯备胎（市场2无腾讯代码
    不降级；V0.7.6 备胎升级为**翻页全史缝合**——原单页 800 上限只能兜 ~3 年短窗，
    现按页末锚串行翻页拼 20+ 年全史，每缝日期+收盘双校验，P25/P28）；
    指数路径行内带 **source** 字段（"push2his"|"tencent"——诚实标注
    当前来自哪条通道，主源健康但历史短的指数如中证A500 不能按长度推断来源）；
    股票 = 腾讯 fqkline（不封 IP，lmt>800 显式报错引导指数形态，单一来源不带
    source）。20h 缓存、键含 lmt（≤20h 旧缓存行可能无 source 键，消费方用 .get）；
    RiskControlError 不降级。
    基金诊断用途：基准对照/风格温度/压力测试；个股深度分析仍归 a-stock-data。
    """
    lmt = int(lmt)
    if lmt < 10:
        raise ValueError(f"lmt 至少 10 根，收到 {lmt}")
    kind, ident = _kline_resolve(query)
    if kind == "stock":
        if lmt > _TENCENT_KLINE_CAP:
            raise ValueError(
                f"股票日K腾讯通道实测上限 {_TENCENT_KLINE_CAP} 根（更大会静默截断或 param error，"
                f"P25）——更长窗口请用指数名或 secid 形态（EM 主源支持更大 lmt），收到 {lmt}")
        try:
            rows = _tencent_kline(ident, bars=lmt, force_refresh=force_refresh)
        except (RiskControlError, CircuitOpenError):
            raise
        except RuntimeError as e:
            hints = [c for c in ("0." + ident[-6:], "1." + ident[-6:])
                     if c in BENCH_SECID.values()]
            msg = str(e) + (f"——若 {ident[-6:]} 是指数，请用指数名或 secid 形态（如 {hints[0]}）"
                            if hints else "")
            raise RuntimeError(msg) from e
        if len(rows) < min(lmt, 60):
            raise RuntimeError(f"kline_daily 股票日K不足（{ident} 需≥{min(lmt, 60)} 实收 {len(rows)}）")
        return rows
    # 指数路径
    fallback_key = "" if use_tencent_fallback else "_nofb"
    rel = f"kline/idx_{ident.replace('.', '_')}_lmt{lmt}{fallback_key}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            return cached
    need = min(lmt, 100)
    rows: list = []
    em_rows = 0
    fb_note = "未启用"
    try:
        ks = ((_push2_get("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                               {"secid": ident, "klt": 101, "fqt": 1, "lmt": lmt,
                                "end": "20500101", "fields1": "f1,f2,f3",
                                "fields2": "f51,f53"}, f"kline {ident}")
               .get("data") or {}).get("klines")) or []
        for line in ks:
            p = line.split(",")
            if len(p) >= 2 and re.match(r"\d{4}-\d{2}-\d{2}", p[0]):
                try:
                    rows.append({"date": p[0], "close": float(p[1]), "source": "push2his"})
                except ValueError:
                    continue
    except (RiskControlError, CircuitOpenError):
        raise                                          # 风险型信号≠连接故障，绝不降级（F6 契约）
    except RuntimeError:
        rows = []                                      # _push2_get 网络类故障才走备胎（P26）
    em_rows = len(rows)
    if em_rows < need and use_tencent_fallback:
        sym = _secid_tencent_sym(ident)
        if sym is None:
            fb_note = "备胎不可用（该指数无腾讯代码：市场2中证公司发布/东财板块指数）"
        else:
            fb_note = "备胎失败"
            fb_failed = True                    # V0.8.1 复审：布尔承载成败分支，文案改动不失效
            kept = rows
            rows = []                                  # 重置防两源日期/复权基线混拼
            try:
                # V0.7.6：翻页全史缝合（原 800 根短窗钳制——熔断期只能兜 ~3 年）
                paged = _tencent_kline_paginated(sym, lmt)
                rows = [{"date": r["date"], "close": r["close"],
                         "source": "tencent"} for r in paged]
            except (RiskControlError, CircuitOpenError):
                raise          # 风险型信号绝不吞（V0.7.6 审查加固）——进备胎时 EM 残行
                               # 必不足 need（否则不进备胎），回用 kept 必然最终报错，
                               # re-raise 无损且错误归因更准
            except Exception as e:
                fb_note = f"备胎失败（{str(e)[:40]}）"
            else:
                fb_failed = False
            if len(rows) < need and not fb_failed:
                fb_note = f"备胎短史（实收 {len(rows)} 根）"   # V0.8.1：成功但不足
                                                              # 如实归因，不再误称「失败」
            if len(rows) < need:                       # 备胎也不够 → 回用 EM 有效行（诚实留痕）
                rows = kept
    rows.sort(key=lambda x: x["date"])
    seen, dedup = set(), []
    for r in rows:                                     # 按日期去重（下游收益率序列重复即拒算）
        if r["date"] not in seen:
            seen.add(r["date"])
            dedup.append(r)
    if len(dedup) < need:                              # 去重后过线（审查建议：防重复行虚过）
        raise RuntimeError(
            f"指数日线不足（{ident} 需≥{need}）：EM 实收 {em_rows} 行，{fb_note}，"
            f"去重后 {len(dedup)} 行")
    if em_rows >= need:
        _cbreak_success("em")                         # 腾讯备胎成功不清零东财风控连击
    _cache_write(rel, dedup)
    return dedup
```

## 层 9 · 中证官方全收益指数层（V0.8.0：清单 + 官方映射 + 验证历史 + 同步）

```python

CSINDEX_MIN_INTERVAL = 1.0
CSINDEX_BASE = "https://www.csindex.com.cn/csindex-home"
CSINDEX_SESSION = requests.Session()
CSINDEX_SESSION.trust_env = False
CSINDEX_SESSION.headers.update({"User-Agent": UA})
CSINDEX_REQUEST_COUNT = [0]
_CSINDEX_CODE_RE = re.compile(r"[A-Z0-9]{6,20}")
_CSINDEX_ALL_SERIES = ["1", "2", "3", "7", "4", "5", "6"]
_CSINDEX_CURRENCY_MARKERS = (
    "HKD", "USD", "EUR", "AUD", "TWD", "CHF", "SGD", "GBP", "CAD",
    "港元", "美元", "欧元", "澳大利亚元", "台元", "瑞士法郎", "新加坡元", "英镑", "加元",
)


def _csindex_code(value) -> str:
    code = str(value or "").strip().upper()
    if not _CSINDEX_CODE_RE.fullmatch(code):
        raise ValueError(f"中证指数代码须为 6~20 位字母数字，收到 {value!r}")
    return code


def _csindex_hit(url: str):
    CSINDEX_REQUEST_COUNT[0] += 1


def _csindex_request(path: str, params: Optional[dict] = None,
                     payload: Optional[dict] = None, timeout: int = 30):
    """中证官网统一请求：独立配额/熔断/账本；HTTP 200 不代表业务成功。"""
    url = CSINDEX_BASE + path
    headers = {"Referer": "https://www.csindex.com.cn/"}
    if _REQUEST_GATEWAY is not None:
        session = _SessionAdapter(CSINDEX_SESSION, _csindex_hit)
        if payload is None:
            resp = _REQUEST_GATEWAY.get(url, params=params, headers=headers,
                                        timeout=timeout, retries=1, retry_statuses=(),
                                        session=session)
        else:
            resp = _REQUEST_GATEWAY.post(url, json=payload, headers=headers,
                                         timeout=timeout, retries=1, retry_statuses=(),
                                         session=session)
        _release_pending(resp, "csindex-passthrough")
    else:
        raise RuntimeError(
            "中证请求禁止 legacy/teardown 直连：跨进程滑窗、单并发和共享风控必须保持激活；"
            "请恢复 _reset_request_coordination() 后重试")
    # 纵深防御双保险（gateway 已在返回前对 403/429/风控正文抛 RiskControlError，
    # 见 fund_data_core/gateway.py 的 _send）——保留以覆盖网关实现回归，非主防线
    assert_not_risk_control(resp, "csindex", path)
    resp.raise_for_status()
    text = resp.content.decode("utf-8", errors="replace")
    _assert_not_risk_content(text, "csindex", path)
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise UpstreamRequestError(f"中证响应非 JSON（{path}）: {text[:100]!r}") from e
    if not isinstance(body, dict) or str(body.get("code")) != "200":
        raise UpstreamRequestError(
            f"中证业务失败（{path}，code={body.get('code') if isinstance(body, dict) else None}，"
            f"msg={body.get('msg') if isinstance(body, dict) else None}）")
    if body.get("success") is False:
        raise UpstreamRequestError(f"中证业务 success=false（{path}，msg={body.get('msg')}）")
    return body


def _csindex_universe_payload(page_num: int, page_size: int = 1000) -> dict:
    return {
        "sorter": {"sortField": "null", "sortOrder": None},
        "pager": {"pageNum": page_num, "pageSize": page_size},
        "indexFilter": {
            "ifCustomized": None, "ifTracked": None, "ifWeightCapped": None,
            "indexCompliance": None, "hotSpot": None, "indexClassify": None,
            "currency": None, "region": None, "indexSeries": _CSINDEX_ALL_SERIES,
            "undefined": None,
        },
    }


def _csindex_universe_row(row: dict) -> dict:
    code = _csindex_code(row.get("indexCode"))
    name = str(row.get("indexName") or "").strip()
    if not name:
        raise UpstreamFormatError(f"中证指数清单缺名称（{code}）")
    return {
        "index_code": code, "index_name": name,
        "index_name_en": row.get("indexNameEn"),
        "index_series": row.get("indexSeries"),
        "index_series_en": row.get("indexSeriesEn"),
        "asset_class": row.get("assetsClassify"),
        "asset_class_en": row.get("assetsClassifyEn"),
        "hot_spot": row.get("hotSpot"), "region": row.get("region"),
        "currency": row.get("currency"), "if_tracked": row.get("ifTracked"),
        "index_classify": row.get("indexClassify"),
        "if_customized": row.get("ifCustomized"),
        "if_weight_capped": row.get("ifWeightCapped"),
        "cons_number": row.get("consNumber"), "latest_close": row.get("latestClose"),
        "monthly_return": row.get("monthlyReturn"),
        "publish_date": row.get("publishDate"),
    }


def csindex_universe(index_series=None, asset_class=None, currency="人民币",
                     force_refresh: bool = False) -> list[dict]:
    """中证官网全部七个系列基础指数清单；筛选本地执行，完整性强校验。"""
    rel = "csindex/universe.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24)
        if cached is not None:
            rows = cached
        else:
            rows = None
    else:
        rows = None
    if rows is None:
        first = _csindex_request("/index-list/query-index-item",
                                 payload=_csindex_universe_payload(1))
        total, page_count = first.get("total"), first.get("size")
        first_rows = first.get("data")
        if (not isinstance(total, int) or total <= 0 or not isinstance(page_count, int)
                or page_count < 1 or not isinstance(first_rows, list)):
            raise UpstreamFormatError("中证指数清单响应缺 total/size/data 或数值非法")
        raw = list(first_rows)
        for page in range(2, page_count + 1):
            body = _csindex_request(
                "/index-list/query-index-item",
                payload=_csindex_universe_payload(page))
            if body.get("total") != total or body.get("size") != page_count:
                raise UpstreamFormatError("中证指数清单分页期间 total/size 发生变化")
            data = body.get("data")
            if not isinstance(data, list):
                raise UpstreamFormatError(f"中证指数清单第 {page} 页 data 非 list")
            raw.extend(data)
        if len(raw) != total:
            raise UpstreamFormatError(f"中证指数清单不完整：total={total}，实收={len(raw)}")
        rows = [_csindex_universe_row(x) for x in raw]
        codes = [x["index_code"] for x in rows]
        if len(set(codes)) != len(codes):
            raise UpstreamFormatError("中证指数清单存在重复 indexCode")
        _cbreak_success("csindex")
        _cache_write(rel, rows)

    def _values(value):
        if value is None:
            return None
        return {str(x) for x in (value if isinstance(value, (list, tuple, set)) else [value])}

    series, assets = _values(index_series), _values(asset_class)
    currencies = _values(currency)
    return [x for x in rows
            if (series is None or x["index_series"] in series
                or x["index_series_en"] in series)
            and (assets is None or x["asset_class"] in assets
                 or x["asset_class_en"] in assets)
            and (currencies is None or x["currency"] in currencies)]


def _csindex_basic_info(index_code, force_refresh: bool = False) -> dict:
    code = _csindex_code(index_code)
    rel = f"csindex/basic/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24 * 30)
        if cached is not None:
            return cached
    body = _csindex_request(f"/indexInfo/index-basic-info/{code}")
    data = body.get("data")
    if not isinstance(data, dict) or data.get("indexCode") != code:
        raise UpstreamFormatError(f"中证基本资料身份不符（请求 {code}）")
    _cbreak_success("csindex")
    _cache_write(rel, data)
    return data


def _csindex_names_indicate_total(cn: str, en: str) -> bool:
    cn_text, en_text = str(cn or ""), str(en or "")
    upper = en_text.upper().replace(" ", "")
    if ("净收益" in cn_text or "无全收益" in cn_text or "(净)" in cn_text
            or "（净）" in cn_text
            or "NTRI" in upper or "NETTOTALRETURN" in upper):
        return False
    return ("全收益" in cn_text or "TOTALRETURN" in upper
            or bool(re.search(r"(?:^|\W)TRI$", en_text.upper())))


def _csindex_derivative_candidates(rows: list) -> list:
    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            code = _csindex_code(row.get("indexCode"))
        except ValueError:
            continue
        cn, en = str(row.get("indexNameCn") or ""), str(row.get("indexNameEn") or "")
        upper = en.upper().replace(" ", "")
        is_total = ("全收益" in cn or upper.endswith("TRI")
                    or "TOTALRETURN" in upper)
        is_net = "净收益" in cn or "(净)" in cn or "（净）" in cn or "NTRI" in upper
        is_foreign = any(marker in code[6:] or marker in cn or marker in en.upper()
                         for marker in _CSINDEX_CURRENCY_MARKERS)
        if is_total and not is_net and not is_foreign:
            candidates.append({"index_code": code, "index_name_cn": cn, "index_name_en": en})
    return candidates


def _csindex_mapping_from_info(base_code: str, info: dict, status: str) -> dict:
    currency = "CNY" if info.get("currencyCn") == "人民币" else info.get("currencyCn")
    return {
        "base_index_code": base_code,
        "total_return_code": info["indexCode"],
        "index_name": info.get("indexFullNameCn") or info.get("indexShortNameCn"),
        "index_name_en": info.get("indexFullNameEn") or info.get("indexShortNameEn"),
        "currency": currency, "return_type": "total_return", "status": status,
        "basic_date": info.get("basicDate"), "basic_index": info.get("basicIndex"),
        "source": "csindex/basic-info" if status == "already_total"
                  else "csindex/derivative+basic-info",
    }


def csindex_resolve_total_return(index_code, force_refresh: bool = False) -> dict:
    """基础/全收益代码 → 官方映射；不猜代码，歧义与未知口径显式返回。"""
    code = _csindex_code(index_code)
    rel = f"csindex/mappings/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24 * 30)
        if cached is not None and not (cached.get("status") == "no_total_return"
                                       and cached.get("return_type") == "total_return"):
            return cached
        # V0.8.0 落盘的旧错值（no_total_return 却标 total_return）视为 miss
        # 重取自愈——mapping 缓存 30 天，版本 bump 不清洗本目录
    info = _csindex_basic_info(code, force_refresh=force_refresh)
    if _csindex_names_indicate_total(info.get("indexFullNameCn"), info.get("indexFullNameEn")):
        if info.get("currencyCn") != "人民币":
            raise UpstreamFormatError(f"全收益指数 {code} 非人民币口径：{info.get('currencyCn')}")
        out = _csindex_mapping_from_info(code, info, "already_total")
        _cache_write(rel, out)
        return out
    body = _csindex_request("/perf/get-derivative-index", params={"indexCode": code})
    data = body.get("data")
    if not isinstance(data, list):
        raise UpstreamFormatError(f"中证衍生指数响应 data 非 list（{code}）")
    candidates = _csindex_derivative_candidates(data)
    if len(candidates) > 1:
        out = {"base_index_code": code, "total_return_code": None,
               "return_type": "unknown", "status": "ambiguous",
               "candidates": candidates, "source": "csindex/derivative"}
    elif not candidates:
        status = "no_total_return" if info.get("indexType") == "stock" else "return_type_unknown"
        # V0.8.1 修：无 TR 衍生的股票指数本身是价格指数——return_type 如实标
        # price_return（V0.8.0 误标 total_return，消费方按该字段分流会拿错口径）
        out = {"base_index_code": code, "total_return_code": None,
               "return_type": "price_return" if status == "no_total_return" else "unknown",
               "status": status, "source": "csindex/derivative+basic-info"}
    else:
        candidate = candidates[0]
        tr_info = _csindex_basic_info(candidate["index_code"], force_refresh=force_refresh)
        if not _csindex_names_indicate_total(
                tr_info.get("indexFullNameCn"), tr_info.get("indexFullNameEn")):
            raise UpstreamFormatError(
                f"衍生候选 {candidate['index_code']} 的基本资料未确认全收益口径")
        if tr_info.get("currencyCn") != "人民币":
            raise UpstreamFormatError(
                f"衍生候选 {candidate['index_code']} 非人民币口径：{tr_info.get('currencyCn')}")
        out = _csindex_mapping_from_info(code, tr_info, "mapped")
    _cbreak_success("csindex")
    _cache_write(rel, out)
    return out


def _csindex_date(value, field: str = "date") -> str:
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y%m%d" if re.fullmatch(r"\d{8}", text) else "%Y-%m-%d")
    except ValueError as e:
        raise ValueError(f"{field} 须为 YYYYMMDD 或 YYYY-MM-DD，收到 {value!r}") from e
    return parsed.date().isoformat()


def _csindex_trade_dates(index_code: str, force_refresh: bool = False) -> list[str]:
    code = _csindex_code(index_code)
    rel = f"csindex/trade_dates/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=20)
        if cached is not None:
            try:
                cached_dates = [_csindex_date(x, "tradeDate") for x in cached]
            except (TypeError, ValueError):
                cached_dates = []
            if cached_dates and cached_dates == sorted(cached_dates) \
                    and len(set(cached_dates)) == len(cached_dates):
                return cached_dates
    body = _csindex_request("/perf/index-perf-tradeDate", params={"indexCode": code})
    data = body.get("data")
    if not isinstance(data, list):
        raise UpstreamFormatError(f"中证有效日期 data 非 list（{code}）")
    dates = [_csindex_date(x, "tradeDate") for x in data]
    if not dates:
        raise UpstreamFormatError(f"中证有效日期空返回（{code}），拒绝把行情全部过滤为空")
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise UpstreamFormatError(f"中证有效日期非升序或存在重复（{code}）")
    _cbreak_success("csindex")
    _cache_write(rel, dates)
    return dates


def _csindex_float(row: dict, field: str):
    value = row.get(field)
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise UpstreamFormatError(f"中证行情字段 {field} 非数值：{value!r}") from e
    if not math.isfinite(number):
        raise UpstreamFormatError(f"中证行情字段 {field} 非有限数值")
    return number


def _csindex_perf_rows(raw: list, mapping: dict, valid_dates: set,
                       start: str, end: str) -> tuple[list, list]:
    code = mapping["total_return_code"]
    rows, numeric_warnings = [], []
    seen = set()
    previous = None
    for item in raw:
        if not isinstance(item, dict) or item.get("indexCode") != code:
            raise UpstreamFormatError(f"中证行情身份或行结构异常（期望 {code}）")
        if not _csindex_names_indicate_total(
                item.get("indexNameCnAll"), item.get("indexNameEnAll")):
            raise UpstreamFormatError(f"中证行情未确认全收益口径（{code}）")
        day = _csindex_date(item.get("tradeDate"), "tradeDate")
        if day in seen:
            raise UpstreamFormatError(f"中证行情存在重复日期（{code} {day}）")
        seen.add(day)
        if day not in valid_dates:
            continue
        if not start <= day <= end:
            continue
        close = _csindex_float(item, "close")
        if close is None or close <= 0:
            raise UpstreamFormatError(f"中证行情收盘价非法（{code} {day}: {close}）")
        row = {
            "date": day, "close": close, "index_code": code,
            "base_index_code": mapping["base_index_code"],
            "index_name": item.get("indexNameCnAll") or mapping.get("index_name"),
            "return_type": "total_return", "currency": mapping.get("currency") or "CNY",
            "source": "csindex", "change": _csindex_float(item, "change"),
            "change_pct": _csindex_float(item, "changePct"),
            "trading_vol": _csindex_float(item, "tradingVol"),
            "trading_value": _csindex_float(item, "tradingValue"),
            "cons_number": _csindex_float(item, "consNumber"),
            "pe_ttm": _csindex_float(item, "peg"),
        }
        for field in ("trading_vol", "trading_value", "cons_number"):
            if row[field] is not None and row[field] < 0:
                raise UpstreamFormatError(f"中证行情 {field} 为负（{code} {day}）")
        if previous is not None:
            change = row["close"] - previous["close"]
            returns = (row["close"] / previous["close"] - 1) * 100
            if row["change"] is not None and abs(change - row["change"]) > 0.06:
                numeric_warnings.append(f"{day}:change")
            if row["change_pct"] is not None and abs(returns - row["change_pct"]) > 0.06:
                numeric_warnings.append(f"{day}:changePct")
        rows.append(row)
        previous = row
    dates = [x["date"] for x in rows]
    if dates != sorted(dates):
        raise UpstreamFormatError(f"中证行情日期非升序（{code}）")
    return rows, numeric_warnings


def _csindex_sequence_warnings(rows: list) -> list:
    """对最终拼接序列复核，覆盖分年边界和增量缝合点。"""
    warnings = []
    for previous, row in zip(rows, rows[1:]):
        change = row["close"] - previous["close"]
        returns = (row["close"] / previous["close"] - 1) * 100
        if row.get("change") is not None and abs(change - row["change"]) > 0.06:
            warnings.append(f"{row['date']}:change")
        if row.get("change_pct") is not None and abs(returns - row["change_pct"]) > 0.06:
            warnings.append(f"{row['date']}:changePct")
    return warnings


def _csindex_cache_valid(rows, code: str) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    dates = []
    for row in rows:
        if not isinstance(row, dict) or row.get("index_code") != code:
            return False
        close = row.get("close")
        if isinstance(close, bool) or not isinstance(close, (int, float)) \
                or not math.isfinite(close) or close <= 0:
            return False
        day = row.get("date")
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return False
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return False
        dates.append(day)
    return dates == sorted(dates) and len(set(dates)) == len(dates)


def _csindex_download_history(mapping: dict, start: str, end: str,
                              force_refresh: bool = False, seed_rows=None,
                              coverage_start=None) -> tuple[list, dict]:
    code = mapping["total_return_code"]
    coverage_start = coverage_start or start
    today = datetime.now(_BJ_TZ).date().isoformat()
    trade_dates = _csindex_trade_dates(code, force_refresh=force_refresh)
    if end == today and datetime.now(_BJ_TZ).weekday() < 5 and trade_dates[-1] < today \
            and not force_refresh:
        trade_dates = _csindex_trade_dates(code, force_refresh=True)
    trade_dates_stale = (end == today and datetime.now(_BJ_TZ).weekday() < 5
                         and trade_dates[-1] < today)
    valid = set(trade_dates)
    expected = sorted(day for day in valid if coverage_start <= day <= end)

    def _missing_of(dates):
        # V0.8.1：fetch 边界日（=当天）行情盘后延迟发布——tradeDate 清单先行含
        # 今天而 perf 未回时不计缺失（盘前/盘中同步不炸，V0.8.0 会整史重取后仍
        # 报 UpstreamFormatError）；行内若已带回则自然入库，完整性断言只对已发布
        # 日执行，pending_today 如实入报告。豁免仅在 end=当天时生效（复审补充：
        # 防未来直呼本私有函数传历史 end 时静默豁免真实缺行）
        miss = [day for day in expected if day not in dates]
        pending = (end if (end == datetime.now(_BJ_TZ).date().isoformat()
                           and end in valid and end not in dates) else None)
        return [d for d in miss if d != pending], pending

    def _fetch(s, e):
        body = _csindex_request("/perf/index-perf", params={
            "indexCode": code, "startDate": s.replace("-", ""),
            "endDate": e.replace("-", "")}, timeout=60)
        data = body.get("data")
        if not isinstance(data, list):
            raise UpstreamFormatError(f"中证行情 data 非 list（{code}）")
        return _csindex_perf_rows(data, mapping, valid, s, e)

    rows, warnings = _fetch(start, end)
    if seed_rows:
        rows = [x for x in seed_rows
                if coverage_start <= x["date"] < start and x["date"] in valid] + rows
    dates = {x["date"] for x in rows}
    missing, pending_today = _missing_of(dates)
    chunked = False
    if missing:
        chunked = True
        rows, warnings = [], []
        for year in range(int(coverage_start[:4]), int(end[:4]) + 1):
            s = max(coverage_start, f"{year}-01-01")
            e = min(end, f"{year}-12-31")
            part, part_warnings = _fetch(s, e)
            rows.extend(part)
            warnings.extend(part_warnings)
        dates = {x["date"] for x in rows}
        missing, pending_today = _missing_of(dates)
        if missing:
            raise UpstreamFormatError(
                f"中证行情按年重取后仍缺 {len(missing)} 个有效日期（{code}，"
                f"示例 {missing[:5]}）")
    if len(rows) != len(dates):
        raise UpstreamFormatError(f"中证行情拼接后存在重复日期（{code}）")
    extra = sorted(dates - set(expected))
    if extra:
        raise UpstreamFormatError(
            f"中证行情含非官方有效日期（{code}，{len(extra)} 处，示例 {extra[:5]}）")
    warnings = sorted(set(warnings + _csindex_sequence_warnings(rows)))
    fatal_warnings = [x for x in warnings if x.endswith(":changePct")]
    if fatal_warnings:
        raise UpstreamFormatError(
            f"中证行情收盘与涨跌幅矛盾（{code}，{len(fatal_warnings)} 处，"
            f"示例 {fatal_warnings[:5]}）；全部数值警告 {warnings[:10]}")
    _cbreak_success("csindex")
    report = {"index_code": code, "expected_dates": len(expected),
              "actual_dates": len(rows), "missing_dates": missing,
              "pending_today": pending_today,
              "numeric_warnings": warnings, "chunked": chunked,
              "incremental": bool(seed_rows), "coverage_start": coverage_start,
              "trade_dates_stale": trade_dates_stale,
              "first_date": rows[0]["date"] if rows else None,
              "last_date": rows[-1]["date"] if rows else None}
    return rows, report


def _csindex_rebase_base_code(rows: list, base_code: str) -> list:
    """日线缓存按 TR 代码共享——同一全收益序列经不同基础代码查询（如 000300 与
    H00300）共用一份文件；出库与落盘前按本次调用方视角重写 base_index_code
    （V0.8.1 修串台：行情数值同源无误，仅该元数据字段曾随首查基础码漂移）。"""
    for x in rows:
        x["base_index_code"] = base_code
    return rows


def csindex_total_return_daily(index_code, start_date="", end_date="",
                               force_refresh: bool = False) -> list[dict]:
    """官方人民币全收益指数日线；官方日期过滤、完整性和数值校验通过才缓存。"""
    mapping = csindex_resolve_total_return(index_code, force_refresh=force_refresh)
    if mapping.get("status") not in ("mapped", "already_total"):
        raise ValueError(f"指数 {index_code!r} 无已验证人民币全收益映射：{mapping.get('status')}")
    today = datetime.now(_BJ_TZ).date().isoformat()
    basic = mapping.get("basic_date")
    if not basic:
        raise UpstreamFormatError(f"全收益指数 {mapping['total_return_code']} 缺 basicDate")
    start = _csindex_date(start_date, "start_date") if start_date else _csindex_date(basic, "basicDate")
    end = _csindex_date(end_date, "end_date") if end_date else today
    if end > today:
        raise ValueError(f"end_date 不可超过今天 {today}，收到 {end}")
    if start > end:
        raise ValueError(f"start_date {start} 晚于 end_date {end}")
    code = mapping["total_return_code"]
    current_year = int(today[:4])

    stale_current = None
    if not force_refresh:
        combined, complete = [], True
        for year in range(int(start[:4]), int(end[:4]) + 1):
            rel = f"csindex/daily/{code}_{year}.json"
            cached = _cache_read(rel, max_age_hours=20 if year == current_year else None)
            if cached is not None and not _csindex_cache_valid(cached, code):
                cached = None
            if cached is None:
                if year == current_year:
                    stale = _cache_read(rel, max_age_hours=None)
                    if _csindex_cache_valid(stale, code):
                        stale_current = stale
                complete = False
                break
            combined.extend(cached)
        if complete:
            return [x for x in _csindex_rebase_base_code(combined, mapping["base_index_code"])
                    if start <= x["date"] <= end]

    seed_rows, coverage_start = None, None
    if not force_refresh and int(start[:4]) == current_year == int(end[:4]):
        coverage_start = f"{current_year}-01-01"
        fetch_start = stale_current[-1]["date"] if stale_current else coverage_start
        seed_rows = stale_current
    else:
        fetch_start = _csindex_date(basic, "basicDate")
    rows, report = _csindex_download_history(
        mapping, fetch_start, today, force_refresh=force_refresh,
        seed_rows=seed_rows, coverage_start=coverage_start)
    report["fetched_at"] = datetime.now(_BJ_TZ).isoformat()
    report["requested_start"] = start
    report["requested_end"] = end
    _csindex_rebase_base_code(rows, mapping["base_index_code"])   # 落盘前归一到本次基础码
    by_year = defaultdict(list)
    for row in rows:
        by_year[int(row["date"][:4])].append(row)
    for year, year_rows in by_year.items():
        _cache_write(f"csindex/daily/{code}_{year}.json", year_rows)
    _cache_write(f"csindex/reports/{code}_latest.json", report)
    if (report.get("pending_today") or report.get("trade_dates_stale")) and current_year in by_year:
        # 当日行情未发布或交易日清单未纳入当天：当年文件回拨到约 2h 后过期，
        # 避免已发布行情被旧清单过滤后再固化 20h。
        back = time.time() - 18 * 3600
        os.utime(FUND_CACHE_DIR / f"csindex/daily/{code}_{current_year}.json",
                 (back, back))
    return [x for x in rows if start <= x["date"] <= end]


def csindex_sync_total_return(codes=None, all_indices: bool = False,
                              start_date="", end_date="", resume: bool = True) -> dict:
    """显式批量同步；风险/连续三次上游故障停止，单指数数据问题可续跑。

    V0.8.1：manifest 落盘走 file_lock 重读-合并-写回——双进程并发同步不再整文件
    互相覆盖（丢 job 即丢 resume 语义）；清单只保留本次窗口的 job（有界，不随
    历史窗口累积），异窗口并发以后写窗口为准（resume 判等本就按窗口）。"""
    if bool(codes) == bool(all_indices):
        raise ValueError("codes 与 all_indices=True 必须二选一，且不能同时省略")
    start_date = _csindex_date(start_date, "start_date") if start_date else ""
    end_date = _csindex_date(end_date, "end_date") if end_date else ""
    today = datetime.now(_BJ_TZ).date().isoformat()
    effective_end = end_date or today
    if start_date and start_date > effective_end:
        raise ValueError(
            f"start_date {start_date} 晚于有效结束日 {effective_end}；零网络拒绝同步")
    if end_date > today:
        raise ValueError(f"end_date 不可超过今天 {today}；零网络拒绝同步")
    if all_indices:
        selected = [x["index_code"] for x in csindex_universe()]
    else:
        selected = list(dict.fromkeys(_csindex_code(x) for x in codes))
    if not selected:
        raise ValueError("同步代码清单为空")
    rel = "csindex/sync_manifest.json"
    manifest = _cache_read(rel, max_age_hours=None) if resume else None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("jobs"), dict):
        manifest = {"jobs": {}}
    jobs = manifest["jobs"]

    def _manifest_write(job_code, job):
        with file_lock(FUND_CACHE_DIR / ".csindex_sync_manifest.lock"):
            disk = _cache_read(rel, max_age_hours=None)
            if not isinstance(disk, dict) or not isinstance(disk.get("jobs"), dict):
                disk = {"jobs": {}}
            disk["jobs"][job_code] = job
            disk["jobs"] = {k: v for k, v in disk["jobs"].items()
                            if (v.get("start_date", ""), v.get("end_date", ""))
                            == (start_date, end_date)}
            disk["updated_at"] = datetime.now(_BJ_TZ).isoformat()
            _cache_write(rel, disk)

    now = time.time()
    upstream_failures = 0
    summary = {"total": len(selected), "ok": 0, "no_data": 0,
               "no_total_return": 0, "return_type_unknown": 0,
               "ambiguous": 0, "error": 0, "skipped": 0,
               "request_count_start": CSINDEX_REQUEST_COUNT[0]}
    for code in selected:
        previous = jobs.get(code) or {}
        same_window = (previous.get("start_date", "") == start_date
                       and previous.get("end_date", "") == end_date)
        if resume and same_window and previous.get("status") == "ok":
            try:
                fetched_ts = datetime.fromisoformat(previous["fetched_at"]).timestamp()
            except (KeyError, ValueError, TypeError):
                fetched_ts = 0
            if now - fetched_ts < 20 * 3600:
                summary["ok"] += 1
                summary["skipped"] += 1
                continue
        job = {"base_index_code": code, "start_date": start_date, "end_date": end_date,
               "fetched_at": datetime.now(_BJ_TZ).isoformat()}
        try:
            mapping = csindex_resolve_total_return(code)
            if mapping.get("status") not in ("mapped", "already_total"):
                status = mapping["status"]
                job.update(status=status, mapping=mapping)
            else:
                rows = csindex_total_return_daily(code, start_date=start_date, end_date=end_date)
                status = "ok" if rows else "no_data"
                job.update(status=status, mapping=mapping, rows=len(rows),
                           first_date=rows[0]["date"] if rows else None,
                           last_date=rows[-1]["date"] if rows else None)
        except (RiskControlError, CircuitOpenError):
            job.update(status="error", error="风险熔断，未继续后续指数")
            jobs[code] = job
            _manifest_write(code, job)
            raise
        except Exception as e:
            status = "error"
            job.update(status=status, error=f"{type(e).__name__}: {str(e)[:300]}")
            if isinstance(e, (requests.RequestException, UpstreamRequestError)):
                upstream_failures += 1
                if upstream_failures >= 3:
                    jobs[code] = job
                    _manifest_write(code, job)
                    raise RuntimeError(
                        "中证同步连续 3 次上游传输/接口失败，已停止后续指数；"
                        "请排查上游后使用 resume=True 恢复") from e
            else:
                upstream_failures = 0
        else:
            upstream_failures = 0
        jobs[code] = job
        summary[status] = summary.get(status, 0) + 1
        _manifest_write(code, job)
    summary["requests"] = CSINDEX_REQUEST_COUNT[0] - summary.pop("request_count_start")
    summary["manifest"] = rel
    summary["jobs"] = [jobs[code] for code in selected if code in jobs]
    return summary
```

## 层 10 · 申万行业指数层（V0.8.2：官方清单 + 估值 + 日线 + 成分）

```python
# 数据源：申万宏源研究官网 www.swsresearch.com/institute-sw/api/（第一手，
# P34：该站负载均衡部分节点漏发中间证书，见 _sws_ca_bundle 的自组 bundle 方案，
# 绝不 verify=False）。AkShare 的乐咕聚合通道（token 反爬）不采用。
# 探针实锚（2026-11-24，sws 线预算 ≤9 发实花 4 发）：
#   · 估值报表 {"code":"200","message":"ok","data":{count,next,previous,results}}
#   · 估值回溯至 2003-01-02（更早无数据）；单码查询 swindexcode=801180 支持
#   · page_size=300 一页装下全年 242 行（翻页省请求）；行值多为字符串须 float 化
#   · trend 一发全史（801010 共 6497 行，最早 1999-12-30，基期 1000）；无日期参数
#   · markup=涨跌幅%（hike=涨跌点）→ change_pct 取 markup；成分 413 只单发拿全
#   · 流通市值 negotiablessharesum1 单位=亿元

SWS_MIN_INTERVAL = 1.0
SWS_BASE = "https://www.swsresearch.com/institute-sw/api"
SWS_SESSION = requests.Session()
SWS_SESSION.trust_env = False
SWS_SESSION.headers.update({"User-Agent": UA,
                            "Referer": "https://www.swsresearch.com/"})
SWS_REQUEST_COUNT = [0]
_SWS_CODE_RE = re.compile(r"\d{6}")
_SWS_INDEX_TYPES = ("市场表征", "一级行业", "二级行业", "风格指数")
# GET 轨四类（实测 9/31/124/16）；「大类风格指数」「金创指数」上游走另一 POST
# 接口（AkShare __index_realtime_sw 形态）——GET current/ 对金创返回空（烟测
# 实证 fail-loud 触发），归 backlog 不在 GET 白名单冒充
_SWS_VALUATION_EPOCH = "2003-01-01"              # 估值报表最早可用日（探针实锚）
# P34：GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1 中间证书（公共 CA 证书，
# DigiCert AIA 分发 cacerts.digicert.cn，有效期 2022-12-15 ~ 2032-12-14，
# 签发者根 DigiCert Global Root G2 已在系统信任库）。站点服务器漏发此级，
# 浏览器可 AIA 自动补抓而 requests/curl 不会——自组 bundle 保持完整链校验。
# 站点若换签发者：本常量失效 → SSLCertVerificationError 显式报错（fail-loud，
# 不计熔断），更新此 PEM 即恢复。
_SWS_EXTRA_CERT_PEM = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIFxjCCBK6gAwIBAgIQDwa7CTBhSCZ/yhtxwduAfTANBgkqhkiG9w0BAQsFADBh\n"
    "MQswCQYDVQQGEwJVUzEVMBMGA1UEChMMRGlnaUNlcnQgSW5jMRkwFwYDVQQLExB3\n"
    "d3cuZGlnaWNlcnQuY29tMSAwHgYDVQQDExdEaWdpQ2VydCBHbG9iYWwgUm9vdCBH\n"
    "MjAeFw0yMjEyMTUwMDAwMDBaFw0zMjEyMTQyMzU5NTlaMFsxCzAJBgNVBAYTAlVT\n"
    "MRcwFQYDVQQKEw5EaWdpQ2VydCwgSW5jLjEzMDEGA1UEAxMqR2VvVHJ1c3QgRzIg\n"
    "VExTIENOIFJTQTQwOTYgU0hBMjU2IDIwMjIgQ0ExMIICIjANBgkqhkiG9w0BAQEF\n"
    "AAOCAg8AMIICCgKCAgEAn1dxOo4OzYa4O1EJd0nhFI5QB/kXAJTHRI6C1J2Gz86B\n"
    "Ge9+DD8R4vexG7/QnUvV+5887o+G4enlkDwJV1Pehq4i0n+X6VKIPg5cThCx6/o0\n"
    "3bUkLWld7slhi3Hli/MaosZZuytdU1uCzQlGpaLB2TiTZbDImiVaykdfwl8V6AXP\n"
    "0Ab4wIcvPggl4qlwyPsBY6NODbP884BmL1ntdXfzecGst30FnAtm4w+PTo0I1T3F\n"
    "ITYXaNuIJnKonD1xXaN4Ar/rvcpKpntxVyZQ2T1Lb7vlfYISqNF+1ugpTzKnI1z7\n"
    "xV7tSqXd2vs6Csy6yFN+QLWwMnGWuQkvrO1m+F7V85S9ECpbqn9qNtKl8gIQ7JeZ\n"
    "BmBdLroW+4j1PDzBWmSWKB+gvRubVSTuoDR3VCtlI4G/Uh+ObF4UStbhKjr1SNCW\n"
    "JcrB1oF+wWBl8Bvozm4xflyyGDY47KCP/Fn9X5GHDNLQA9R0zqDKRumipp1UXswF\n"
    "uzH0I+gOuqSdZSsYID9XSVq6adcJ3rIMAcTuODcrPrJssOFKGFUbmY/VxbxMgYHI\n"
    "/07Zfdxoa+q0osWpofbMhLPZz9UuJFBCLsjgSckkSU02/4itmLm9e3lt6E/4q9Mc\n"
    "MCaS8+qrLWKG9wQtviC8zGynEXpxjEQE8WyrfeYKyXwZFCPB4rpTw1joJmGFeLUC\n"
    "AwEAAaOCAX4wggF6MBIGA1UdEwEB/wQIMAYBAf8CAQAwHQYDVR0OBBYEFEFOjmmd\n"
    "9G4l7HgUHH7XzR2Zz/lrMB8GA1UdIwQYMBaAFE4iVCAYlebjbuYP+vq5Eu0GF485\n"
    "MA4GA1UdDwEB/wQEAwIBhjAdBgNVHSUEFjAUBggrBgEFBQcDAQYIKwYBBQUHAwIw\n"
    "dAYIKwYBBQUHAQEEaDBmMCMGCCsGAQUFBzABhhdodHRwOi8vb2NzcC5kaWdpY2Vy\n"
    "dC5jbjA/BggrBgEFBQcwAoYzaHR0cDovL2NhY2VydHMuZGlnaWNlcnQuY24vRGln\n"
    "aUNlcnRHbG9iYWxSb290RzIuY3J0MEAGA1UdHwQ5MDcwNaAzoDGGL2h0dHA6Ly9j\n"
    "cmwuZGlnaWNlcnQuY24vRGlnaUNlcnRHbG9iYWxSb290RzIuY3JsMD0GA1UdIAQ2\n"
    "MDQwCwYJYIZIAYb9bAIBMAcGBWeBDAEBMAgGBmeBDAECATAIBgZngQwBAgIwCAYG\n"
    "Z4EMAQIDMA0GCSqGSIb3DQEBCwUAA4IBAQAkpDVI+nCKnAEEpDfldytHXUYOr2ys\n"
    "aGeboE3d1KAN4Gt+eioRvgNdmDKbVFCYY0C6ErIXIvSGXafsprU2dclka/kmH+9U\n"
    "4q3v5Acx6KwcnEuYLN0QOtrlQ9s3Z4IbIzdYUv8IauXC27a3x99x2WMVxNu1KGJy\n"
    "0r1Z+Xya1adf9/e1LFj1CGdq9HfQ/HFWP2khzxQZ4u62sOHuZdPgtNidFOhQLC+2\n"
    "5cNsPgqsaCrGLbSjzni7VN7NhAbTsLhA1oz41vHHB+q4ZzNlC01elmPBOtupe2HM\n"
    "/lqMA/J/nTxc718THdxpszFllAET1/lFEolnqihpUFPaPjRwU7yZIseS\n"
    "-----END CERTIFICATE-----\n"
)
def _sws_extra_cert_info() -> str:
    """内置中间证书摘要（与 _SWS_EXTRA_CERT_PEM 一并维护——站点换签发者时同步更新）。"""
    return ("GeoTrust G2 TLS CN RSA4096 SHA256 2022 CA1 / issuer=DigiCert Global Root G2 / "
            "notBefore=2022-12-15 / notAfter=2032-12-14 / AIA cacerts.digicert.cn")


_sws_ca_bundle_cache: list = []                  # 惰性单例（路径），FUND_CACHE_DIR 重定向后重建


def _sws_ca_bundle() -> str:
    """certifi 根库 + 申万站缺失的中间证书拼自组 bundle（完整链校验，绝不 verify=False）。

    bundle 落缓存目录（与缓存同生命周期）；FUND_CACHE_DIR 被测试重定向后按新目录
    重建（探测：路径变化即重写，不做内容探测——EXTRA_CERT 常量变更由版本升级
    触发全量失效）。"""
    import certifi
    path = FUND_CACHE_DIR / ".sws_ca_bundle.pem"
    if (_sws_ca_bundle_cache and str(_sws_ca_bundle_cache[0]) == str(path)
            and path.exists()):
        return str(path)
    path.parent.mkdir(parents=True, exist_ok=True)  # 全新环境缓存目录不存在（P1：import 即崩实测复现）
    base = Path(certifi.where()).read_text(encoding="utf-8")
    path.write_text(base + "\n" + _SWS_EXTRA_CERT_PEM, encoding="utf-8")
    _sws_ca_bundle_cache[:] = [path]
    return str(path)


SWS_SESSION.verify = _sws_ca_bundle()              # 模块级初始化（P34）；_sws_request 每请求刷新
                                                   # （FUND_CACHE_DIR 测试重定向后自动重建）


def _sws_code(value) -> str:
    code = str(value or "").strip()
    if not _SWS_CODE_RE.fullmatch(code):
        raise ValueError(f"申万指数代码须为 6 位数字（如 801010），收到 {value!r}")
    return code


def _sws_hit(url: str):
    SWS_REQUEST_COUNT[0] += 1


def _sws_conn_file() -> Path:
    return FUND_CACHE_DIR / ".cbreak_swconn"


_sws_conn_fails = [0]


def _sws_conn_check():
    """申万连接级熔断检查（P26 同款：RST/超时无状态码，F2 看不见）。

    证书校验失败（SSLCertVerificationError）不计入——证书问题≠IP 封锁，
    停机只会白白冻结通道；provider 级 gateway record_failure 照常计数作为
    证书风暴的兜底背板（有意接受，V0.8.2 审查钉死口径）。"""
    f = _sws_conn_file()
    if f.exists():
        try:
            until = float((f.read_text().split() or ["0"])[0])
        except (ValueError, OSError):
            until = 0.0
        if until > time.time():
            remain = int(until - time.time())
            raise RuntimeError(
                f"申万通道连接级熔断中（疑似 IP 级封锁/持续 RST，已自动停机保护），剩 {remain}s。"
                "停机期零网络——指数日线可用东财 kline 备胎口径过渡；继续请求只会延长封锁。")
        f.unlink(missing_ok=True)
        _sws_conn_fails[0] = 0


def _sws_conn_fail(why: str):
    _sws_conn_fails[0] += 1
    if _sws_conn_fails[0] >= 2:
        try:
            _sws_conn_file().parent.mkdir(parents=True, exist_ok=True)
            _sws_conn_file().write_text(f"{time.time() + _SINACONN_COOLDOWN:.0f} {why}")
        except OSError:
            pass
        _sws_conn_fails[0] = 0


def _sws_request(path: str, params: Optional[dict] = None, timeout: int = 30):
    """申万官网统一请求：独立配额/熔断/账本；业务 code≠200 → UpstreamRequestError。

    teardown 模式 fail-closed（csindex 同款）；证书校验失败显式报错不计连接级
    熔断（P34）；其余传输失败计连接级熔断 2 连败停 1h；403/429 风控分流（F6）。"""
    url = SWS_BASE + path
    headers = {"Referer": "https://www.swsresearch.com/"}
    _sws_conn_check()
    SWS_SESSION.verify = _sws_ca_bundle()          # 每请求刷新（FUND_CACHE_DIR 可被测试重定向）
    if _REQUEST_GATEWAY is not None:
        session = _SessionAdapter(SWS_SESSION, _sws_hit)
        try:
            resp = _REQUEST_GATEWAY.get(url, params=params, headers=headers,
                                        timeout=timeout, retries=1, retry_statuses=(),
                                        session=session)
        except requests.exceptions.SSLError as e:
            cause = e.__cause__
            if isinstance(cause, ssl.SSLCertVerificationError) or \
                    "CERTIFICATE_VERIFY_FAILED" in str(cause or e):
                # 证书问题≠封锁：不计连接级熔断（fail-loud 显式指引）；provider 级
                # record_failure 已在 gateway 内照计（兜底背板）
                raise RuntimeError(
                    "申万官网证书校验失败——服务器漏发中间证书且自组 CA bundle 未覆盖"
                    "（站点换签发者？）→ 更新 fund_data._SWS_EXTRA_CERT_PEM（P34）。"
                    "本错误不计入熔断。") from e
            _sws_conn_fail(f"sws-conn-ssl-{type(cause).__name__ if cause else 'SSL'}")
            raise                                  # 非证书类 SSL 错：连接级失败
        except requests.exceptions.RequestException as e:
            _sws_conn_fail(f"sws-conn-{type(e.__cause__).__name__ if e.__cause__ else type(e).__name__}")
            raise                                  # RST/超时/拒连：P26 同款 2 连败停 1h
        _release_pending(resp, "sws-passthrough")
    else:
        raise RuntimeError(
            "申万请求禁止 legacy/teardown 直连：跨进程滑窗、单并发和共享风控必须保持激活；"
            "请恢复 _reset_request_coordination() 后重试")
    assert_not_risk_control(resp, "sws", path)
    resp.raise_for_status()
    text = resp.content.decode("utf-8", errors="replace")
    _assert_not_risk_content(text, "sws", path)
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise UpstreamRequestError(f"申万响应非 JSON（{path}）: {text[:100]!r}") from e
    if not isinstance(body, dict) or str(body.get("code")) != "200":
        raise UpstreamRequestError(
            f"申万业务失败（{path}，code={body.get('code') if isinstance(body, dict) else None}，"
            f"msg={body.get('message') if isinstance(body, dict) else None}）")
    _sws_conn_fails[0] = 0
    _cbreak_success("sws")
    return body


def _sws_date(value, field: str = "date") -> str:
    """上游日期统一 ISO 化：兼容 1999-12-30 / 2003-01-10T08:00:00+08:00 / 20030110。"""
    text = str(value or "").strip()[:10]
    try:
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat()
    except ValueError as e:
        raise UpstreamFormatError(f"申万{field}须为日期形态，收到 {value!r}") from e


def _sws_float(value):
    """上游数值多为字符串（探针实锤）——float 化，空/非法→None（不拒绝，由各守卫裁决）。"""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def sw_index_list(indextype: str = "一级行业", force_refresh: bool = False) -> list[dict]:
    """申万官方指数清单（indextype 白名单五类；翻页拉全；24h 缓存）。

    返回 [{index_code, index_name, prev_close, open, latest, high, low,
    volume, amount}]；上游实为六类，「大类风格指数」走另一 POST 接口（本期
    GET 轨不覆盖）。一级行业锚：实收 ≥31 且含 801010（探针实值）。"""
    if indextype not in _SWS_INDEX_TYPES:
        raise ValueError(f"indextype 须为 {_SWS_INDEX_TYPES} 之一，收到 {indextype!r}")
    rel = f"sws/index_list_{indextype}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24)
        if cached is not None:
            return cached
    rows, page, received = [], 1, 0
    while True:
        body = _sws_request("/index_publish/current/", params={
            "page": page, "page_size": 200, "indextype": indextype})
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise UpstreamFormatError(f"申万清单响应缺 data/results（{indextype}）")
        results = data["results"]
        if not results and page == 1:
            raise UpstreamFormatError(f"申万清单空返回（{indextype}）——上游改版或软封嫌疑")
        for item in results:
            code = str(item.get("swindexcode") or "").strip()
            name = str(item.get("swindexname") or "").strip()
            if not _SWS_CODE_RE.fullmatch(code) or not name:
                raise UpstreamFormatError(f"申万清单行结构异常：{item!r:.120}")
            rows.append({
                "index_code": code, "index_name": name,
                "prev_close": _sws_float(item.get("l3")),
                "open": _sws_float(item.get("l4")),
                "amount": _sws_float(item.get("l5")),
                "high": _sws_float(item.get("l6")),
                "low": _sws_float(item.get("l7")),
                "latest": _sws_float(item.get("l8")),
                "volume": _sws_float(item.get("l11")),
            })
        received += len(results)
        if received >= int(data.get("count") or 0) or not results:
            break
        page += 1
        if page > 20:                              # 防御：count 漂移防死循环
            raise UpstreamFormatError(f"申万清单翻页超限（{indextype}，count={data.get('count')}）")
    total = int((body.get("data") or {}).get("count") or 0)
    if total and len(rows) != total:
        raise UpstreamFormatError(f"申万清单不完整：count={total}，实收={len(rows)}")
    if indextype == "一级行业" and (len(rows) < 31
                                    or "801010" not in {x["index_code"] for x in rows}):
        raise UpstreamFormatError(f"申万一级行业清单异常：实收 {len(rows)} 行（锚 ≥31 且含 801010）")
    _cache_write(rel, rows)
    return rows


def _sws_year_rows(rel_prefix: str, code: str, current_year: int,
                   stale_current: Optional[list] = None,
                   require_current: bool = False) -> tuple[list, bool]:
    """按年缓存拼接；日线可要求当前年存在，历史估值区间不强制当前年。"""
    import glob as _glob
    combined, complete, years = [], True, []
    for path in sorted(_glob.glob(str(FUND_CACHE_DIR / f"{rel_prefix}_*.json"))):
        stem = Path(path).stem
        try:
            years.append(int(stem.rsplit("_", 1)[-1]))
        except ValueError:
            continue                               # 非年文件（防御）
    if not years:
        return [], False
    if require_current and current_year not in years:
        complete = False
    for year in years:
        rel = f"{rel_prefix}_{year}.json"
        cached = _cache_read(rel, max_age_hours=20 if year == current_year else None)
        if cached is None and year == current_year:
            cached = stale_current                 # 过期但结构有效 → 增量种子
        if cached is None:
            complete = False
            break
        combined.extend(cached)
    return combined, complete


def _sws_cache_valid(rows, code: str, date_key: str = "date") -> bool:
    """sws 年缓存结构校验：行 dict/代码匹配/日期合法升序唯一/数值有限（bool 排除）。"""
    if not isinstance(rows, list) or not rows:
        return False
    dates = []
    for row in rows:
        if not isinstance(row, dict) or row.get("index_code") != code:
            return False
        day = row.get(date_key)
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return False
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return False
        close = row.get("close")
        if isinstance(close, bool) or not isinstance(close, (int, float)) \
                or not math.isfinite(close) or close <= 0:
            return False
        dates.append(day)
    return dates == sorted(dates) and len(set(dates)) == len(dates)


def sw_index_daily(index_code, period: str = "day",
                   force_refresh: bool = False) -> list[dict]:
    """申万指数日线全史（period=day/week/month；trend 一发全史，按年切片缓存）。

    返回 [{date, open, high, low, close, volume, amount, change_pct}] 升序；
    close 与 change_pct 勾稽 ±0.5% 警告（markup 口径未深探，先宽后紧，探针⑦锚）。
    当前年盘后发布：末行日期<今天 → 当年缓存 mtime 回拨 18h（≈2h 后过期增量），
    全史重发即可自然补当日行（trend 无日期参数，重发=全量一发）。
    已知局限：缓存完整性按现存年文件盘点——历史年文件被手工删除会静默短史
    （trend 每次全史落盘、重发一发即自愈；valuation 层已加请求年份对账）。"""
    code = _sws_code(index_code)
    if period not in ("day", "week", "month"):
        raise ValueError(f"period 须为 day/week/month，收到 {period!r}")
    today = datetime.now(_BJ_TZ).date().isoformat()
    current_year = int(today[:4])
    rel_prefix = f"sws/daily/{code}_{period}"

    if not force_refresh:
        combined, complete = _sws_year_rows(rel_prefix, code, current_year,
                                            require_current=True)
        if complete and combined:
            return combined
        # 当前年过期/缺失（complete=False）→ trend 一发全史重拉覆盖（无日期参数，
        # 增量无收益；mtime 回拨保证盘后 2h 内触发补当日行）
    body = _sws_request("/index_publish/trend/",
                        params={"swindexcode": code, "period": period.upper()},
                        timeout=60)
    raw = body.get("data")
    if not isinstance(raw, list) or not raw:
        raise UpstreamFormatError(f"申万日线空返回（{code} {period}）——上游改版或软封嫌疑")
    rows, warnings = [], []
    for item in raw:
        day = _sws_date(item.get("bargaindate"), "bargaindate")
        close = _sws_float(item.get("closeindex"))
        if close is None or close <= 0:
            raise UpstreamFormatError(f"申万日线收盘非法（{code} {day}: {close}）")
        open_ = _sws_float(item.get("openindex"))
        rows.append({
            "index_code": code, "date": day,
            "open": open_, "high": _sws_float(item.get("maxindex")),
            "low": _sws_float(item.get("minindex")), "close": close,
            "volume": _sws_float(item.get("bargainamount")),
            "amount": _sws_float(item.get("bargainsum")),
            "change_pct": _sws_float(item.get("markup")),
        })
        prev = rows[-2]["close"] if len(rows) >= 2 else None
        pct = rows[-1]["change_pct"]
        if prev and pct is not None and abs((close / prev - 1) * 100 - pct) > 0.5:
            warnings.append(f"{day}:change_pct")
    dates = [x["date"] for x in rows]
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise UpstreamFormatError(f"申万日线日期非升序或重复（{code}）")
    _cache_write(f"sws/reports/{code}_{period}_latest.json",
                 {"index_code": code, "period": period, "rows": len(rows),
                   "numeric_warnings": sorted(set(warnings)),
                   "first_date": rows[0]["date"], "last_date": rows[-1]["date"],
                   "fetched_at": datetime.now(_BJ_TZ).isoformat()})
    by_year = defaultdict(list)
    for row in rows:
        by_year[int(row["date"][:4])].append(row)
    for year, year_rows in by_year.items():
        _cache_write(f"{rel_prefix}_{year}.json", year_rows)
    if rows[-1]["date"] < today and datetime.now(_BJ_TZ).weekday() < 5:
        # 仅工作日盘后回拨；周末的周五末行是正常最新数据，不制造 2h 重拉循环。
        current_path = FUND_CACHE_DIR / f"{rel_prefix}_{current_year}.json"
        if current_year in by_year and current_path.exists():
            back = time.time() - 18 * 3600
            os.utime(current_path, (back, back))
    return rows


def sw_index_valuation(index_code, start_date: str = "", end_date: str = "",
                       force_refresh: bool = False) -> list[dict]:
    """申万官方估值序列（PE/PB/股息率/换手率/流通市值；行业基金对标核心增量）。

    返回 [{date, close, pe, pb, dividend_yield, turnover_rate, float_mv,
    mean_price}] 升序；pe 允许 None（亏损期口径，探针未采到负样本，负值合法
    不拒——守卫只拒 |pe|≥2000 的脏值）；pb 须 (0,500)；股息率 [0,30)。
    float_mv=negotiablessharesum1（单位亿元，探针实锚）。当日估值盘后发布：
    end=今天且缺行 → 不计缺失、pending_today 写入 sws/reports/ 缓存文件（同
    csindex 口径）；当年末行<今天 → mtime 回拨 18h。"""
    code = _sws_code(index_code)
    today = datetime.now(_BJ_TZ).date().isoformat()
    start = _sws_date(start_date, "start_date") if start_date else _SWS_VALUATION_EPOCH
    end = _sws_date(end_date, "end_date") if end_date else today
    if end > today:
        raise ValueError(f"end_date 不可超过今天 {today}，收到 {end}")
    if start > end:
        raise ValueError(f"start_date {start} 晚于 end_date {end}")
    current_year = int(today[:4])
    rel_prefix = f"sws/valuation/{code}"

    stale_current, combined = None, []
    if not force_refresh:
        rel_current = f"{rel_prefix}_{current_year}.json"
        cached_current = _cache_read(rel_current, max_age_hours=20)
        if cached_current is not None and _sws_cache_valid(cached_current, code):
            stale_current = None                   # 新鲜：直接返回缓存，不做增量
        else:
            stale = _cache_read(rel_current, max_age_hours=None)
            if _sws_cache_valid(stale, code):
                stale_current = stale              # 仅过期缓存作增量种子

    def _parse(raw):
        rows, seen = [], set()
        for item in raw:
            day = _sws_date(item.get("bargaindate"), "bargaindate")
            if day in seen:
                raise UpstreamFormatError(f"申万估值重复日期（{code} {day}）")
            seen.add(day)
            close = _sws_float(item.get("closeindex"))
            if close is None or close <= 0:
                raise UpstreamFormatError(f"申万估值收盘非法（{code} {day}: {close}）")
            pe, pb, dp = (_sws_float(item.get("pe")),
                          _sws_float(item.get("pb")), _sws_float(item.get("dp")))
            if pe is not None and abs(pe) >= 2000:
                raise UpstreamFormatError(f"申万估值 PE 越界（{code} {day}: {pe}）")
            if pb is not None and not 0 < pb < 500:
                raise UpstreamFormatError(f"申万估值 PB 越界（{code} {day}: {pb}）")
            if dp is not None and not 0 <= dp < 30:
                raise UpstreamFormatError(f"申万估值股息率越界（{code} {day}: {dp}）")
            rows.append({
                "index_code": code, "date": day, "close": close,
                "pe": pe, "pb": pb, "dividend_yield": dp,
                "turnover_rate": _sws_float(item.get("turnoverrate")),
                "float_mv": _sws_float(item.get("negotiablessharesum1")),
                "mean_price": _sws_float(item.get("meanprice")),
            })
        # 上游按日期降序返回（探针⑥a：单页首行=区间最新日）——排序后再校验
        rows.sort(key=lambda x: x["date"])   # 上游降序返回（探针⑥a）——排正后查重
        if len({x["date"] for x in rows}) != len(rows):
            raise UpstreamFormatError(f"申万估值日期重复（{code}）")
        return rows

    need_fetch, fetch_start = True, None
    report_rel = f"sws/reports/{code}_val_latest.json"
    previous_report = _cache_read(report_rel, max_age_hours=None) or {}
    if not force_refresh:
        combined, complete = _sws_year_rows(rel_prefix, code, current_year,
                                            stale_current or [])
        # P1（审查实测复现）：complete 只盘点磁盘现存年——请求了未缓存年份
        # （如先查 2025 再查 2024、或默认全史）会静默返回空/短史。complete
        # 须对照请求区间年份集合，缺年显式取数（绝不静默空）
        lower_year = int(start[:4])
        verified_earliest = previous_report.get("verified_earliest")
        if isinstance(verified_earliest, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", verified_earliest):
            lower_year = max(lower_year, int(verified_earliest[:4]))
        end_year = int(min(end, today)[:4])
        if lower_year > end_year:
            return []
        want_years = set(range(lower_year, end_year + 1))
        have_years = {int(x["date"][:4]) for x in combined}
        if complete and not want_years.issubset(have_years):
            complete = False
            fetch_start = f"{min(want_years - have_years)}-01-01"
        if complete:
            if stale_current is None:
                return [x for x in combined if start <= x["date"] <= end]
            # 当前年过期：增量从末行起（重叠一天，合并以新覆旧）
            last = max(stale_current, key=lambda x: x["date"])["date"]
            need_fetch, fetch_start = True, last
        elif fetch_start is None:
            fetch_start = f"{min(want_years)}-01-01"
    effective_start = fetch_start or start
    raw_all, page = [], 1
    while True:
        body = _sws_request("/index_analysis/index_analysis_report/", params={
            "page": page, "page_size": 500, "index_type": "一级行业",
            # 上游要 ISO 带横线格式（YYYY-MM-DD）——探针⑥实证（紧凑 YYYYMMDD
            # HTTP 500 首版烟测实锤）；page_size=500 在线全量 507 实证可用
            # （探针⑥单锚 300 一页装下全年 242 行）
            "start_date": effective_start,
            "end_date": today, "type": "DAY",
            "swindexcode": code}, timeout=60)
        data = body.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise UpstreamFormatError(f"申万估值响应缺 data/results（{code}）")
        raw_all.extend(data["results"])
        total = int(data.get("count") or 0)
        if not data["results"] or (total and len(raw_all) >= total):
            break
        page += 1
        if page > 100:
            raise UpstreamFormatError(f"申万估值翻页超限（{code}）")
    # P2：终局 count 对账（防 count 缺失时半截史静默入库——清单层同款）
    total = int((body.get("data") or {}).get("count") or 0)
    if total and len(raw_all) != total:
        raise UpstreamFormatError(f"申万估值不完整：count={total}，实收={len(raw_all)}")
    fresh = _parse(raw_all)
    if not fresh and (not combined or end != today):
        raise UpstreamFormatError(f"申万估值空返回（{code} {start}~{end}）——上游改版/软封嫌疑")
    pending_today = None
    latest_available = fresh[-1]["date"] if fresh else combined[-1]["date"]
    if end == today and latest_available < today:
        pending_today = today                  # 当日盘后未发布：豁免不拒
    merged = {x["date"]: x for x in combined}  # 全史拼段为基座（防历史年丢失）
    for x in fresh:                            # 新覆旧（重叠段）
        merged[x["date"]] = x
    rows = [merged[d] for d in sorted(merged)]
    verified_earliest = previous_report.get("verified_earliest")
    if effective_start == _SWS_VALUATION_EPOCH and fresh:
        verified_earliest = fresh[0]["date"]
    report = {"index_code": code, "rows": len(rows),
               "missing_dates": [], "pending_today": pending_today,
               "verified_earliest": verified_earliest,
               "fetched_at": datetime.now(_BJ_TZ).isoformat()}
    by_year = defaultdict(list)
    for row in rows:
        by_year[int(row["date"][:4])].append(row)
    for year, year_rows in by_year.items():
        _cache_write(f"{rel_prefix}_{year}.json", year_rows)
    _cache_write(report_rel, report)
    if pending_today and current_year in by_year and datetime.now(_BJ_TZ).weekday() < 5:
        current_path = FUND_CACHE_DIR / f"{rel_prefix}_{current_year}.json"
        if current_path.exists():
            back = time.time() - 18 * 3600
            os.utime(current_path, (back, back))
    return [x for x in rows if start <= x["date"] <= end]


def sw_index_components(index_code, force_refresh: bool = False) -> list[dict]:
    """申万指数成分股（单发 page_size=10000；季频数据 30 天缓存）。

    返回 [{stock_code, stock_name, weight, since}]（weight %；since=计入日期）。
    空成分显式报错不静默空；权重和 ∉[95,105] → stderr 警告不拒绝（申万含
    长尾低权重股，历史上曾个别缺行）。"""
    code = _sws_code(index_code)
    rel = f"sws/components/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=24 * 30)
        if cached is not None:
            return cached
    body = _sws_request("/index_publish/details/component_stocks/",
                        params={"swindexcode": code, "page": 1, "page_size": 10000})
    data = body.get("data")
    results = (data or {}).get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        raise UpstreamFormatError(f"申万成分空返回（{code}）——指数无公开成分或上游改版")
    rows = []
    for item in results:
        stock = str(item.get("stockcode") or "").strip()
        if not stock:
            continue
        rows.append({
            "index_code": code, "stock_code": stock,
            "stock_name": str(item.get("stockname") or "").strip(),
            "weight": _sws_float(item.get("newweight")),
            "since": _sws_date(item.get("beginningdate"), "beginningdate")
            if item.get("beginningdate") else None,
        })
    weight_sum = sum(x["weight"] or 0 for x in rows)
    if not 95 <= weight_sum <= 105:
        print(f"[fund_data] 警告：申万成分 {code} 权重和 {weight_sum:.2f}%"
              " ∉[95,105]（长尾缺行嫌疑），数据照常返回", file=sys.stderr)
    _cache_write(rel, rows)
    return rows
```

## 层 11 · 公募REITs层（V0.8.4：腾讯基础数据 + 询问后 AI 直读报告）

```python
# 两段式：第一段只用腾讯拉基础数据（零 PDF）→ AI 先把画像交给用户并询问 → 确认后才进
# 第二段取报告文本，由 AI 直接读。库里不写任何抽取规则：不正则、不抽值、不缓存读数。
# 2026-11-26 实测钉死（180101 主锚，508000 沪市交叉）：
#   · 公募 REIT 不按日披露份额净值（半年频，中期/年度报告）；东财 E2/E6 对 REIT 的净值
#     停在发行价（180101 恒 2.31@2021）——qdii_premium 据此算出约 -40% 的假溢价（P36）
#   · 腾讯行情串自带单位净值（第 81 段）与折溢价率（第 77 段），103 只一次批量可取
#     （URL 956 字符）；净值日期与净值历史取 stockjs.finance.qq.com 的 year_all 静态文件
#   · 新上市 REIT 的净值是发行价（净值日期 = 成立日，508610：2026-11-03）——输出必带净值日期
#   · 腾讯前复权是减法复权（除息前 qfq = 不复权 − 分红）——分红再投资收益用不复权收盘自算
#   · 经营指标/可供分配金额/收入净利润现金流/评估值只在定期报告 PDF：第二段 AI 直读

_REIT_CODE_RE = re.compile(r"508\d{3}|180[1-9]\d{2}|181\d{3}")
_REIT_HALF_YEAR_ENDS = ("06-30", "12-31")   # 公募 REIT 份额净值的披露节点（中期/年度报告）
_REIT_NAV_TTL_HOURS = 24               # 净值历史：半年频，当日缓存足够
_REIT_DIV_TTL_HOURS = 24               # 分红历史：事件频，当日缓存足够
_REIT_ZONE_TTL_HOURS = 300 / 3600      # 实时概览 5 分钟微缓存（防同会话重复调用连打）
_REIT_KLINE_TTL_HOURS = 20
_REIT_KLINE_BARS = 400                 # 不复权日线根数 ≈ 1.6 年（年初至今 + 近一年 + 缓冲）
_REIT_ANNUAL_READY = (4, 10)           # 年报（12-31 份额净值）披露截止 3 月末，再留 10 天等腾讯更新
_REIT_INTERIM_READY = (9, 10)          # 中期报告（06-30 份额净值）披露截止 8 月末，再留 10 天
_REIT_TTM_DAYS = 365
_REIT_FANOUT_INTERVAL = 1.0            # 逐只补齐的请求间隔下限（秒，另加 0~0.3s 抖动）
_REIT_FANOUT_MAX_FAILS = 2             # 连续失败 2 次即中止（403/429/熔断另有即时上抛）
_REIT_QUOTE_CHUNK = 200                # 行情批量每请求代码数（103 只实测单请求）
_REIT_EMPTY_BACKDATE_HOURS = 18        # 空结果缓存 mtime 回拨 18h → 剩余有效期 6h
_REIT_TOC_HIT_MIN = 6                  # 一页命中 ≥6 个不同词条 = 疑似目录页
_REIT_EXTRACTOR = "pypdf-layout-v1"     # 报告文本缓存的抽取方式标记：换抽取方式即自动重抽
# 连接层失败的归类（防封语义见 _tencent_fund_get / _reit_fill_nav_dates）：http.client.HTTPException
# 族（坏状态行/残包等）不是 OSError 子类，不单列就会从 OSError 分支漏过去
_REIT_TRANSPORT_ERRORS = (OSError, http.client.HTTPException)
_REIT_FANOUT_FAIL_ERRORS = (OSError, http.client.HTTPException, RuntimeError, ValueError)
# 关键词页码定位词表：上交所《REITs 规则适用指引第 7 号》规定的披露项 + 实测补充。
# 只用来定位页码，不抽值；泛词（评估/运营管理）实测单份命中 15~30 页，已换成评估值/评估方法
_REIT_KEYWORDS = ("可供分配金额", "实际分配金额", "现金流分派率", "主要财务指标",
                  "经营活动产生的现金流量净额", "出租率", "平均租金", "收缴率", "剩余租期",
                  "车流量", "通行费收入", "发电量", "结算电量", "入住率", "持有人结构",
                  "份额净值", "评估值", "评估方法", "业态")
_REIT_READING_TIPS = (
    "页码一律指 PDF 序号（第 N 页 = PDF 第 N 页），与报告目录的印刷页码有偏移",
    "表格列多：注意本期、上年同期、本年累计、同比不要串列；金额单位看表头（人民币元/万元）",
    "读完用 cross_check 对账：单位实际分配 ↔ 腾讯分红史，期末份额净值 ↔ 腾讯净值；对不上先怀疑读串了行列",
    "keyword_pages 只是定位建议（可能有漏和误），以原文为准",
)
_REIT_DETAIL_PROMPT = ("以上是 {code} 的基础画像（腾讯：行情/半年频净值/折溢价/分红史/分红再投资收益）。"
                       "是否继续读它的定期报告，取经营指标、可供分配金额、收入/净利润/经营现金流等详细数据？"
                       "这些只在报告 PDF 里：东财 2~3 发（清单 1 + PDF 1~2），由 AI 直接读原文。"
                       "确认后先 reit_report_text(\"{code}\") 取页码索引，再指定页码读正文。")


class _ReitPacer:
    """REIT 层请求间慢节拍（防封第一位）：首发不等，其后每次网络命中前先等
    _REIT_FANOUT_INTERVAL + 0~0.3s 抖动。叠在协调器 0.2s 腾讯地板之上——全市场逐只
    补齐 103 发按约 1 发/秒出，不打成突发；只在真正发请求前等，缓存命中不空等。"""

    def __init__(self):
        self.sent = 0

    def wait(self):
        if self.sent:
            time.sleep(_REIT_FANOUT_INTERVAL + random.uniform(0, 0.3))
        self.sent += 1


def _reit_code(value) -> str:
    """归一化 REIT 代码：sh/sz 前缀可选 → 6 位数字；非法抛 ValueError。"""
    c = str(value or "").strip().lower()
    for pre in ("sh", "sz", "bj"):
        if c.startswith(pre):
            c = c[len(pre):]
    if not re.fullmatch(r"\d{6}", c):
        raise ValueError(f"REIT 代码须为 6 位数字（可带 sh/sz 前缀），收到 {value!r}")
    return c


def _is_reit(code, allow_load: bool = True) -> tuple[bool, str]:
    """是不是公募 REIT → (是否, 依据)。

    依据 fund_list：清单类型列是 Reits；code_pattern：清单没这么说（新上市未进 30 天缓存
    清单、清单没装载，或上游改了类型串），但代码落在 508xxx / 180[1-9]xx / 181xxx——号段
    是交易所分配给 REIT 的，形态命中即判 REIT：2026-11-26 实测命中全部 103 只，180 号段的
    15 只非 REIT 全是 1800xx，零误判（180 号段不可整体当 REIT）。类型串哪怕改名
    （Reits→REIT），qdii_premium 的假溢价防线也不失效。其余 not_reit / not_code。
    allow_load=True（网络函数用）：清单未装载时经 fund_list() 加载（磁盘缓存命中 0 请求，
    冷时东财 1 请求）。清单取不到（东财熔断/网络失败）不在此处上抛——降级到形态判断：
    东财故障不该拦住纯腾讯的 reit_profile；真要打东财的调用方（qdii_premium）会在它自己的
    东财调用点如实抛出。
    allow_load=False（零网络函数用）：只看进程内已装载的清单（_FUND_LIST_MEM），没装载
    就直接按形态判断，绝不触发加载——否则 fund_metrics 这类「零网络」函数会在报错分支
    里发请求、带上重试与等待。"""
    try:
        c = _reit_code(code)
    except ValueError:
        return (False, "not_code")
    if allow_load:
        try:
            source = fund_list()
        except Exception:
            source = None
    else:
        source = _FUND_LIST_MEM
    row = next((f for f in (source or []) if f.get("code") == c), None)
    if row is not None and str(row.get("type") or "").strip().lower() == "reits":
        return (True, "fund_list")
    if _REIT_CODE_RE.fullmatch(c):
        return (True, "code_pattern")              # 号段命中即判 REIT，不因清单类型串改名而漏拦
    return (False, "fund_list" if row is not None else "not_reit")


def _reit_not_applicable(code) -> str:
    """REIT 走到净值类工具的报错分支时追加的指路文案；非 REIT 或判定失败返回空串
    （指路文案绝不让原本的报错变形）。只看进程内已装载的清单与代码形态：零网络、
    零加载——本函数被 fund_metrics / fund_dca_backtest（L5 零网络层）的报错分支调用。"""
    try:
        hit, _ = _is_reit(code, allow_load=False)
    except Exception:
        return ""
    if not hit:
        return ""
    return (f"。{code} 是公募 REIT：不按日披露份额净值（东财净值停在发行价），净值类指标不适用"
            "——请用 reit_profile(code)（腾讯半年频净值 + 分红再投资收益）")


def _reit_num(value) -> Optional[float]:
    """字段 → 数值；空串/非数值/非有限数返回 None。REIT 行不用 tencent_quote 的 0.0
    约定——净值缺失必须是 None，0.0 会被当成真净值算出 -100% 的折溢价。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _reit_iso_date(value) -> Optional[str]:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text) else None


def _tencent_fund_get(url: str, referer: bool = True) -> bytes:
    """腾讯基金页接口取原始字节：经 _tencent_raw（许可/配额/403·429 分流/P12 直连）。
    瞬态网络错退避 2s 重试 1 次（tencent_quote 同口径）；有状态码失败与风控信号不重试
    ——HTTPError 是 OSError 子类，须先于 OSError 捕获；http.client.HTTPException 族
    （坏状态行/残包）不是 OSError，须单列，否则既不重试也会炸穿行情板的逐只补齐。"""
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = "https://gu.qq.com/"
    for attempt in (1, 2):
        try:
            return _tencent_raw(url, headers=headers)
        except urllib.error.HTTPError:
            raise
        except _REIT_TRANSPORT_ERRORS:
            if attempt == 2:
                raise
            time.sleep(2.0)


def _reit_quote_time(raw) -> Optional[str]:
    """行情串第 30 段 20261126120530 → 2026-11-26 12:05:30；形态不符返回 None。"""
    t = str(raw or "").strip()
    if not re.fullmatch(r"\d{14}", t):
        return None
    return f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:]}"


def _parse_reit_quotes(text: str) -> dict[str, dict]:
    """腾讯批量行情响应（GBK 已解码）→ {6 位代码: 行情字段}（纯函数）。

    字段位 2026-11-26 实测（88 段/行，沪深 REIT 同构）：1 名称 / 3 现价 / 4 昨收 / 5 今开 /
    30 行情时间 / 32 涨跌% / 33 最高 / 34 最低 / 36 成交量(手) / 37 成交额(万) / 38 换手率% /
    45 总市值(亿) / 77 折溢价率% / 81 单位净值。不足 82 段时净值与折溢价留空不抛；
    有行但 0 行解析成功 = 疑似字段结构变更，拒绝静默空。"""
    out, lines = {}, 0
    for line in text.split(";"):
        if '="' not in line:
            continue
        v = line.split('="', 1)[1].split("~")
        if len(v) < 4:
            continue
        lines += 1
        code = v[2].strip()
        if not re.fullmatch(r"\d{6}", code):
            continue

        def f(i):
            return _reit_num(v[i]) if i < len(v) else None

        row = {"code": code, "name": v[1].strip(), "price": f(3), "last_close": f(4),
               "open": f(5), "quote_time": _reit_quote_time(v[30] if len(v) > 30 else None),
               "change_pct": f(32), "high": f(33), "low": f(34), "volume_lots": f(36),
               "amount_wan": f(37), "turnover_pct": f(38), "mcap_yi": f(45),
               "nav": f(81), "premium_pct": f(77)}
        out[code] = row
    if lines and not out:
        raise RuntimeError(f"腾讯 REIT 行情响应含 {lines} 行但 0 行解析成功——疑似字段结构变更，拒绝静默空")
    return out


def _parse_reit_nav_history(text: str) -> list[dict]:
    """腾讯净值历史 year_all 静态文件 → [{date, nav, acc_nav}] 升序（纯函数）。

    原文形态 fundNavAllYearData={"code": "jj180101", "data": [["20260630", "2.1141",
    "2.5299"], ...], "name": ...}（JSON，中文为转义形式）。公募 REIT 不按日披露份额净值，实测
    半年频：180101 共 11 点，2021-06-07 的 2.3100 → 2026-06-30 的 2.1141，与中报份额净值
    逐位一致。空 data 返回空列表；单行畸形/日期重复/净值非正数拒绝（拒绝静默错）。"""
    brace = text.find("{")
    if brace < 0:
        raise UpstreamFormatError(f"腾讯净值历史无 JSON 对象: {text[:60]!r}")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[brace:])
    except ValueError as e:
        raise UpstreamFormatError(f"腾讯净值历史非 JSON: {text[:60]!r}") from e
    rows = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        raise UpstreamFormatError("腾讯净值历史缺 data 列表（疑似上游改版）")
    out, seen = [], set()
    for x in rows:
        day = str(x[0]) if isinstance(x, list) and x else ""
        nav = _reit_num(str(x[1])) if isinstance(x, list) and len(x) > 1 else None
        if not re.fullmatch(r"\d{8}", day) or nav is None or nav <= 0:
            raise UpstreamFormatError(f"腾讯净值历史行畸形: {x!r}")
        if day in seen:
            raise UpstreamFormatError(f"腾讯净值历史日期重复: {day}")
        seen.add(day)
        acc = _reit_num(x[2]) if len(x) > 2 and x[2] not in (None, "") else None
        out.append({"date": f"{day[:4]}-{day[4:6]}-{day[6:]}", "nav": nav, "acc_nav": acc})
    out.sort(key=lambda r: r["date"])
    return out


def _parse_reit_zone(text: str, code: str) -> dict:
    """腾讯基金实时概览 getPriceZone → {quote, nav, basic, totals}（纯函数）。

    原文三层：data.data 行情（zxjg 现价 / jtkp 今开 / zgj 最高 / zdj 最低 / ztsp 昨收 /
    cjl 成交量(手) / cj_total_amount 成交额(万) / jgzffd 涨跌% / zyjl 折溢价率(小数) /
    update_time），data.info 基本信息 21 字段，data.profit 累计分红。
    只暴露核实过的字段——**不返回**：fxrq「发行日期」（实为最近一次发行，180101 显示
    2023-05-24 是扩募日）、clgm「成立规模」（单位不明）、sgzt/shzt 申购赎回状态
    （对 REIT 恒显示「开放」，是错的）。zxgm「最新规模」单位万元（180101 实测 300439
    万元 = 中报净资产 30.04 亿）。jjdm 与请求代码不符 = 串台，拒绝。"""
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise UpstreamFormatError(f"腾讯实时概览非 JSON（{code}）: {text[:60]!r}") from e
    if not isinstance(obj, dict) or obj.get("code") not in (0, "0"):
        raise UpstreamFormatError(f"腾讯实时概览业务码异常（{code}）: {str(obj)[:60]!r}")
    data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
    live, info, profit = data.get("data"), data.get("info"), data.get("profit")
    if not (isinstance(live, dict) and isinstance(info, dict) and isinstance(profit, dict)):
        raise UpstreamFormatError(f"腾讯实时概览缺 data/data·info/profit 段（{code}）——疑似上游改版")
    if info.get("jjdm") and str(info.get("jjdm")).strip() != code:
        raise UpstreamFormatError(f"腾讯实时概览串台：请求 {code} 返回 jjdm={info.get('jjdm')!r}")
    tx_premium = _reit_num(live.get("zyjl"))
    return {
        "quote": {"price": _reit_num(live.get("zxjg")), "open": _reit_num(live.get("jtkp")),
                  "high": _reit_num(live.get("zgj")), "low": _reit_num(live.get("zdj")),
                  "last_close": _reit_num(live.get("ztsp")),
                  "volume_lots": _reit_num(live.get("cjl")),
                  "amount_wan": _reit_num(live.get("cj_total_amount")),
                  "change_pct": _reit_num(live.get("jgzffd")),
                  "premium_pct": round(tx_premium * 100, 4) if tx_premium is not None else None,
                  "time": live.get("update_time")},
        "nav": {"nav": _reit_num(info.get("dwjz")), "nav_date": _reit_iso_date(info.get("jzrq")),
                "acc_nav": _reit_num(info.get("ljjz"))},
        "basic": {"code": code, "full_name": info.get("jjqc"), "short_name": info.get("jjjc"),
                  "established": _reit_iso_date(info.get("clrq")),
                  "manager_company": info.get("glrmc"), "custodian": info.get("tgrmc"),
                  "managers": info.get("jjjl"), "latest_scale_wan": _reit_num(info.get("zxgm"))},
        "totals": {"dividend_per_unit": _reit_num(profit.get("ljfh")),
                   "dividend_count": _reit_num(profit.get("ljfhcs"))},
    }


def _parse_reit_dividends(text: str, sym: str) -> list[dict]:
    """腾讯分红历史 getProfitHistory → [{ex_date, per_unit}] 新→旧（纯函数）。

    原文形态 data.{sym}.{total, data:[{date: 除息日, fh: 每份分红}]}——180101 实测 13 条，
    日期与东财分红表逐条一致（180102 亦逐条核对）。没有分红的新 REIT 节点缺失或
    data 为空 → 空列表。行畸形/除息日重复/金额非正数拒绝。"""
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise UpstreamFormatError(f"腾讯分红历史非 JSON（{sym}）: {text[:60]!r}") from e
    if not isinstance(obj, dict) or obj.get("code") not in (0, "0"):
        raise UpstreamFormatError(f"腾讯分红历史业务码异常（{sym}）: {str(obj)[:60]!r}")
    data = obj.get("data")
    if not isinstance(data, dict):
        raise UpstreamFormatError(f"腾讯分红历史缺 data（{sym}）——疑似上游改版")
    node = data.get(sym)
    if node is None and len(data) == 1:                 # 节点键形态漂移：仅此一节点时直接采用
        node = next(iter(data.values()))
    if node is None:
        if data:
            raise UpstreamFormatError(f"腾讯分红历史无 {sym} 节点: {list(data)[:3]}")
        return []
    rows = node.get("data") if isinstance(node, dict) else None
    if rows is None and isinstance(node, dict) and "total" in node:
        rows = []                                        # total 在而 data 缺席 = 没有分红
    if not isinstance(rows, list):
        raise UpstreamFormatError(f"腾讯分红历史缺 data 列表（{sym}）")
    out = []
    for x in rows:
        ex_date = str(x.get("date")) if isinstance(x, dict) else ""
        per_unit = _reit_num(x.get("fh")) if isinstance(x, dict) else None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", ex_date) or per_unit is None or per_unit <= 0:
            raise UpstreamFormatError(f"腾讯分红历史行畸形（{sym}）: {x!r}")
        out.append({"ex_date": ex_date, "per_unit": round(per_unit, 6)})
    if len({r["ex_date"] for r in out}) != len(out):
        raise UpstreamFormatError(f"腾讯分红历史除息日重复（{sym}）")
    out.sort(key=lambda r: r["ex_date"], reverse=True)
    return out


def _reit_backdate(rel: str, hours: float) -> None:
    """缓存文件 mtime 回拨 hours（缩短剩余有效期：空结果/未取到不该占满 TTL）。"""
    p = FUND_CACHE_DIR / rel
    try:
        if p.exists():
            back = time.time() - hours * 3600
            os.utime(p, (back, back))
    except OSError:
        pass


def _reit_nav_history(code: str, force_refresh: bool = False, pacer=None) -> dict:
    """腾讯净值历史（1 请求，24h 缓存）→ {code, history:[{date,nav,acc_nav}], note, fetched_at}。
    404 = 腾讯没有该代码的静态文件（新上市未生成）→ 空历史，缓存有效期缩为 6h。"""
    rel = f"reits/navhist/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=_REIT_NAV_TTL_HOURS)
        if cached is not None and isinstance(cached.get("history"), list):
            return cached
    if pacer is not None:
        pacer.wait()
    note = None
    try:
        url = f"https://stockjs.finance.qq.com/fundUnitNavAll/data/year_all/{code}.js"
        history = _parse_reit_nav_history(_tencent_fund_get(url).decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        history, note = [], "腾讯没有该代码的净值历史文件（HTTP 404：新上市未生成，或代码有误）"
    else:
        _cbreak_success("tencent")
    payload = {"code": code, "history": history, "note": note,
               "fetched_at": datetime.now(_BJ_TZ).isoformat(timespec="seconds")}
    _cache_write(rel, payload)
    if not history:
        _reit_backdate(rel, _REIT_EMPTY_BACKDATE_HOURS)
    return payload


def _reit_zone(pre: str, force_refresh: bool = False, pacer=None) -> dict:
    """腾讯基金实时概览（1 请求，5 分钟微缓存）：行情 + 净值日期 + 基本信息 + 累计分红。"""
    code = pre[2:]
    rel = f"reits/zone/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=_REIT_ZONE_TTL_HOURS)
        if cached is not None and isinstance(cached.get("zone"), dict):
            return cached["zone"]
    if pacer is not None:
        pacer.wait()
    url = f"https://web.ifzq.gtimg.cn/fund/newfund/fundBase/getPriceZone?symbol={pre}"
    zone = _parse_reit_zone(_tencent_fund_get(url).decode("utf-8", errors="replace"), code)
    _cbreak_success("tencent")
    _cache_write(rel, {"code": code,
                       "fetched_at": datetime.now(_BJ_TZ).isoformat(timespec="seconds"),
                       "zone": zone})
    return zone


def _reit_dividends(pre: str, force_refresh: bool = False, pacer=None) -> dict:
    """腾讯分红历史（1 请求，24h 缓存）→ {code, history:[{ex_date,per_unit}] 新→旧}。"""
    code = pre[2:]
    rel = f"reits/div/{code}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=_REIT_DIV_TTL_HOURS)
        if cached is not None and isinstance(cached.get("history"), list):
            return cached
    if pacer is not None:
        pacer.wait()
    url = ("https://web.ifzq.gtimg.cn/fund/newfund/fundProfit/getProfitHistory"
           f"?app=web&symbol={pre}")
    history = _parse_reit_dividends(_tencent_fund_get(url).decode("utf-8", errors="replace"), pre)
    _cbreak_success("tencent")
    payload = {"code": code, "history": history,
               "fetched_at": datetime.now(_BJ_TZ).isoformat(timespec="seconds")}
    _cache_write(rel, payload)
    return payload


def _reit_kline_raw(pre: str, force_refresh: bool = False, pacer=None) -> list[dict]:
    """不复权日线收盘 [{date, close}] 升序（1 请求，20h 缓存）。

    当日盘中 bar 剔除——收益指标只算到上一收盘，缓存期内不会冻结一个盘中价；
    所以返回可能比请求根数少 1。上市不足 2 个交易日时不足 2 行，由调用方如实标注。
    空返回（今日才上市，或上游异常）**不缓存**——否则一次异常空响应会把这只 REIT 的
    收益指标钉死 20 小时。"""
    rel = f"reits/kline_raw/{pre[2:]}_{_REIT_KLINE_BARS}.json"
    if not force_refresh:
        cached = _cache_read(rel, max_age_hours=_REIT_KLINE_TTL_HOURS)
        if isinstance(cached, list) and cached:
            return cached
    if pacer is not None:
        pacer.wait()
    today = datetime.now(_BJ_TZ).date().isoformat()
    rows = [r for r in _tencent_kline_page(pre, bars=_REIT_KLINE_BARS, fq="") if r["date"] < today]
    _cbreak_success("tencent")
    if rows:
        _cache_write(rel, rows)
    return rows


def _reit_nav_expected(today: str) -> str:
    """最近一个「披露截止日已过」的半年末（纯函数）——今天理应能拿到的最新净值日期。

    公募 REIT 的份额净值半年披露一次：年报（12-31 净值）截止次年 3 月末，中期报告
    （06-30 净值）截止 8 月末；各再留 10 天等腾讯把新净值补上。"""
    y = int(today[:4])
    md = (int(today[5:7]), int(today[8:]))
    if md >= _REIT_INTERIM_READY:
        return f"{y}-06-30"
    if md >= _REIT_ANNUAL_READY:
        return f"{y - 1}-12-31"
    return f"{y - 1}-06-30"


def _reit_nav_meta(nav_date, established, today: str) -> dict:
    """净值种类与过期判定（纯函数）。

    nav_kind：issue_price = 净值日期等于成立日（份额净值尚未披露，这个「净值」是发行价，
    折溢价须读作「相对发行价」）；report = 来自中期/年度报告的份额净值；unknown = 没有
    净值日期。只知道净值日期时（行情板逐只补齐）：半年末 → report，否则推断为发行价并
    标 nav_kind_inferred=True（以 reit_profile 的成立日核对为准）。
    nav_stale：净值日早于 nav_expected（最近一个已过披露截止日的半年末）——该出的报告
    净值腾讯还没有。按披露截止判而不是「距今 N 天」：12-31 的净值要到次年 3 月末才更新，
    固定天数阈值每年 1~3 月和 8 月都会把正常状态误报成过期。新上市同理：成立日早于
    nav_expected 却还挂着发行价，说明首份定期报告的净值没跟上。"""
    expected = _reit_nav_expected(today)
    if not nav_date:
        return {"nav_kind": "unknown", "nav_kind_basis": "没有净值日期", "nav_kind_inferred": False,
                "nav_stale": None, "nav_age_days": None, "nav_expected": expected}
    if established and nav_date == established:
        kind, basis, inferred = "issue_price", "净值日期等于成立日：份额净值尚未披露，净值是发行价", False
    elif established:
        kind, basis, inferred = "report", "净值日期晚于成立日：来自定期报告的份额净值", False
    elif nav_date[5:] in _REIT_HALF_YEAR_ENDS:
        kind, basis, inferred = "report", "净值日期是半年末：来自中期/年度报告的份额净值", False
    else:
        kind, basis, inferred = "issue_price", "净值日期不是半年末：推断为发行价（新上市，以成立日核对为准）", True
    age = (date.fromisoformat(today) - date.fromisoformat(nav_date)).days
    return {"nav_kind": kind, "nav_kind_basis": basis, "nav_kind_inferred": inferred,
            "nav_stale": nav_date < expected, "nav_age_days": age, "nav_expected": expected}


def _reit_ttm_dividend(history: list[dict], as_of: str) -> dict:
    """近 12 个月（as_of 往前 365 天，含两端）除息的每份分红合计与次数（纯函数）。

    没有分红时 per_unit=None 而不是 0——REIT 一年分红 1~4 次且有跨期调配，0 会被误读成
    「不分红」。上市不足一年时窗口内只有上市后的分红，口径不完整，count 供调用方判断。"""
    start = (date.fromisoformat(as_of) - timedelta(days=_REIT_TTM_DAYS)).isoformat()
    rows = [d for d in history if start <= d["ex_date"] <= as_of]
    return {"window": [start, as_of], "count": len(rows),
            "per_unit": round(sum(d["per_unit"] for d in rows), 6) if rows else None,
            "ex_dates": [d["ex_date"] for d in rows]}


def _reit_dividends_by_year(history: list[dict]) -> dict:
    """分红史按除息日所在年份聚合 → {年: {per_unit, count}}（纯函数）。"""
    by_year = {}
    for d in history:
        item = by_year.setdefault(d["ex_date"][:4], {"per_unit": 0.0, "count": 0})
        item["per_unit"] = round(item["per_unit"] + d["per_unit"], 6)
        item["count"] += 1
    return by_year


def _reit_total_return(kline: list[dict], dividends: list[dict]) -> tuple[list[dict], list[dict]]:
    """不复权收盘 + 分红历史 → 分红再投资序列 [{date, nav}]（纯函数），并返回被跳过的分红。

    除息日当日收益率 = (收盘价 + 每份分红) ÷ 前一日收盘价 − 1，逐日连乘；首点 = 首日收盘价。
    腾讯前复权是减法复权，不能拿来做分红再投资，故用不复权收盘自算。除息日落在日线
    区间内却不在日线里（数据缺行）的分红跳过并返回，调用方标注——绝不悄悄漏掉一笔分红。"""
    div_by_date = {d["ex_date"]: d["per_unit"] for d in dividends}
    series, level, prev_close = [], None, None
    for row in kline:
        if level is None:
            level = row["close"]
        else:
            level = level * (row["close"] + div_by_date.get(row["date"], 0.0)) / prev_close
        series.append({"date": row["date"], "nav": level})
        prev_close = row["close"]
    skipped = []
    if series:
        kdates = {r["date"] for r in kline}
        skipped = [d for d in dividends
                   if series[0]["date"] < d["ex_date"] <= series[-1]["date"]
                   and d["ex_date"] not in kdates]
    return series, skipped


def _reit_window_metrics(series: list[dict], start: str) -> Optional[dict]:
    """以 start 之前最后一个完成 bar 为基点的区间指标（复用 fund_metrics，零网络）。

    基点取 start 前一 bar，年初至今才是「自上年末收盘起算」而非「自今年首个收盘起算」。
    序列起点不早于 start（上市晚于窗口起点）= 窗口不完整，返回 None 不硬造。"""
    i = sum(1 for r in series if r["date"] < start)
    if i < 1 or len(series) - i < 1:
        return None
    return fund_metrics({"nav_history": series[i - 1:]})


def _reit_cn10y_cache() -> Optional[dict]:
    """只读国债缓存里日期最新的 10 年期国债收益率（零请求）。

    扫 macro/tsy_*.json 里未过期（20h）的缓存，取日期最新一行；值域守卫 (0,15)。
    没有缓存返回 None——REIT 利差不为此发东财请求。"""
    best = None
    macro_dir = FUND_CACHE_DIR / "macro"
    if not macro_dir.is_dir():
        return None
    for p in sorted(macro_dir.glob("tsy_*.json")):
        if not re.fullmatch(r"tsy_\d+\.json", p.name):
            continue
        rows = _cache_read(f"macro/{p.name}", max_age_hours=SNAPSHOT_TTL_HOURS)
        if not isinstance(rows, list):
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            day = str(r.get("SOLAR_DATE") or r.get("date") or "")[:10]
            val = _reit_num(r.get("EMM00166466", r.get("cn_10y")))
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or val is None or not 0 < val < 15:
                continue
            if best is None or day > best["date"]:
                best = {"date": day, "cn_10y": val}
    return best


def _reit_fill_nav_dates(rows: list[dict], force_refresh: bool, pacer, today: str) -> None:
    """行情板逐只补净值日期（就地写回 rows；防封逻辑都在这里）。

    每只先读当日缓存，缺的才发 1 请求（_ReitPacer 慢节拍）。RiskControlError（403/429/
    配额背压）与 CircuitOpenError（熔断闸门拒绝——两者是平级异常，不是父子关系，须分别
    列出）立即上抛，绝不带着被拒的连接继续打；其余失败（网络/结构）连续
    _REIT_FANOUT_MAX_FAILS 次即中止——已取到的留在缓存，下次调用只补缺的（断点续跑）。
    中止后余下各行仍先用当日缓存填，实在没有的才标「未取」——缓存里有数据却报缺失是误导。
    force_refresh=True：净值历史绕过缓存重取（行情批本就不缓存，每次都是实时）。"""
    def _cached(code):
        if force_refresh:
            return None
        hist = _cache_read(f"reits/navhist/{code}.json", max_age_hours=_REIT_NAV_TTL_HOURS)
        return hist if isinstance(hist, dict) and isinstance(hist.get("history"), list) else None

    def _apply(row, hist):
        if not hist["history"]:
            row["notes"].append(hist.get("note") or "腾讯净值历史为空")
            return
        last = hist["history"][-1]
        meta = _reit_nav_meta(last["date"], None, today)
        row.update(nav_date=last["date"], nav_kind=meta["nav_kind"], nav_stale=meta["nav_stale"])
        if row["nav"] is None:
            row["nav"] = last["nav"]
        elif abs(row["nav"] - last["nav"]) > 0.00005:
            row["notes"].append(f"行情串净值 {row['nav']} 与净值历史末点 {last['nav']}"
                                f"（{last['date']}）不一致，折溢价按行情串净值")
        if meta["nav_kind_inferred"]:
            row["notes"].append(meta["nav_kind_basis"])

    fails, last_error = 0, None
    for i, row in enumerate(rows):
        hist = _cached(row["code"])
        if hist is None:
            try:
                hist = _reit_nav_history(row["code"], force_refresh=force_refresh, pacer=pacer)
            except (RiskControlError, CircuitOpenError):
                raise                             # 风控/熔断/背压：立即停（F6 契约）
            except _REIT_FANOUT_FAIL_ERRORS as e:
                fails += 1
                last_error = f"{type(e).__name__}: {str(e)[:60]}"
                row["notes"].append(f"净值历史取数失败（{type(e).__name__}）")
                if fails >= _REIT_FANOUT_MAX_FAILS:
                    for rest in rows[i + 1:]:
                        rest_hist = _cached(rest["code"])
                        if rest_hist is None:
                            rest["notes"].append(f"净值日期未取（补齐已中止：连续 {fails} 次失败，"
                                                 "已取到的在当日缓存，下次调用只补缺的）")
                        else:
                            _apply(rest, rest_hist)
                    print(f"[fund_data] 警告：REIT 净值日期补齐中止（连续 {fails} 次失败，"
                          f"最后错误 {last_error}），已完成 {i + 1 - fails}/{len(rows)} 只",
                          file=sys.stderr)
                    return
                continue
            fails = 0
        _apply(row, hist)


def _reit_premium_basis(row: dict) -> Optional[str]:
    """折溢价的比较基准文案；没有净值时为 None。"""
    if not row.get("nav"):
        return None
    if row.get("nav_kind") == "issue_price":
        return f"相对发行价 {row['nav']}（净值日期 {row.get('nav_date')}）"
    if row.get("nav_date"):
        return f"相对 {row['nav_date']} 份额净值 {row['nav']}"
    return "相对腾讯净值（净值日期未取：传 with_nav_date=True 或对单只用 reit_profile）"


def reit_board(force_refresh: bool = False, with_nav_date: bool = False) -> list[dict]:
    """全市场公募 REITs 行情板（腾讯 1 请求）。

    默认不逐只补净值日期（with_nav_date=False）。清单取 fund_list() 里类型为 Reits 的
    基金（东财缓存 30 天，冷时 1 请求）；腾讯行情串一次批量取回现价/涨跌/开高低/成交量额/
    换手/总市值 + 单位净值 + 折溢价率。按代码升序，每行 {code, name, price, change_pct,
    open, high, low, volume_lots, amount_wan, turnover_pct, mcap_yi, nav, premium_pct,
    nav_date, nav_kind, nav_stale, premium_basis, quote_time, notes}。
    with_nav_date=False（默认，防封第一）：只发 1 请求；折溢价是腾讯按最新可得净值算的，
    但不知道净值是哪天的（nav_kind="unknown"）——全市场初筛够用，入围标的再 reit_profile。
    with_nav_date=True：逐只补净值历史拿净值日期，1 请求/只（103 只按 1 发/秒约 2 分钟，
    当日缓存）。force_refresh=True：净值历史绕过当日缓存重取（行情批本就每次实时）。
    ⚠️ 净值是半年频：折溢价相对的是旧净值，必须连同 nav_date / nav_kind 一起解读。
    ⚠️ 行情无返回的代码（未上市/代码变更）保留在板内，价格为空并在 notes 说明。"""
    universe = sorted((f for f in fund_list()
                       if str(f.get("type") or "").strip().lower() == "reits"),
                      key=lambda f: str(f.get("code")))
    if not universe:
        raise RuntimeError("基金清单里没有类型为 Reits 的基金（2026-11 实测 103 只）"
                           "——疑似上游类型字段变更，拒绝静默空")
    codes = [str(f["code"]) for f in universe]
    pacer = _ReitPacer()
    quotes = {}
    for i in range(0, len(codes), _REIT_QUOTE_CHUNK):
        pacer.wait()
        url = "https://qt.gtimg.cn/q=" + ",".join(
            _tencent_prefix(c) for c in codes[i:i + _REIT_QUOTE_CHUNK])
        quotes.update(_parse_reit_quotes(_tencent_fund_get(url, referer=False).decode(
            "gbk", errors="replace")))
    if not quotes:
        raise RuntimeError(f"腾讯 REIT 行情批量返回 0 行（{len(codes)} 只）——疑似上游变更，拒绝静默空")
    _cbreak_success("tencent")
    rows = []
    for f in universe:
        q = quotes.get(str(f["code"])) or {}
        row = {"code": str(f["code"]), "name": q.get("name") or f.get("name"),
               "price": q.get("price"), "change_pct": q.get("change_pct"), "open": q.get("open"),
               "high": q.get("high"), "low": q.get("low"), "volume_lots": q.get("volume_lots"),
               "amount_wan": q.get("amount_wan"), "turnover_pct": q.get("turnover_pct"),
               "mcap_yi": q.get("mcap_yi"), "nav": q.get("nav"),
               "premium_pct": q.get("premium_pct"), "nav_date": None, "nav_kind": "unknown",
               "nav_stale": None,
               "premium_basis": None, "quote_time": q.get("quote_time"), "notes": []}
        if not q:
            row["notes"].append("腾讯行情无返回（未上市或代码变更）")
        rows.append(row)
    if with_nav_date:
        _reit_fill_nav_dates(rows, force_refresh, pacer,
                             datetime.now(_BJ_TZ).date().isoformat())
    for row in rows:
        row["premium_basis"] = _reit_premium_basis(row)
    return rows


def reit_profile(code, force_refresh: bool = False, macro: str = "cache") -> dict:
    """单只公募 REIT 基础画像（腾讯 4 请求）。

    当日缓存后 0~1 请求（实时概览 5 分钟微缓存）；零 PDF。东财 0~1 请求（仅基金清单
    冷加载）。返回 {code, name, as_of, basic, quote, nav, premium, dividends, returns,
    spread, notes, detail_prompt, requests}。
    basic：全称/简称/成立日/管理人/托管人/经理/最新规模（只暴露核实过的腾讯字段）。
    nav：最新净值、净值日期、nav_kind（issue_price=净值日期等于成立日，这个净值是发行价；
    report=定期报告份额净值）、nav_stale（净值日早于最近一个已过披露截止日的半年末：
    年报截止 3 月末、中报截止 8 月末，各留 10 天）、单位与累计净值历史。
    premium：折溢价率与 P/NAV，basis 写明相对哪天的净值。
    dividends：全部分红（除息日/每份）、累计、分年合计、近 12 个月每份分红与派息率
    （除息日落在近 365 天内的合计 ÷ 现价；没有分红返回 None 不返回 0）。
    returns：分红再投资口径（不复权收盘 + 除息日加回分红，逐日连乘后复用 fund_metrics）
    的年初至今、近一年、最大回撤、年化波动；窗口不完整（上市不足一年）返回 None 并说明。
    spread：近 12 个月派息率 − 10 年期国债，单位基点。macro="cache"（默认）只读国债缓存，
    没有缓存时 spread 里 cn_10y 为空并提示先调 treasury_yield_daily()；macro="fetch" 才发
    东财 1 请求（风控异常照常上抛）。
    requests：本次调用真实发出的请求数（按请求账本前后差值，含清单冷加载）。
    detail_prompt：交给用户后照着问的文案——确认后才进第二段 reit_report_text。
    ⚠️ 净值是半年频：折溢价相对的是旧净值，必须连同 nav_date / nav_kind 一起解读。"""
    if macro not in ("cache", "fetch"):
        raise ValueError(f"macro 只支持 'cache'（只读国债缓存，默认）或 'fetch'（允许东财 1 请求），"
                         f"收到 {macro!r}")
    c = _reit_code(code)
    tx0, tt0 = TENCENT_REQUEST_COUNT[0], TT_REQUEST_COUNT[0]   # 基线取在识别之前：清单冷加载也算数
    hit, basis = _is_reit(c)
    if not hit:
        raise ValueError(f"{c} 不是公募 REIT（依据：{basis}）。普通基金请走 fund_snapshot / fund_metrics")
    pre = _tencent_prefix(c)
    today = datetime.now(_BJ_TZ).date().isoformat()
    pacer, notes = _ReitPacer(), []
    zone = _reit_zone(pre, force_refresh, pacer)
    hist = _reit_nav_history(c, force_refresh, pacer)
    divs = _reit_dividends(pre, force_refresh, pacer)
    kline = _reit_kline_raw(pre, force_refresh, pacer)

    basic = zone["basic"]
    price = zone["quote"].get("price")
    nav_date, nav, acc_nav = zone["nav"]["nav_date"], zone["nav"]["nav"], zone["nav"]["acc_nav"]
    history = hist.get("history") or []
    if history:                                      # 净值取实时概览与净值历史缓存里较新的
        last = history[-1]
        if nav_date is None or last["date"] >= nav_date:
            if nav_date is not None and last["date"] > nav_date:
                notes.append(f"实时概览净值日期 {nav_date} 早于净值历史末点 "
                             f"{last['date']}，净值以净值历史为准")
            elif nav_date is not None and nav is not None and abs(last["nav"] - nav) > 0.00005:
                notes.append(f"实时概览与净值历史同为 {nav_date} 的净值不一致"
                             f"（{nav} 对 {last['nav']}），以净值历史为准")
            if last["acc_nav"] is not None:             # 历史行缺累计净值列时别把实时概览的值冲掉
                acc_nav = last["acc_nav"]
            elif acc_nav is not None and (nav_date is None or last["date"] > nav_date):
                notes.append(f"净值历史末点 {last['date']} 缺累计净值，acc_nav 仍是实时概览的值"
                             + (f"（{nav_date}）" if nav_date else "（概览未给净值日期）"))
            nav_date, nav = last["date"], last["nav"]
        else:
            notes.append(f"净值历史缓存末点 {history[-1]['date']} 早于实时概览净值日期 {nav_date}"
                         "（当日缓存尚未更新），净值取实时概览")
    meta = _reit_nav_meta(nav_date, basic.get("established"), today)

    premium = {"premium_pct": None, "p_nav": None, "basis": None, "nav_stale": meta["nav_stale"]}
    if price and nav:
        premium["premium_pct"] = round((price / nav - 1) * 100, 2)
        premium["p_nav"] = round(price / nav, 4)
        if meta["nav_kind"] != "issue_price":
            premium["basis"] = (f"相对 {nav_date} 份额净值 {nav}" if nav_date
                                else f"相对腾讯净值 {nav}（无净值日期）")
        elif meta["nav_kind_inferred"]:                 # 腾讯未给成立日：只能说「推断为发行价」
            premium["basis"] = f"相对发行价 {nav}（净值日期 {nav_date} 非半年末，推断为发行价）"
        else:
            premium["basis"] = f"相对发行价 {nav}（净值日期 {nav_date} = 成立日）"
        tx_premium = zone["quote"].get("premium_pct")
        if tx_premium is not None and abs(tx_premium - premium["premium_pct"]) > 0.05:
            notes.append(f"本地折溢价 {premium['premium_pct']}% 与腾讯折溢价字段 {tx_premium}% "
                         "不一致（行情时点不同或上游口径变化），以「现价 ÷ 净值 − 1」为准")
    else:
        notes.append("缺现价或净值，折溢价未计算")

    div_history = divs.get("history") or []
    total_div = round(sum(d["per_unit"] for d in div_history), 6)
    by_year = _reit_dividends_by_year(div_history)
    ttm = _reit_ttm_dividend(div_history, today)
    if ttm["per_unit"] is not None and price:
        ttm["yield_pct"] = round(ttm["per_unit"] / price * 100, 2)
    else:
        ttm["yield_pct"] = None
    declared = zone["totals"].get("dividend_per_unit")
    if div_history and declared is not None and abs(declared - total_div) > 0.0005:
        notes.append(f"分红历史合计 {total_div} 与腾讯「累计分红」字段 {declared} 不一致"
                     "（分红史可能不完整），请用报告里的实际分配金额核对")
    dividends = {"count": len(div_history), "total_per_unit": total_div if div_history else None,
                 "history": div_history, "by_year": by_year, "ttm": ttm}

    series, skipped = _reit_total_return(kline, div_history)
    returns = {"method": "分红再投资：不复权收盘价 + 除息日加回每份分红，逐日连乘（腾讯前复权是减法复权，不可直接用）",
               "first_bar": series[0]["date"] if series else None,
               "as_of": series[-1]["date"] if series else None,
               "ytd_pct": None, "ret_1y_pct": None, "max_drawdown_pct": None,
               "max_drawdown_window": None, "annualized_volatility_pct": None, "window": None,
               "skipped_dividends": skipped, "notes": []}
    if skipped:
        returns["notes"].append(f"有 {len(skipped)} 笔分红的除息日不在日线里"
                                f"（{', '.join(d['ex_date'] for d in skipped)}），收益未计入，请核对")
    if not kline:
        returns["notes"].append("腾讯日线返回空（今日才上市或上游异常，未缓存），收益指标未计算")
    elif len(series) < 2:
        returns["notes"].append("日线只有 1 根完成 bar（刚上市），收益指标未计算")
    else:
        m_ytd = _reit_window_metrics(series, f"{today[:4]}-01-01")
        if m_ytd:
            returns["ytd_pct"] = m_ytd["total_return_pct"]
        else:
            returns["notes"].append("今年还没有完成的日线（新年首个交易日之前，或今年才上市），"
                                    "年初至今未计算")
        m_1y = _reit_window_metrics(
            series, (date.fromisoformat(today) - timedelta(days=_REIT_TTM_DAYS)).isoformat())
        if m_1y:
            returns.update(ret_1y_pct=m_1y["total_return_pct"],
                           max_drawdown_pct=m_1y["max_drawdown_pct"],
                           max_drawdown_window=m_1y["max_drawdown_window"],
                           annualized_volatility_pct=m_1y["annualized_volatility_pct"],
                           window=m_1y["window"])
        else:
            returns["notes"].append("上市不足一年，近一年收益/回撤/波动未计算")

    if macro == "fetch" and _reit_cn10y_cache() is None:
        treasury_yield_daily(days=5)                  # 东财 1 请求；风控异常照常上抛
    cn10y = _reit_cn10y_cache()
    spread = None
    if ttm["yield_pct"] is not None:
        spread = {"ttm_yield_pct": ttm["yield_pct"],
                  "basis": f"近 12 个月每份分红 {ttm['per_unit']} ÷ 现价 {price}",
                  "cn_10y_pct": None, "cn_10y_date": None, "spread_bp": None}
        if cn10y:
            spread.update(cn_10y_pct=cn10y["cn_10y"], cn_10y_date=cn10y["date"],
                          spread_bp=round((ttm["yield_pct"] - cn10y["cn_10y"]) * 100, 1))
        else:
            spread["note"] = ("没有未过期的国债缓存：先调 treasury_yield_daily()（东财 1 发），"
                              "或传 macro='fetch'")
    return {
        "code": c, "name": basic.get("short_name"),
        "as_of": zone["quote"].get("time"),
        "basic": {**basic, "latest_scale_note": "腾讯「最新规模」，单位万元（180101 实测与中报净资产一致）"},
        "quote": zone["quote"],
        "nav": {"nav": nav, "nav_date": nav_date, "acc_nav": acc_nav, **meta, "history": history,
                "frequency": "半年频（公募 REIT 不按日披露份额净值）"},
        "premium": premium, "dividends": dividends, "returns": returns, "spread": spread,
        "notes": notes, "detail_prompt": _REIT_DETAIL_PROMPT.format(code=c),
        "requests": {"tencent": TENCENT_REQUEST_COUNT[0] - tx0,
                     "eastmoney": TT_REQUEST_COUNT[0] - tt0},
    }


def _reit_norm_pages(pages) -> list[int]:
    """pages 参数归一化：None → []；单个整数或整数可迭代 → 去重升序页码列表。"""
    if pages is None:
        return []
    try:
        items = [pages] if isinstance(pages, int) else list(pages)
    except TypeError:
        raise ValueError(f"pages 须为 ≥1 的页码整数或其列表，收到 {pages!r}") from None
    out = []
    for p in items:
        if isinstance(p, bool) or not isinstance(p, int) or p < 1:
            raise ValueError(f"pages 须为 ≥1 的页码整数或其列表，收到 {p!r}")
        if p not in out:
            out.append(p)
    return sorted(out)


def _reit_locate_keywords(pages_text: list[str], words) -> tuple[dict, list[int]]:
    """关键词页码定位（纯函数）：对每页去掉空白后的文本做子串查找，只返回页码。

    去空白是因为布局模式的表格会把词拆开（实测「平均租金」「可供分配金额」在表格里
    被空格隔断）。一页命中 ≥ _REIT_TOC_HIT_MIN 个不同词条标为疑似目录页。"""
    folded = ["".join(t.split()) for t in pages_text]
    hits = {w: [i + 1 for i, t in enumerate(folded) if w in t]
            for w in words if any(w in t for t in folded)}
    toc = [i + 1 for i, t in enumerate(folded)
           if sum(1 for w in words if w in t) >= _REIT_TOC_HIT_MIN]
    return hits, toc


def _reit_pdf_pages(pdf_path: str) -> tuple[list[str], list[str]]:
    """pypdf 布局模式逐页抽取文本 → (逐页文本, 警告)（用到才导入；缺失/过旧显式报错给升级提示）。

    布局模式保留表格的行列位置，AI 读多列财务表时不易串列（180101 季报 23 页 0.3 秒）。
    抽取在文件句柄存活期间做完，不把句柄带出函数。单页抽取失败按空页处理并记入警告
    （空白页无内容流时 pypdf 抛 KeyError——真实报告常夹空白页，一页坏不该让整份报告取不到
    文本）；全篇无文本由调用方报错。"""
    try:
        import pypdf
    except ImportError as e:
        raise RuntimeError("需要 pypdf（REIT 报告 PDF 转文本）：pip install pypdf") from e
    texts, warnings = [], []
    try:
        with open(pdf_path, "rb") as fh:
            pages = pypdf.PdfReader(fh).pages
            for i, page in enumerate(pages, start=1):
                try:
                    texts.append(page.extract_text(extraction_mode="layout") or "")
                    continue
                except TypeError as e:               # 只有「不认识 extraction_mode」才是版本过旧
                    if "extraction_mode" in str(e):
                        raise RuntimeError("pypdf 版本过旧，不支持布局模式抽取："
                                           "pip install -U pypdf") from e
                    why = type(e).__name__
                except Exception as e:
                    why = type(e).__name__
                texts.append("")
                warnings.append(f"第 {i} 页文本抽取失败（{why}），按空页处理")
    except OSError as e:
        raise RuntimeError(f"PDF 打开失败（{Path(pdf_path).name}）: "
                           f"{type(e).__name__} {str(e)[:80]}") from e
    except RuntimeError:
        raise
    except Exception as e:                           # pypdf 异常族庞杂（解析/加密…），统一归一上抛
        raise RuntimeError(f"PDF 解析失败（{Path(pdf_path).name}）: "
                           f"{type(e).__name__} {str(e)[:80]}") from e
    return texts, warnings


def _reit_clean_text(text: str) -> tuple[str, int]:
    """把 PDF 文字层里的孤立代理项换成 U+FFFD → (清理后文本, 替换个数)（纯函数）。

    孤立代理项（U+D800~U+DFFF 单独出现）不是合法字符，UTF-8 编不了——不清理，文本缓存
    一写就 UnicodeEncodeError，整份报告取不到。真实样本：508068 中报第 14~19 页「剩余
    租期」公式里的求和号，字体把它映射成数学字母符号，文字层只剩代理对的前一半。"""
    bad = [ch for ch in text if 0xD800 <= ord(ch) <= 0xDFFF]
    if not bad:
        return text, 0
    return "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text), len(bad)


def _reit_cross_check(code: str, report: dict) -> dict:
    """报告阅读的交叉核对值：只读当日腾讯缓存，零请求；缓存不存在就如实说没有。"""
    divs = _cache_read(f"reits/div/{code}.json", max_age_hours=_REIT_DIV_TTL_HOURS)
    navh = _cache_read(f"reits/navhist/{code}.json", max_age_hours=_REIT_NAV_TTL_HOURS)
    div_rows = (divs or {}).get("history") if isinstance(divs, dict) else None
    nav_rows = (navh or {}).get("history") if isinstance(navh, dict) else None
    if not div_rows and not nav_rows:
        return {"available": False,
                "note": "当日没有这只 REIT 的腾讯缓存（先调过 reit_profile 才有）；本函数不为此发请求"}
    out = {"available": True, "source": "当日腾讯缓存（reit_profile / reit_board 写入）"}
    if div_rows:
        out["dividends_by_year"] = _reit_dividends_by_year(div_rows)
        out["dividend_history"] = div_rows
    hints = []
    if report.get("kind") in ("Q1", "Q2", "Q3", "Q4", "interim", "annual") and report.get("year"):
        hints.append(f"报告里「本年累计/本年实际分配金额」（每份）↔ "
                     f"dividends_by_year[{report['year']}] 的 per_unit")
        hints.append("腾讯按除息日归年；报告若按权益登记日或收益分配所属期归年，跨年处会有差异，以报告为准")
    period_end = {"interim": "-06-30", "annual": "-12-31"}.get(report.get("kind"))
    if nav_rows and period_end and report.get("year"):
        want = f"{report['year']}{period_end}"           # 取报告期对应的那个点，而不是永远取末点
        point = next((p for p in nav_rows if p["date"] == want), None)
        if point:
            out["nav"] = {"nav": point["nav"], "nav_date": point["date"], "matched_period": True}
            hints.append(f"报告里「期末份额净值」↔ nav.nav（腾讯 {want} 的份额净值）")
        else:
            latest = nav_rows[-1]
            out["nav"] = {"nav": latest["nav"], "nav_date": latest["date"],
                          "matched_period": False}
            hints.append(f"腾讯净值历史里没有 {want} 的点，报告里的期末份额净值暂无法对照"
                         f"（腾讯最新一点是 {latest['date']}：新报告可能尚未更新，旧报告期可能早于"
                         "腾讯历史的起点）")
    if not div_rows:
        hints.append("当日没有分红史缓存，实际分配金额无法对照")
    out["hints"] = hints
    return out


def reit_report_text(code, kind=None, year=None, pages=None, keywords=None,
                     force_refresh: bool = False, max_chars: int = 60000) -> dict:
    """REIT 报告逐页文本（AI 直读，先问用户）。

    第二段：取一份定期报告的逐页文本交给 AI 直接读。库只负责取到文本——**不写任何
    抽取规则**：收入/净利润/现金流/可供分配金额/经营指标/持有人结构/评估值全部由 AI
    读原文，库不正则、不抽值、不缓存读数。四步：
      ① 清单  复用 fund_reports（东财 1 发，当日缓存）
      ② 转文本 pypdf 布局模式逐页抽取，存 reits/text/{code}_{报告ID}（JSON，无扩展名）——
         **先查这份文本缓存，命中就不下载 PDF**（省东财 1~2 发）；带抽取方式标记，换了自动重抽
      ③ 下载  仅文本缓存未命中时复用 fund_report_pdf（东财 1~2 发，存 FUND_CACHE_DIR/reits/pdf/）
      ④ 建索引 每页页码/字符数/开头预览 + 关键词页码定位
    kind：None=最新一份季报/中期/年报 | Q1~Q4 | interim（中期）| annual（年度）| other
      （评估报告、审计报告等未分类）| 其他字符串=标题包含该词（如 "评估"、"审计"）。
    year：报告年度（other 类与标题词选报时 year 列为空，按「年份列等于或标题含该年」筛）。
    pages：None=只返回索引不返回正文；给页码列表=附上这些页的正文（每页一段，带页码），
      合计超过 max_chars 报错并提示缩小范围。
    keywords：在默认 _REIT_KEYWORDS 之外追加的定位词；只返回页码，不返回任何数值。
    返回 {code, title, report_id, date, kind, year, page_count, total_chars, page_index,
    keyword_pages, toc_pages, cross_check, reading_tips, next}；给了 pages 时另带
    pages_text 与 chars，且不再带 page_index / keyword_pages（索引调用里已经拿过）。"""
    c = _reit_code(code)
    hit, basis = _is_reit(c)
    if not hit:
        raise ValueError(f"{c} 不是公募 REIT（依据：{basis}）；普通基金报告请走 fund_reports")
    allowed = (None, "Q1", "Q2", "Q3", "Q4", "interim", "annual", "other")
    if kind is not None and (not isinstance(kind, str) or not kind.strip()):
        raise ValueError("kind 需为 None / Q1~Q4 / interim / annual / other，或标题包含的词（如 '评估'）"
                         f"，收到 {kind!r}")
    kind = kind.strip() if isinstance(kind, str) else kind
    by_title = kind is not None and kind not in allowed          # 自由文本 → 标题包含
    if year is not None and (isinstance(year, bool) or not re.fullmatch(r"\d{4}", str(year))):
        raise ValueError(f"year 须为 4 位年份或 None，收到 {year!r}")
    year = int(year) if year is not None else None
    want_pages = _reit_norm_pages(pages)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
        raise ValueError(f"max_chars 须为正整数，收到 {max_chars!r}")
    try:
        extra = ([] if keywords is None
                 else [keywords] if isinstance(keywords, str) else list(keywords))
    except TypeError:
        raise ValueError(f"keywords 须为字符串或字符串列表，收到 {keywords!r}") from None
    words = list(_REIT_KEYWORDS)
    for w in extra:
        w = str(w).strip()
        if w and w not in words:
            words.append(w)

    listing = fund_reports(c, category="periodic", limit=100, force_refresh=force_refresh)
    reports = listing.get("reports") or []
    if kind is None:
        cands = [r for r in reports if r.get("kind") != "other"]
    elif by_title:
        cands = [r for r in reports if kind in (r.get("title") or "")]
    else:
        cands = [r for r in reports if r.get("kind") == kind]
    if year is not None:
        loose = kind == "other" or by_title                    # 这两类的 year 列为空，看标题
        cands = [r for r in cands if r.get("year") == year
                 or (loose and str(year) in (r.get("title") or ""))]
    if not cands:
        have = sorted({f"{r.get('kind')} {r.get('year')}" for r in reports if r.get("kind") != "other"})
        raise ValueError(f"{c} 没有匹配的定期报告（kind={kind!r} year={year!r}）；"
                         f"可用：{' / '.join(have) or '无'}")
    # 年报与年报摘要同归 annual：同类里优先不含「摘要」的；稳定排序保留源序新→旧
    cands = sorted(cands, key=lambda r: "摘要" in (r.get("title") or ""))
    report = cands[0]                                 # 源序新→旧：取最新一份

    text_rel = f"reits/text/{c}_{report.get('report_id')}"
    doc = None
    if not force_refresh:                             # 先查文本缓存：命中就不必下载 PDF
        cached = _cache_read(text_rel, max_age_hours=None)
        if (isinstance(cached, dict) and cached.get("extractor") == _REIT_EXTRACTOR
                and isinstance(cached.get("pages"), list) and cached["pages"]):
            doc = cached
    if doc is None:
        pdf_path = fund_report_pdf(report, dest_dir=FUND_CACHE_DIR / "reits" / "pdf",
                                   force_refresh=force_refresh)
        texts, extract_warnings = _reit_pdf_pages(pdf_path)
        cleaned = []
        for i, t in enumerate(texts, start=1):         # 落缓存前的边界：文字层非法字符在这里清掉
            t, n_bad = _reit_clean_text(t)
            cleaned.append(t)
            if n_bad:
                extract_warnings.append(f"第 {i} 页文字层含 {n_bad} 个无法编码的字符（孤立代理项），"
                                        "已替换为占位符")
        texts = cleaned
        if not any(t.strip() for t in texts):
            raise RuntimeError(f"报告 PDF 没有可提取的文本（{Path(pdf_path).name}）——疑似扫描件；"
                               + ("；".join(extract_warnings) or "无逐页警告"))
        doc = {"code": c, "report_id": report.get("report_id"), "title": report.get("title"),
               "extractor": _REIT_EXTRACTOR,
               "extracted_at": datetime.now(_BJ_TZ).isoformat(timespec="seconds"),
               "extract_warnings": extract_warnings,
               "pages": [{"page": i + 1, "chars": len(t), "preview": "".join(t.split())[:60],
                          "text": t} for i, t in enumerate(texts)]}
        _cache_write(text_rel, doc)
    hits, toc = _reit_locate_keywords([p["text"] for p in doc["pages"]], words)
    out = {"code": c, "title": report.get("title"), "report_id": report.get("report_id"),
           "date": report.get("date"), "kind": report.get("kind"), "year": report.get("year"),
           "page_count": len(doc["pages"]),
           "total_chars": sum(p["chars"] for p in doc["pages"]),
           "toc_pages": toc, "cross_check": _reit_cross_check(c, report),
           "reading_tips": list(_REIT_READING_TIPS)}
    if doc.get("extract_warnings"):
        out["extract_warnings"] = doc["extract_warnings"]
    if not want_pages:
        out["page_index"] = [{k: v for k, v in p.items() if k != "text"} for p in doc["pages"]]
        out["keyword_pages"] = hits
        out["next"] = (f"reit_report_text({c!r}, kind={report.get('kind')!r}, year={report.get('year')!r}, "
                       f"pages=[...]) 取正文；一次合计 ≤{max_chars} 字符")
        return out
    bad = [p for p in want_pages if p > len(doc["pages"])]
    if bad:
        raise ValueError(f"页码超出范围 {bad}（本报告共 {len(doc['pages'])} 页）")
    chosen = [doc["pages"][p - 1] for p in want_pages]
    total = sum(p["chars"] for p in chosen)
    if total > max_chars:
        detail = "，".join(f"第{p['page']}页 {p['chars']}" for p in chosen)
        raise ValueError(f"所选页合计 {total} 字符，超过上限 {max_chars}（{detail}）"
                         "——请缩小页码范围")
    out["pages_text"] = [{"page": p["page"], "text": p["text"]} for p in chosen]
    out["chars"] = total
    return out


# ── Sprint 2 默认激活：跨进程请求协调（真实时钟；测试可随时重建）────────
# 放在模块末尾确保策略引用的全部常量（TT/CNINFO/CSINDEX 间隔等）已定义。import 副作用
# 仅构建协调器与网关对象，首个网络请求才落盘状态（~/.fund_data_cache/coordinator）。
_reset_request_coordination()
```

## 使用示例

```python
# 流A：单基金深度诊断（常态 1 请求）
snap = fund_snapshot("161725")                    # 或传名称 "招商中证白酒"
m = fund_metrics(snap)                            # 复权口径全指标（含现申购费标注）
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
fund_crowding()[:10]                         # 季末后120天内1h短缓存；其后90天
fund_industry_allocation()                   # 19 门类行业配置（巨潮官方口径）

# V0.6 经理画像与专项组装层
rank_persistence(top=20)                     # 业绩持续性矩阵（0 请求长牛初筛）
fund_scale("161725")                         # 单基金规模（ENDNAV 精确值）
bond_plus_profile("007300")                  # 固收+画像（仓位带/回撤带/利率标注）
qdii_premium(["513100", "513500"])           # 场内溢价（净值滞后口径）
fund_purchase_limit("513100")                # 申购限额（快照 purchase_limit 同源；无批量接口）
fund_reports("交银趋势")                     # 定期报告清单（默认 periodic，1 请求当日缓存）
fund_report_pdf(rp["reports"][0])            # 报告 PDF 下载（EdgeOne 自动解，返回本地路径）
nav_divergence("161725")                     # 重仓探测（背离度→疑似调仓）
fund_manager("161725")                       # 基金码→现任经理画像（在管清单）
manager_rank(pages=1)                        # 经理排行 50 位（服务端仅姓名序）

# V0.8.0 中证官方全收益指数层
csindex_universe()                             # 全系列人民币基础指数（currency=None 全币种）
csindex_resolve_total_return("000300")        # 官方映射 → H00300
csindex_total_return_daily("000300", "20240101", "20241231")
csindex_sync_total_return(codes=["000300", "H30269"])  # 显式批量，可恢复

# V0.8.4 公募REITs层（两段式：腾讯基础数据 → 询问 → AI 直读报告）
board = reit_board()                               # 全市场行情板：1 请求（行情+净值+折溢价）
p = reit_profile("180101")                         # 单只画像：4 请求（当日缓存后 0~1）
print(p["detail_prompt"])                          # 先把画像交给用户，照这句询问是否读报告
idx = reit_report_text("180101")                   # 用户确认后：页码索引 + 关键词页码（不抽值）
txt = reit_report_text("180101", pages=idx["keyword_pages"]["可供分配金额"])  # 按页取正文，AI 直接读
```

**不覆盖（明确边界）**：账户持仓/交易下单（用 ttskill）、港基专项、盘中实时估值、基金经理历任切分（独立评测 skill 的 jjjl 通道）、单基金行业配置史（E2 已停供；全市场季度口径见 `fund_industry_allocation`）、货基万份收益（P15）、债券持仓明细 zqcc、FOF 底层基金明细（五源实测无结构化接口，坑P24）、经理姓名→ID 索引（坑P23）。
