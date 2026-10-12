# 2026-10-12：冻结500场分类路径与官方竞彩盘口核验进展

## 保护边界
- 仅在 `feature/v5.2-classification-data-audit` 分支。
- V5.1 未修改；不合并实验分支。
- `predictions/2026-10-11/recovered_original_predictions_004-028.md` 保持只读。
- `model_validation/500_match_V4_frozen_predictions.csv` 保持只读。
- 本记录不是模型修复成功声明。

## 分类路径的代码级结论（现有冻结CSV可证明的范围）
冻结文件中的 `pred_result` 与对 `p_home/p_draw/p_away` 执行最大概率分类一致，500行概率和也正常（误差阈值2e-5内）。因此，现有证据不支持“胜平负标签映射把平局改成胜/负”这一假设。

500行中 `p_draw` 从未同时高于 `p_home` 和 `p_away`，但它在9行高于主胜概率、18行高于客胜概率。分类器并非明确禁止平局；它只是每次都被至少一侧的非平局概率压过。当前首要嫌疑因此落在分类前的概率生成/比分分布（特别是λ和两侧强弱差对H/A概率的影响），而不是最后一行的argmax或标签映射。

冻结CSV没有保存每场 `lambda_home/lambda_away` 和完整比分概率矩阵，单凭CSV无法严谨地区分：λ总体偏高、主客λ差/实力调整、或比分矩阵/Dixon-Coles聚合问题。下一步必须只读重放并输出每场T−12h主客λ、H/D/A概率、净胜球0/1/2/3+概率、分类分差，并检查矩阵归一化及低比分修正。此前的平局乘数压力测试已因Brier/LogLoss恶化被拒绝，不能用加阈值冒充修复。

## 官方竞彩让球核验
004—028恢复档案中的让球值仍是对话恢复值，不是已保存的官方接口原始响应。028第一比分2–1、主队-1，机械映射为让平，而原档案记录让胜；原始档案不覆盖。

新增独立脚本尝试通过官方 `getUniformMatchResultV1.qry` 和 `getFixedBonusV1.qry` 匹配赛事ID、读取 `oddsHistory.hhadList.goalLine` 与原始字段，只写新审计JSON。**脚本实际运行并保存响应前，不得声称25场官方盘口核验已完成。** 接口即使返回历史列表，也必须确认时间戳能否证明原预测时点（约北京时间16:38）的盘口；否则只能确认历史列表存在，不能确认当时快照。

## 新增只读工具
- `data_pipeline/diagnose_frozen500_classification_path.py`：重算冻结CSV的argmax一致性、平局排序、混淆矩阵与概率差。
- `data_pipeline/audit_official_sporttery_handicap_004_028.py`：通过官方接口逐场匹配并保留hhad原始字段。

## 验收门槛
- 分类：只读重放保存500行λ/完整概率分布；候选修正独立时间留出，Brier与LogLoss均不恶化。
- 盘口：25/25唯一匹配官方赛事ID，逐场保留原始goalLine和历史时间字段；未能证明预测时点的行标记未核验，不以亚洲盘替代。
- 未达到门槛前：不合并、不调整V5.1、不宣称修复成功。
