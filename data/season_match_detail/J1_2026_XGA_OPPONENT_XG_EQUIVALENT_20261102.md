# 日职 2026/27 对手 xG 等价值数据层说明 — 2026-11-02

## 范围与字段语义

本文件由 `j1_2026_match_xg_free.csv` 的 130 条比赛映射记录生成。它不替代原始 xG 文件，也不把对手 xG 等价值冒充独立供应商 xGA。

- 公式：主队 xGA 等价值 = 同场客队 xG；客队 xGA 等价值 = 同场主队 xG。
- `xga_independent_source_verified=false`：当前没有证据证明供应商对该场单独提供了 xGA 字段。
- `model_calibration_eligible=true` 仅用于来源状态为 `verified_from_current_fetch` 的记录，且仅代表 provider-specific xG / 对手 xG 等价值可用；不代表官方 xG，也不代表独立 xGA。
- `unverified_legacy_value` 行保留原始值供追溯，但不纳入正式模型校准。

## 核验统计

- 总记录：130 场，130 个唯一 internal_match_id。
- 双方 xG 数值非空：130/130。
- 当前抓取重新核验：50 场。
- 尚未重新核验的历史数值：80 场。
- 独立来源明确提供比赛级 xGA：0/130。
- 等价值计算仅做同场对手 xG 字段映射，不使用比分、球队均值、插补或跨供应商平均。

## 原始来源字段

保留原始 `home_xg`、`away_xg`、来源名称、来源 URL、来源模型说明及 `source_validation_status`。所有记录都带 `eligibility_reason`，以防止未核验历史值被误用于正式训练。

