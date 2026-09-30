# 500场历史比赛基础数据库

范围：2026-01-01至2026-09-30；英超、西甲、德甲、意甲、法甲、韩国K League 1、日本J1 League；仅正式联赛。

字段：date,league,home,away,home_score,away_score,result,handicap,handicap_result,starting_xi_home,starting_xi_away,substitutions_home,substitutions_away,goals,assists,cards,injuries_absences,source_result,source_lineup

规则：90分钟赛果；加时赛不计入；先记录客观历史数据，再进行模型验证。首发/换人/进球/助攻等球员数据优先使用可核验的比赛详情来源；无法核验的字段留空，不猜测。Sporttery用于官方竞彩赛果/让球核验；Soccerway可用于比赛详情、首发、换人、进球、助攻等交叉核验。
