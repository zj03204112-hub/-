# J1 2026 xGA 独立来源核验报告 — 2026-11-02

## 核验对象与范围

- 仓库：`zj03204112-hub/-`
- 分支：`audit/stable-match-id-mapping-20261101`
- 核验基线：`6ee6a79e07e6235c3819c84b06b08dc7cf0ff00e`
- 数据文件：`data/season_match_detail/j1_2026_match_xg_free.csv`
- 样本：130 场已映射比赛；逐行检查 CSV 的 xG/xGA、来源名称、来源 URL、原始行与备注。

## 结论

**独立来源明确展示的比赛级 xGA：0/130。**

| 分类 | 场数 | 解释 |
|---|---:|---|
| 当前同时有 home_xga / away_xga 的记录 | 50 | 全部来源标为 OddAlerts |
| xGA 与对手 xG 完全镜像的记录 | 50 | home_xga = away_xg 且 away_xga = home_xg |
| 可证明由来源页面独立展示比赛级 xGA 的记录 | 0 | 当前留存证据没有显示独立 xGA 字段/数值 |
| xGA 仍为空的记录 | 80 | 保持缺失，不估算、不反推 |

## 来源核验

1. CSV 中全部 50 条非空 xGA 记录的 `source_name` 均为 `OddAlerts`，来源 URL 均指向其公开联赛 xG 页面：<https://www.oddalerts.com/xg/j-league>。
2. 对应的 `source_raw_row` 保存的是一场比赛的日期、主队名称与主队 xG、比分、客队名称与客队 xG。没有保存一个独立的 xGA 数值或单独的 xGA 字段证据。
3. OddAlerts 的公开 xG 说明页将 xG 解释为比赛中机会质量的汇总；其旧版说明也提及 xGA 可用于球队层面的统计，并支持最近若干场及主客场汇总。该说明不能证明当前这 50 场的 xGA 是一个独立采集/独立建模的比赛级字段。来源说明：<https://www.oddalerts.com/journal/expected-goals-explained>、<https://www.oddalerts.com/guides/glossary/expected-goals-xg>。
4. 因为两列严格镜像，当前最稳妥的语义是 **opponent-xG-equivalent（对手 xG 等价值）**。在足球统计定义中，球队的 xGA 通常可由对手的 xG 表示；但这属于同一组双方 xG 的对应/派生关系，不能被记成“供应商另行独立提供的 xGA”。
5. Tifolab、ScoutingStats、VJStats、FotMob 的本数据行目前均没有非空 xGA。它们的比赛级 xG 页面不能自动证明独立 xGA 覆盖。

## 数据处理决定

- 不将这 50 条镜像值计入“独立来源 xGA 覆盖率”。
- 不静默删除原始值：保留现有 xGA 数值，以便追溯，并将其语义限制为对手 xG 等价值。
- 后续统计应同时报告：
  - `xga_independent_source_verified`：独立来源明确展示的比赛级 xGA；
  - `xga_opponent_xg_equivalent`：由双方 xG 对应得到的 xGA 等价值；
  - `xga_missing`：缺失值。
- 当前计数：独立来源核验 0 场；对手 xG 等价 50 场；缺失 80 场。
- 继续遵守免费来源、可追溯 URL、不用比分或球队均值估算、不跨供应商平均的规则。

## 可复核链接

- 当前 CSV：<https://github.com/zj03204112-hub/-/blob/audit/stable-match-id-mapping-20261101/data/season_match_detail/j1_2026_match_xg_free.csv>
- xG/xGA 审计 JSON：<https://github.com/zj03204112-hub/-/blob/audit/stable-match-id-mapping-20261101/data/season_match_detail/j1_2026_match_xg_free_audit.json>
- OddAlerts xG 联赛页：<https://www.oddalerts.com/xg/j-league>
- OddAlerts xG 说明：<https://www.oddalerts.com/journal/expected-goals-explained>
