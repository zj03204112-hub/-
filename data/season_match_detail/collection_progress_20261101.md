# 七大联赛数据采集与核验进度（2026-11-01）

## 本次提交内容
- 韩国 K League 1：对仓库中现有 80 行修复日期列；将原 UTC 时间保留为 `Time_UTC`，并转换为韩国当地时间写入 `Time`。日期范围为 2026-02-28 至 2026-05-12。结构检查：80 行、无无效日期/时间、无重复“日期+主队+客队”键。
- 西甲 2025–26：新增 380 场赛果/半场比分/首发 XI/裁判数据。380/380 场两队首发各 11 人，0 重复比赛键，0 XI 内重复姓名。
- 德甲 2025–26：新增 306 场赛果/半场比分/首发 XI/裁判数据。306/306 场两队首发各 11 人，0 重复比赛键，0 XI 内重复姓名。
- 意甲 2025–26：新增 380 场赛果/半场比分/首发 XI/裁判数据。380/380 场两队首发各 11 人，0 重复比赛键，0 XI 内重复姓名。
- 法甲 2025–26：新增 306 场赛果/半场比分数据。来源不包含首发 XI，因此首发核验仍未完成；有 1 场半场比分缺失，保持为空，没有用全场比分补填。
- 日本 J1 2026/27：映射 CSV 共 126 场，日期、主客队、比分与官方赛程逐场对照匹配。CSV 中官方赛果、首发及实际出场状态字段均为 verified（126/126），且 126 行均有逐场核验说明；但本轮没有重新打开全部 126 个比赛报告，当前仅重新打开核实了 2026-09-19 川崎Ｆ 3-3 鹿岛及 2026-10-31 柏 4-0 浦和的官方数据页。针对本轮核验请求，额外审计发现并修复 50 行 `source_record_url` 误填为来源名称的问题：改为同一行已记录的官方比赛详情 URL；该修复提交 `c968004853d398a5130ba4559c126c7b00a50f45`。已补齐 2026-09-19 川崎Ｆ 3-3 鹿岛的 `provider_event_id=34596`，来源为 J.League Data Site 官方比赛记录。
- 日本 J1 2025：检查了 OpenFootball JSON 候选源；380 场赛程中仅 136 场有全场比分，244 场缺比分，故未把它作为完整赛果数据集写入。

- 2025–26 五大欧洲联赛：已从仓库现有赛果源生成稳定内部 match_id 映射底表，新增英超 380、西甲 380、德甲 306、意甲 380、法甲 306，共 1,752 场。五份映射底表均按“联赛+赛季+日期+主队+客队”生成确定性内部 ID；本次结构检查均无重复日期/主客队键、无缺失全场比分。供应商 event ID、官方比赛页及逐场官方核验状态均明确保留为 pending，不将来源数据冒充官方已核验。
  - `data/season_match_detail/premier-league_2025_26_match_id_mapping.csv`
  - `data/season_match_detail/laliga_2025_26_match_id_mapping.csv`
  - `data/season_match_detail/bundesliga_2025_26_match_id_mapping.csv`
  - `data/season_match_detail/seriea_2025_26_match_id_mapping.csv`
  - `data/season_match_detail/ligue1_2025_26_match_id_mapping.csv`
- 英超映射底表的来源数据没有首发 XI/实际出场；西甲、德甲、意甲的 OpenFootball 源提供首发名单但尚未逐场与官方比赛报告二次核对；法甲源不提供首发 XI。五大联赛的实际出场记录均尚未完成逐场核验。

## 84 场旧待核验记录的修复说明（2026-11-01）
- 重新读取 J1 2026 映射 CSV 后，记录共 126 场；官方赛果、首发、实际出场三项状态字段均为 `verified`（各 126/126），因此当前文件中不存在 84 条仍标记为待核验的记录。此前“84 场待核验”是旧进度口径，不应继续当作当前未完成数量。
- 本轮发现 50 行 `source_record_url` 填入了来源名称而非 URL，已修复为对应行已有的官方比赛详情 URL。该修复不等同于本轮重新打开并独立复核这 50 场的全部比赛事件。
- 当前映射键重复数为 0，126 场均有官方比赛 URL 和供应商赛事 ID；赛果底表已与映射表按日期+主客队对齐为 126/126。已从 J.League Data Site 官方 2026 年 9 月赛程列表进入该场官方记录，确认 `match_card_id=34596` 并补入映射 CSV。另发现 `data/auto_results/J1_League_2026_27.csv` 原为 125 行，漏掉 2026-10-31 柏 4-0 浦和（match_card_id=34644），已补入，赛果底表现为 126 行。
- 本轮相关提交：CSV URL 修复 `c968004853d398a5130ba4559c126c7b00a50f45`。此处明确区分“现有核验状态字段”与“本轮重新独立核验”，避免将两者混为一谈。

## 当前赛季 CSV 现有记录数（仅为采集行数，不代表全部已验收）
| 联赛 | 行数 | 状态 |
|---|---:|---|
| 英超 | 80 | 结构检查通过，逐场官方复核未完成 |
| 西甲 | 99 | 结构检查通过，逐场官方复核未完成 |
| 德甲 | 63 | 结构检查通过，逐场官方复核未完成 |
| 意甲 | 90 | 结构检查通过，逐场官方复核未完成 |
| 法甲 | 72 | 结构检查通过，逐场官方复核未完成 |
| 日本 J1 | 126 | 映射表官方赛果/首发/出场状态字段 126/126；本轮打开 126/126 场官方报告（121 个 Data Site 页面 + 5 个官网备用页）；供应商赛事 ID 126/126；赛果底表已补齐至 126 行 |
| 韩国 K League 1 | 80 | 日期/时间修复已放在本分支，尚未合并到 main |

## 首发与真实出场记录
- 英超 2025–26：仓库既有的独立首发审计报告记载 380/380 场首发 XI 完整、0 场不完整、0 场未匹配；这是固定第三方数据源的审计结果，仍需保留来源与版本追溯。
- 西甲、德甲、意甲 2025–26：本次导入的 OpenFootball full-text 来源均提供两队首发，程序校验 11 人/队；这是结构和来源数据核验，不等于已逐名与各联赛官方比赛报告二次比对。
- 法甲、K League：首发 XI 与真实出场记录仍需继续采集。J1 2026/27 的 126 场官方详情页均已打开并有首发/替补/换人记录可查，但当前映射 CSV 只存状态、ID 和链接，没有把每场双方 11 人、替补名单及换人事件保存成独立的球员明细表；因此“页面已核验”不等于“球员明细数据层已完成”。
- “真实出场记录”与“首发名单”分开统计；不能将首发名单直接当作全场出场球员清单。

## 尚未完成
1. 日本 J1 2025 赛季：OpenFootball 候选源 380 场中仅 136 场有全场比分，244 场缺比分；需另找可靠赛果源补齐，并继续采集首发和实际出场记录。J1 2026/27 的 126 场已完成官方详情页复开与赛果/映射核对，但逐场球员名单明细仍未落成独立数据表。
2. 韩国 K League 1 2025 全赛季赛果、首发及实际出场记录。
3. 法甲 306 场首发及实际出场记录。
4. 六个非英超联赛的稳定内部 match_id、供应商赛事 ID 映射和逐场赛果二次复核。
5. 各联赛完成率的最终验收统计。

## 数据源
- 西甲：https://raw.githubusercontent.com/openfootball/espana/master/2025-26/1-liga-full.txt
- 德甲：https://raw.githubusercontent.com/openfootball/deutschland/master/2025-26/1-bundesliga-full.txt
- 意甲：https://raw.githubusercontent.com/openfootball/italy/master/2025-26/1-seriea-full.txt
- 法甲：https://raw.githubusercontent.com/openfootball/europe/master/france/2025-26_fr1.txt
- 日本 J1 2025 候选：https://raw.githubusercontent.com/openfootball/football.json/master/2025/jp.1.json
- 韩国 K League 1 2025 赛果来源索引：https://fbref.com/en/comps/55/2025/schedule/2025-K-League-1-Scores-and-Fixtures

- 第二批扩展联赛（2025–26，OpenFootball football.json 来源）已建立稳定内部 match_id 映射底表，共 2,233 条：英冠 557、葡超 306、荷甲 306、比甲 240、奥地利 Bundesliga 132、苏超 198、土超 306、希腊 Super League 182。底表均无重复“日期+主队+客队”键、无缺失日期。来源当前包含已赛和未来/未出结果赛程，因此“有比分”数量与总行数分开记录；官方逐场核验、供应商 event ID、首发和实际出场仍未完成，未标记为已核验。
  - `data/season_match_detail/championship_2025_26_match_id_mapping.csv`（557 行；521 行带比分）
  - `data/season_match_detail/primeira-liga_2025_26_match_id_mapping.csv`（306 行；287 行带比分）
  - `data/season_match_detail/eredivisie_2025_26_match_id_mapping.csv`（306 行；295 行带比分）
  - `data/season_match_detail/belgian-pro-league_2025_26_match_id_mapping.csv`（240 行；104 行带比分）
  - `data/season_match_detail/austrian-bundesliga_2025_26_match_id_mapping.csv`（132 行；67 行带比分）
  - `data/season_match_detail/scottish-premiership_2025_26_match_id_mapping.csv`（198 行；58 行带比分）
  - `data/season_match_detail/turkish-super-lig_2025_26_match_id_mapping.csv`（306 行；90 行带比分）
  - `data/season_match_detail/greek-super-league_2025_26_match_id_mapping.csv`（182 行；61 行带比分）


## 本轮追加修复（2026-11-01）
- 官方 J.League Data Site 赛程列表中，2026-09-19 川崎Ｆ 3-3 鹿岛对应官方比赛记录 `match_card_id=34596`。已将该 ID 和官方 Data Site 详情 URL 写入 `j1_2026_match_id_mapping.csv`，不再留空。
- 对比 `j1_2026_match_id_mapping.csv` 与 `data/auto_results/J1_League_2026_27.csv` 时发现后者少 1 场：2026-10-31 柏 4-0 浦和。官方记录 `match_card_id=34644` 确认日期、比分、首发、替补及换人事件；已补入赛果 CSV。两表现在均为 126 场，按日期+主客队键 126/126 对齐。
- 官方数据页复核证据：川崎Ｆ 3-3 鹿岛的 `match_card_id=34596`，以及柏 4-0 浦和的 `match_card_id=34644`。本轮已重新打开全部 126 个官方比赛详情页；由于没有独立保存的逐场球员名单明细表，尚未完成“存储名单 vs 官方名单”的程序化逐名差分。
- 韩国 K League 1 文件 `data/auto_results/K_League_1_2026_27.csv` 当前 80 行，`Date` 均为 ISO 日期，`Time` 与 `Time_UTC` 均为 HH:MM 格式；样例及全行格式检查无异常。已对 80/80 行完成 UTC+9 算术复算：每行 `Time` 均比 `Time_UTC` 快 540 分钟（按 24 小时取模），日期和时间格式检查 80/80 通过。此项验证的是字段之间的时区算术一致性，不等于逐场重新核对官方开球时间。

- 首发/替补详情复核进度更新：126/126 官方详情页均可通过 Data Site 或 jleague.jp 官方页访问。5 个 Data Site 页面无法由当前浏览器直接渲染，已在映射表中为 `official_match_url` 添加官网备用地址，原 `source_record_url` 保留 Data Site provider 记录。仍需补充独立的逐场球员名单明细表，才能自动检测重复球员、错配和姓名差异。
