# 五个新增联赛：独立扩库、样本与映射

此目录是与生产数据库 `football_model_database.sqlite` 隔离的数据域。当前不会将这些比赛合并进旧数据库，也不会触碰现有固定 500 场验证样本或 V5.1 生产参数。

## 范围

| 联赛 | 当前赛季 | 上赛季 | 赛季口径 |
|---|---|---|---|
| 英冠 | 2026–27 | 2025–26 | 欧洲联赛赛季 |
| 欧冠 | 2026–27 | 2025–26 | UEFA Champions League 赛季 |
| 葡超 | 2026–27 | 2025–26 | 欧洲联赛赛季 |
| 挪超 | 2026 | 2025 | 日历年赛季 |
| 芬超 | 2026 | 2025 | 日历年赛季 |

## 文件与独立表

- `expanded_leagues.sqlite`: 独立数据库。
- `expanded_matches.csv`: 赛事/赛果主表导出；包含赛程和已完赛赛事。
- `expanded_match_mapping.csv`: 独立比赛映射，使用 `competition_key + season_label + provider_event_id` 的精确映射，不依赖队名模糊匹配。
- `expanded_team_mapping.csv`: 每个联赛/赛季的来源球队 ID 与标准化球队键映射。
- `expanded_samples.csv`: 只包含已完赛、有最终比分的样本；每个联赛、每个赛季分别按时间排序切分为 60% train、20% calibration、20% holdout。
- `ingestion_report.json`: 每个联赛/赛季实际拉取数、已完赛数、映射异常及错误。

数据库表：`expanded_matches`、`expanded_match_mapping`、`expanded_team_mapping`、`expanded_samples`、`expanded_ingestion_runs`。

## 数据来源与限制

当前适配 ESPN 公共赛事记分牌接口，按各联赛独立 slug 拉取月度赛程/赛果；欧冠仅从欧冠赛事接口读取，不从联赛赛事里推断。每个来源事件使用 `XL:<competition>:<season>:<event_id>` 作为独立主键，避免与旧数据库 match_id 冲突。

该流程会保留真实来源 ID、来源链接和抓取时间。某联赛/赛季返回 0 场、接口失败或出现未映射记录时，报告会标记 `PARTIAL_OR_FAILED`，workflow 不会把部分结果当作成功数据提交。数据覆盖必须以 `ingestion_report.json` 的实测计数为准，而非仅凭代码或表结构判断。

## 验收原则

1. 10 个联赛-赛季分组都必须成功返回数据。
2. 所有事件均有稳定的来源赛事 ID 和唯一独立 match ID。
3. 映射表必须覆盖所有赛事；未映射数必须为 0。
4. 样本只收已完赛且有最终比分的比赛。
5. 每个联赛/赛季独立切分样本，不跨联赛、不跨赛季混切。
6. 不写入旧数据库，不修改模型，不将新联赛样本混入既有 500 场测试集。
