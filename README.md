# 足球让球模型数据库

最新复盘数据库：2026-09-19。

包含 2026 年模型预测、赛果与复盘数据。

<!-- model pipeline: calibration + chronological V3/V4 blend enabled -->

<!-- handicap-coverage-refresh-2026-09-21 -->

<!-- validation-scope-fixed-2026-09-21 -->

<!-- tiered-handicap-target-refresh -->


## 模型版本

- **V5.1**：历史基线，保持冻结，不覆盖历史预测。
- **V5.2 市场锚定进球分配**：候选模块已加入 `model_validation/v5_2_market_lambda.py`。计算顺序为大小球盘口估计总进球 λ，再结合去水欧赔分配主客队 λ，最后按比分联合概率输出候选比分及胜平负概率。
- V5.2 当前为代码候选版；测试工作流已配置，但尚未确认通过记录。由于实时预测入口和可核验赛前盘口历史接入尚未完成，不能声称自动预测已切换到 V5.2。正式启用前必须完成 CI、冻结样本对照回测和端到端接入验证。
- 规格：`model_validation/V5.2_MARKET_LAMBDA_SPEC.md`。
