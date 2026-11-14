# A/B/C 影子实验数据审计报告 — 2026-11-14

## 审计范围与执行边界

- 仓库：`zj03204112-hub/-`（沿用已配置的实验仓库）
- 分支：`experiment/draw-tail-score-consistency-2026-11-14`
- 本次分支 HEAD：`f16f08f70dd829868e133c01a76431b65e1a82c9`
- 实验规范：`model_validation/ABC_DRAW_GOAL_SCORE_EXPERIMENT_20261114.md`
- 本次执行方式：GitHub 仓库只读检查、SQL schema 检查、读取最近一次数据库审计及历史阻塞报告。
- 重要限制：本次没有在 GitHub Actions 中重新执行 SQLite 审计，也没有执行 Python 单元测试或回测；当前可用 GitHub 连接只读获取文件/运行记录，不能派发 workflow 或运行仓库 shell。以下数据库行数和完整性结果明确引用 2026-11-09 已保存的审计，不冒充 2026-11-14 新跑出的结果。

## 仓库与工作流状态

- 实验分支存在；生产 V5.1 文件未作修改。
- SQLite 数据库文件存在，仓库记录大小为 63,332,352 bytes。
- 已找到既有 V5.2 单元测试及 workflow，但候选测试 workflow 的 push 路径限定为 `main`；另有 `workflow_dispatch`。本次分支最近一次 Actions 记录是 `Football player data verification`，不是 A/B/C 实验测试。
- 本次没有 A/B/C 专项测试运行结果，不能标记“实验代码测试完成”。

## 市场数据审计

### A — 平局赔率变化

引用最近一次已保存的只读数据库审计：`model_validation/draw_odds_movement_coverage_20261109.json`。

- `sporttery_market`：51,622 行。
- 其中 `football_data_ah_close` 46,594 行、1,824 场；`asian_handicap_avg` 5,028 行、5,028 场。
- 两类记录的 `draw_value` 均为 0 行非空。
- 没有 bookmaker/provider 维度；`captured_at` 只有日期精度，没有供应商盘赔更新时间。
- 没有可用的同一比赛、同一公司、同一市场定义的两次赛前 1X2 平赔快照。

结论：平赔十进制赔率变化、去水平局概率变化均为 `BLOCKED_MISSING_MARKET_DATA`。不得把亚洲让球水位解释为平赔。

### B — 大小球与高进球尾部

引用最近一次已保存的阻塞报告：`model_validation/v5_2_goal_tail_backtest_blocked_report.json`。

- 历史预测样本审计：14 行。
- 经核验的赛前大小球盘口线：0 行。
- 经核验的赛前 Over 赔率：0 行。
- 经核验的赛前 Under 赔率：0 行。
- 同时具备盘口线及 Over/Under 双边价格的快照：0 行。
- 原因：历史记录缺少赛前大小球盘口和双边水位；赛果不能反推赛前市场。

结论：B 为 `BLOCKED_MISSING_MARKET_DATA`。现有候选代码仅支持半球盘的部分路径，四分之一盘处理虽有独立结算模块和测试文件，但本次未执行测试；不得据此声称已通过验证。六档 0/1/2/3/4/5+ 市场校准和高进球尾部回测未完成。

### C — 比分矩阵一致性

当前 SQL schema 中：
- `prediction_snapshot` 只保存比分1/比分2、半全场、置信度等字段，没有原始 H/D/A 三项概率、双方 lambda、完整比分矩阵、模型版本和 Git SHA。
- `sporttery_market` 没有大小球盘口、Over/Under 双边赔率、公司标识、原始 payload/hash。
- `provider_event_map` 有 provider/event_id，但每场每 provider 只保留一条映射及 observed_at；不足以单独证明盘口快照的供应商事件时间。
- `results` 与 `matches` 有稳定 match_id，但这不能补回缺失的赛前预测概率和矩阵。

结论：目前无法在同一不可变 match_id 样本上重建完整的赛前 lambda、联合比分矩阵和概率边际，因此 C 的端到端影子回测为 `BLOCKED_INSUFFICIENT_ORIGINAL_PREDICTION_SNAPSHOT`。可以在隔离分支先写纯数学单元测试，但本次没有执行。

## 样本与覆盖口径

最近一次数据库审计（2026-11-09）记录：
- 已完赛比赛：5,505。
- 有赛果的比赛：5,505；缺失赛果：0。
- 重复 match_id：0；孤立赛果：0。
- 有 provider_event_map 的比赛：710。
- prediction_snapshot：9,989。
- sporttery_market：51,622。

这些是历史数据库完整性统计，不等于 A/B/C 可用市场样本数。A 可比平赔时间序列为 0；B 可核验双边大小球快照为 0；C 具备原始概率/lambda/完整矩阵的快照数在当前 schema 中不可提供，必须先做原始数据字段级审计，不能将 9,989 条预测快照直接当作有效 C 样本。

本次未重新计算重复/未匹配/拒绝市场记录的逐条数量；现有审计只提供上述可确认统计，未提供完整市场记录拒绝原因明细。

## 前一轮失败结果（必须保留）

2026-11-09 首轮 1,002 场独立测试集：
- Baseline H/D/A Brier：0.606490。
- Baseline H/D/A Log Loss：1.013060。
- 平局召回率：1.91%；平均预测平局概率：25.634%。
- 总进球分档 Log Loss：1.413507。
- 平局单独偏置未改善召回率。
- 进球尾部乘数令总进球 Log Loss 恶化至 1.420996。
- 两项调整均未获接受，V5.1 未改变。

## 本周状态判定

| 状态 | 本周结论 |
|---|---|
| 数据审计完成 | 部分完成：仓库/schema/已有审计报告核读完成；本周未重新运行 SQLite 全量审计 |
| 实验代码测试完成 | 否：未执行单元测试 |
| 影子回测完成 | 否：A/B/C 市场数据与原始预测快照不足 |
| 通过验收 | 否 |
| 已获用户批准上线 | 否 |

A：`BLOCKED_MISSING_MARKET_DATA`（平赔赔率时间序列缺失）。  
B：`BLOCKED_MISSING_MARKET_DATA`（赛前大小球线及 Over/Under 水位缺失）。  
C：`BLOCKED_INSUFFICIENT_ORIGINAL_PREDICTION_SNAPSHOT`（缺少原始概率、lambda 和联合矩阵）。

## 下一步执行顺序

1. 在具备 Actions 派发权限的环境中，运行现有只读 `football-db-audit.yml`，重新生成当前 SQLite 完整性、关系和市场覆盖报告。
2. 检查并恢复真实历史 1X2 与 O/U 原始数据源：至少包含 bookmaker/provider、provider event ID、稳定 match_id、盘口线、双方价格、供应商时间、抓取时间、开球时间、raw payload/hash。
3. 单独审计预测归档：确认原始 H/D/A 概率、lambda_home/lambda_away、预测 cutoff、模型版本、Git SHA、第一/第二比分是否可恢复。
4. 数据覆盖达标前，只编写 A/B/C 的隔离单元测试和数据校验器；不得运行或发布市场回测结论。
5. 数据达标后再冻结唯一 match_id 样本，按 60/20/20 时间顺序划分，并用第二个未触碰时间窗复现。

## 保护约束

- V5.1 正式模型、正式预测入口及历史预测快照只读，不改动。
- 不自动合并 PR、不部署、不启用候选。
- 不用收盘/临场/赛后数据回填 T-12h 缺失值。
- 本报告不构成实验通过或上线建议。
