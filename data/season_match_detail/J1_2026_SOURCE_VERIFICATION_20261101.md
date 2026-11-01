# J1 2026/27 数据源搜集与核验记录

核验日期：2026-11-01
仓库分支：audit/stable-match-id-mapping-20261101

## 一、现有赛事映射表机器核验
对 `data/season_match_detail/j1_2026_match_id_mapping.csv` 当前版本逐行统计：
- 记录数：126
- `internal_match_id` 重复：0
- `provider_event_id` 非空：126/126
- `official_match_url` 为有效 HTTP(S) URL：126/126
- `official_result_check_status=verified`：126/126
- 日期匹配标志 true：126/126
- 主队匹配标志 true：126/126
- 客队匹配标志 true：126/126
- 比分匹配标志 true：126/126
- lineup_audit_status=verified：126/126
- appearance_audit_status=verified：126/126

重要解释：以上证明的是当前映射表的记录和状态字段一致，不证明 126 场的逐球员明细已经导出到独立 CSV；状态字段不能替代球员名单、球员 ID、分钟、换人事件及 xG 原始值。

## 二、已核验数据源能力
1. J.League Data Site 官方赛程/结果查询页列出 2026/27 赛季，可用于官方赛果及赛事信息核对：
   https://data.j-league.or.jp/SFMS01/
2. J.LEAGUE 官方比赛报告提供首发 XI、替补名单和比赛进程/换人事件。可复核示例：
   https://www.jleague.jp/en/match/j1/2026/090219/lineup/
   https://www.jleague.jp/en/match/j1/2026/091205/
3. SportMonks J1 League API 页面展示 fixture ID、稳定 player/team ID、首发/替补、赛事事件和球队 xG 示例；其官方 xG coverage 表列出 J-League（league ID 968）。文档也明确指出 xG 覆盖依套餐而异，必须使用实际订阅/接口响应确认每场数据：
   https://www.sportmonks.com/football-api/j-league-api/
   https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/expected/coverage
4. API-Football 免费套餐对 2026 赛季的限制已在失败的 Actions 日志中直接确认；不得继续把该免费套餐当作 J1 2026 的可用主数据源。

## 三、目前尚未完成的数据采集
当前分支尚未出现可核验的以下实际产物：
- `j1_2026_players.csv`
- `j1_2026_substitutions.csv`
- `j1_2026_phase1_audit.json`

所以当前无法给出真实的首发球员 ID 覆盖率、替补/换人事件覆盖率、实际出场分钟覆盖率和 xG/xGA 覆盖率。不能将这些指标填成 100%，也不能把官方页面“可查”当作结构化数据“已落盘”。

## 四、完整性规则
- 官方页面作为赛果、首发、替补、换人事件的核验基准。
- xG 采用具备 J1 xG 覆盖的供应商，但需确认订阅和 2026 赛季的真实返回值；缺失 xG 保留为空。
- 球员 ID 必须来自官方球员资料或数据供应商，不能由姓名拼接伪造。
- 每场必须按 fixture ID + team ID + player ID 检查重复与错配；球员分钟需有明确来源或可追溯的换人事件推导。
- 只有当 CSV、逐场错误清单和审计 JSON 实际生成后，才可对覆盖率作结论。
