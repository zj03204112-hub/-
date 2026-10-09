# V4–V5 进球引擎接入审计与联合验证准入记录
日期：2026-10-09
基准分支提交：03d6e32d41154729733ea4976d80bef618d0bb22
状态：BLOCKED_BEFORE_VALIDATION（先修复引擎可复现性与时间泄漏）

## 1. 审计结论

目前不能把 V4 的平局分类器与 V5 的比分输出直接拼接，也不能据此启动有效的联合验证。仓库中可查到 V5-SCORE v1.0 的规格和 500 场重建回测报告，但没有找到一份被明确标识为 V5-SCORE v1.0、可独立调用且与报告完全对应的基础进球引擎实现。

- `model_validation/V5-SCORE_MODEL_SPEC_v1.0.md` 明确说明：历史 V5 完整代码、精确权重及全部参数矩阵未完整恢复；v1.0 规格冻结的是已知结构和规则。
- `model_validation/500_match_V5_SCORE_reconstructed_backtest_report.md` 将历史赛果、时间衰减攻防率、主客场基准、对手攻防归一化、Poisson 与 Dixon–Coles 描述为 V5-SCORE v1.0 重建链路，并报告 500 场方向命中 51.0%。该报告也说明这不是原始 V5 代码的恢复版。
- `data_pipeline/v5_score_v1_1_market_calibrated_500.py` 和 `data_pipeline/v5_score_v1_1_market_calibrated.py` 实际调用 `compare_models.model_lambdas(..., "v4", ...)` 生成基础 λ，再生成 Poisson/DC 矩阵。因此，这两个 V5 市场校准脚本的基础进球分布实际来自当前 V4 λ 逻辑，不能用来证明它们接入了独立的 V5 v1.0 基础引擎。
- `data_pipeline/compare_models.py` 的 `model_lambdas` 具有 V3/V4 路径；其中 V4 使用动态攻防率、对手强度修正，再生成 λ 并套用 Dixon–Coles 矩阵。它不是单独的 V5 引擎入口。
- `data_pipeline/v4_draw_mechanism_v2_frozen_500.py` 的平局分类器特征也基于上述 V4 λ / 矩阵；它属于 V4 实验，不应被误认为 V5 进球引擎。

## 2. 现有 500 场 V4 v2 验证的时间切分风险

固定样本报告标示的日期范围为 2026-01-03 至 2026-09-26；V4 v2 脚本则固定使用 `CUTOFF = 2026-08-22T00:00:00`，将截止日前最多 4000 场作为分类器训练池，并将完整冻结 500 场作为测试集。

由于冻结 500 场中包含截止日之前的比赛，训练池可能包含部分测试样本的赛果；此外，全训练池估计的 away-lambda bias 也会使用这些截止日前的结果。故该实验的 500 场整体指标不能直接视为严格的无泄漏独立测试结果。应在下一轮验证中改成逐场 T-12h walk-forward，或使用明确晚于所有训练数据的纯时间留出集；不得只依赖一个固定 cutoff 来预测包含 cutoff 之前比赛的整份样本。

## 3. 正确的接入设计（不做输出拼接）

1. **先恢复可复现的独立 V5 基础引擎函数**：明确输入、数据截止时间、λ 生成公式、8×8 Poisson/DC 矩阵、归一化与版本锁；它必须能单独输出 `lambda_home`、`lambda_away`、完整比分矩阵、Top-2 比分和聚合 H/D/A 概率。
2. **保持 V4 原样作为对照臂**：使用 V4 自己的 λ 和矩阵，不让 V4 平局分类器覆盖比分矩阵或直接决定最终胜平负。
3. **同场同截止时间并行生成**：每场都使用同一个 T-12h 信息截止点，分别生成 V4 与 V5 两套 λ / 矩阵，并记录输入覆盖率与模型版本。
4. **先做基础引擎对照，再做联合臂**：至少比较 V4-only、V5-SCORE reconstructed v1.0-only。联合臂必须在训练/校准时间段内学习明确的 λ 层或概率矩阵层组合权重；不能把独立分类器标签和另一模型比分简单拼接。联合参数不得使用最终测试区间拟合。
5. **严格时间验证**：按开球时间排序，训练、校准、测试时间段不重叠；对每场预测只允许读取该场 T-12h 之前的数据。若仍用冻结 500 场，需确认每个 match_id 唯一、映射唯一，并按逐场时间滚动生成预测。
6. **统一评价指标**：90 分钟 H/D/A accuracy、Macro-F1、平局 precision/recall、Brier、LogLoss、Score1/Score2 精确命中、净胜球 0/1/2/3+ 分布；让球结果另按实际赛前盘口机械映射，第一比分独立计算，第二比分不参与让球映射。
7. **生产门槛**：所有候选版本均保留 `EXPERIMENT_ONLY`。只有严格时间留出集上联合臂相较 V4 与 V5 两个独立基线有稳定增益，且概率质量不恶化、平局召回不为零，才讨论升级。

## 4. 当前执行状态

- 已完成：审阅 V5-SCORE v1.0 规格、500 场重建报告、V4 `compare_models.py`、V4 v2 分类器及两份 V5 市场校准脚本。
- 已确认：当前 V5 市场校准脚本的基础 λ 调用指向 V4；基础 V5 引擎的独立可复现入口尚未核实。
- 已识别：V4 v2 固定 cutoff 与冻结 500 样本日期范围存在时间泄漏风险。
- **尚未运行 V4–V5 联合回测**：在独立 V5 引擎可复现、时间切分方案修正前，不生成或宣称联合验证准确率。
- 此记录只用于审计与实验准入，不修改 V4、V5-SCORE v1.0 或现有生产预测。

## 5. 代码参考

- V5 规格：`model_validation/V5-SCORE_MODEL_SPEC_v1.0.md`
- V5 重建报告：`model_validation/500_match_V5_SCORE_reconstructed_backtest_report.md`
- 当前 V4 λ / 矩阵：`data_pipeline/compare_models.py`
- V4 v2 平局实验：`data_pipeline/v4_draw_mechanism_v2_frozen_500.py`
- V5 市场校准脚本：`data_pipeline/v5_score_v1_1_market_calibrated_500.py`、`data_pipeline/v5_score_v1_1_market_calibrated.py`
