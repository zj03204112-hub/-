# J1 2026 xGA 来源检索与逐场核验 — 2026-11-02

## 结果摘要

- 比赛记录：130；唯一 internal_match_id：130。
- 当前有双方 xGA 数值：50 场。
- xGA 数值严格镜像对手 xG：50 场。
- xGA 缺失：80 场。
- 能证明供应商独立展示比赛级 xGA：**0 场**。
- 本次没有修改原始 xG/xGA 数值，也没有估算缺失值。

## 候选来源检索结论

1. [OddAlerts J-League xG](https://www.oddalerts.com/xg/j-league)：公开页面可见球队赛季 xGA 汇总和近期比赛双方 xG；现有 50 条记录中的 xGA 数值严格等于对手 xG。未发现独立比赛级 xGA 字段证据。
2. [FootyStats J1 xG](https://footystats.org/japan/j1-league/xg)：公开页面可见球队赛季 xGA 汇总和近期比赛双方 xG；尚无证据证明逐场独立 xGA 字段可免费获取。
3. [VJStats](https://vjstats.com/)：已使用的公开比赛页面提供双方比赛级 xG；未发现独立 xGA 字段证据。
4. [Tifolab](https://www.tifolab.com/)：已使用的公开比赛页面提供比赛级 xG；未发现独立 xGA 字段证据。
5. [ScoutingStats](https://scoutingstats.ai/)：已使用的公开比赛页面提供比赛级 xG；未发现独立 xGA 字段证据。
6. [FotMob](https://www.fotmob.com/)：可免费查看部分比赛统计，但本轮未验证其日职比赛页存在独立 xGA 字段。

## 字段语义规则

- 只有当来源页面或响应明确给出比赛级 xGA 字段，且字段含义、比赛身份和主客方向均可核验时，才标记为独立来源 xGA。
- 只显示双方 xG 的来源，不作为独立 xGA 证据。
- 若保存对手 xG 的等价值，使用 `opponent_xg_equivalent`，不得冒充独立来源。
- 缺失值保留空白，不用赛季均值、比分或模型估计补齐。
- 不同供应商的 xG 模型不互相平均。

## 逐场核验文件

同目录的 `j1_2026_xga_match_level_source_audit.csv` 含 130 场逐行数据：比赛 ID、日期、主客队、比分、双方 xG、原 xGA、来源名称/URL、既有复核备注、独立 xGA 证据标记和字段解释。

逐场比赛身份核验依据已归档的来源 URL、原始记录与既有复核备注。此报告不声称 130 个页面都在本轮重新抓取；对无法证明独立 xGA 的行，不作独立来源认证。

## 最终结论

本轮公开来源检索未能证明存在完全免费、可追溯、对这 130 场逐场明确提供独立 xGA 字段的数据源。因此，独立 xGA 覆盖率继续记为 0/130；50 场作为对手 xG 等价值，80 场保持缺失。
