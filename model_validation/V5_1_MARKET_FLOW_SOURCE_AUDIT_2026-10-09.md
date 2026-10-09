# V5.1 市场资金流向数据源审计

审计日期：2026-10-09  
实验分支：`experiment/v5-market-flow-source-audit-20261009`  
状态：`SOURCE_AUDIT_ONLY / NO_PRODUCTION_CHANGE`

## 1. 当前结论

暂时不能直接把“盘口资金流向”接入 V5.1 的 500 场回测，因为当前仓库已集成的竞彩固定奖金接口读取的是 `oddsHistory.hadList` / `oddsHistory.hhadList` 等赔率历史字段；现有采集代码没有抽取投注金额、成交量或资金流入字段。赔率变化不能冒充资金流向。

## 2. 数据源审计

| 来源 | 能提供什么 | 能否作为真实资金流向 | 成本/限制 | 当前决定 |
|---|---|---|---|---|
| 中国竞彩网 `getFixedBonusV1.qry` | 固定奖金/赔率历史；仓库现有采集器读取胜平负及让球赔率更新时间和数值 | **不能据当前接入字段认定为资金流** | 需实测接口历史覆盖；不应推断存在未核实字段 | 保留为赔率变化基线，不命名为资金流 |
| Betfair Historical Data BASIC | 官方历史交易所市场数据，价格更新频率约每分钟；无成交量 | **不能**；可用于免费赔率趋势代理实验 | 足球 BASIC 官方页面列为免费；需注册并下载历史文件 | 可作为低成本备用，不代替资金流 |
| Betfair Historical Data ADVANCED | 时间戳市场数据、价格阶梯及成交量 | **可以作为交易所成交活跃度/成交量信号**，但不是所有博彩公司投注额，也不能直接解释为“聪明钱” | 官方页面列价：足球 £69/月或 £699/年 | 暂不购买；先做免费样本与比赛匹配可行性检查 |
| Betfair Historical Data PRO | 更高频 tick 数据、完整价格阶梯及成交量 | 可以构造更细的交易量/价格变化特征 | 官方页面列价：足球 £230/月或 £2,299/年 | 当前不建议作为第一步，成本较高 |

官方参考：
- Betfair 历史数据与套餐： https://historicdata.betfair.com/ExchangeHistoricalStore/
- Betfair 历史数据服务说明： https://developer.betfair.com/historical-data-services-api/
- 官方成交量字段说明： https://support.developer.betfair.com/hc/en-us/articles/360002401937-How-is-traded-available-volume-represented-within-the-PRO-Historical-Data-files
- 仓库现有 Sporttery 赔率覆盖采集器：`data_pipeline/validate_sporttery_frozen_500.py`

## 3. 下一步的零付费执行方案

1. 不购买套餐，不更改生产模型。
2. 先获取/检查 Betfair 官方 BASIC 可用样本，确认文件格式、足球市场类型、时间戳和赛事标识是否可解析。BASIC 没有成交量，因此这一步只验证接入链路，不宣称测到了资金流。
3. 并行审计冻结 500 场的 Sporttery 赔率历史覆盖率，将其作为赔率变化基线。
4. 生成比赛匹配覆盖报告：总样本数、唯一匹配数、未匹配数、歧义数、赛前 12h/6h 可用记录数、时间戳有效率。
5. 只有在实际获得带成交量的历史数据后，才计算真实交易量特征并进入模型实验；若无成交量数据，标记 `FLOW_DATA_UNAVAILABLE`，禁止用赔率变化填充。

## 4. 后续实验输入字段约定

每条市场快照至少应保留：

- `match_key`：标准化比赛唯一键
- `source`：数据来源
- `market_type`：1X2 / Asian handicap / totals
- `captured_at_utc`：原始时间戳（UTC）
- `seconds_to_kickoff`：相对开球时间
- `selection`：主胜 / 平 / 客胜，或对应盘口选项
- `back_price` / `lay_price`：若数据源提供
- `matched_volume`：只有源数据明确提供成交量时填写；否则为 null
- `available_liquidity`：只有源数据明确提供可成交挂单量时填写；否则为 null
- `odds_implied_probability`：由赔率计算的隐含概率，需标记是否去水
- `data_quality_status`：OK / MISSING_VOLUME / BAD_TIMESTAMP / AMBIGUOUS_MATCH 等

资金相关变量与赔率变化变量必须分开存储，不得互相填补。

## 5. 防泄漏与模型准入

- 每场预测的特征截点固定为开球前 12 小时；若另做开球前 6 小时版本，必须作为独立实验。
- 所有市场快照必须满足 `captured_at <= kickoff - 12h`（或该实验明确指定的截点）。
- 按冻结样本的时间顺序使用 300 训练 / 100 校准 / 100 最终留出；最终留出集不参与特征选择或调参。
- 第一轮只比较原版 V5.1 与“V5.1 + 单一市场信号层”，不改 λ 生成、不改比分矩阵、不叠加多个新模块。
- 指标至少包括 90 分钟 H/D/A accuracy、平局 recall、Brier、Log Loss、Score1/Score2 命中率及净胜球 0/1/2/3+ 分布。
- 让球胜平负独立计算，且只由第一比分映射；第二比分不得参与让球映射。
- 若市场信号缺失，不得静默填零或使用赛后信息；需记录覆盖率并进行有/无数据分层评估。
- 新模块状态保持 `EXPERIMENT_ONLY`，只有最终留出集达到准入标准才讨论升级。

## 6. 本次未做事项

- 未购买任何数据套餐。
- 未声称已取得 500 场真实资金流数据。
- 未修改 V5.1 生产预测逻辑或生产参数。
- 未把赔率变动当成资金流向。
