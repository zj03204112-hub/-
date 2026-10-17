# 每日足球数据任务｜北京时间 2026-10-17 15:00

**状态：部分完成。** 仅本报告成功提交并回读验证；尝试新建详细 CSV 时 GitHub 写入被安全检查拦截，故详细赛果 CSV、全量赛程 CSV、15:00 xG 逐场/周汇总文件没有成功归档。下列明细写在本报告中，不能把未创建的 CSV 视为已归档。

## A. 前一天复盘（北京时间 2026-10-16）

联网核实 38 场已完赛：欧联杯 18 场、欧协联 18 场、解放者杯 1 场、澳超 1 场。UEFA 官方结果页用于欧联/欧协联；UOL 报道用于 Estudiantes–Flamengo；Sydney FC 官方赛报用于悉尼德比。

### 有对话临时比分、但没有不可变赛前快照的 13 场
| 赛事 | 比赛 | 临时第一/第二比分 | 实际比分 | 第一/第二比分命中 |
|---|---|---:|---:|---|
| 欧联 | Lyon–Crystal Palace | 1-1 / 2-1 | 3-2 | 否 / 否 |
| 欧联 | Torreense–Sunderland | 0-2 / 0-3 | 1-2 | 否 / 否 |
| 欧联 | Lech Poznań–Leverkusen | 0-2 / 1-2 | 0-4 | 否 / 否 |
| 欧联 | Union SG–Real Sociedad | 1-1 / 2-1 | 1-1 | 是 / 否 |
| 欧联 | Salzburg–Milan | 1-2 / 1-1 | 0-4 | 否 / 否 |
| 欧联 | Celta–Juventus | 0-1 / 1-1 | 1-2 | 否 / 否 |
| 欧联 | Hoffenheim–Beşiktaş | 2-1 / 2-2 | 2-3 | 否 / 否 |
| 欧联 | Marseille–Olympiacos | 1-0 / 1-1 | 0-1 | 否 / 否 |
| 欧联 | Bournemouth–Sturm Graz | 2-0 / 2-1 | 3-0 | 否 / 否 |
| 欧联 | Benfica–Celtic | 2-0 / 3-0 | 4-1 | 否 / 否 |
| 欧协联 | Universitatea Craiova–Getafe | 0-1 / 1-1 | 1-1 | 否 / 是 |
| 欧协联 | Copenhagen–Braga | 1-1 / 1-2 | 3-0 | 否 / 否 |
| 解放者杯 | Estudiantes–Flamengo | 0-1 / 1-1 | 0-0 | 否 / 否 |

这 13 场临时对话样本的第一比分命中为 **1/13**，第二比分命中为 **1/13**，仅作诊断，**不计入 V5.1 正式指标**。90 分钟胜平负没有独立的赛前字段，不能从比分反推；半全场和风险提示也缺少可核验的赛前字段。

### 另外 24 场欧战：已核实赛果，但缺少原始预测快照
- **欧联 8 场：** Sparta Praha 1-1 Lillestrøm；AZ Alkmaar 2-1 Hapoel Beer-Sheva；Celje 0-3 Omonia；Ferencváros 2-1 Viktoria Plzeň；GNK Dinamo 1-0 Anderlecht；Jagiellonia 3-1 Ararat-Armenia；N.E.C. Nijmegen 1-1 Levski Sofia；Rennes 2-1 OFI Crete。
- **欧协联 16 场：** Lugano 3-2 Crvena Zvezda；Hajduk Split 2-1 Ajax；Gent 1-0 Aarhus；Egnatia 1-1 Midtjylland；KuPS 2-4 Trabzonspor；Mjällby 2-0 Inter Escaldes；Panathinaikos 2-1 Borac；CSKA Sofia 1-2 Monaco；Riga 1-1 Kairat Almaty；Atalanta 3-1 Pafos；Brighton 4-0 Kauno Žalgiris；Twente 1-0 Thun；Hearts 1-1 Nordsjælland；Sint-Truidense 2-0 Iberia Tbilisi；Freiburg 3-2 Jablonec；Brann 2-1 Lincoln Red Imps。
- **其他 1 场：** Sydney FC 0-0 Western Sydney Wanderers。只有聊天预测 2-1 / 1-1，未冻结，单独标记，不纳入 13 场统计。

因此，38 场的**正式 V5.1 命中率均为 N/A**：没有找到可审计的不可变赛前快照。官方竞彩让球命中率也为 N/A：没有取得原始 Sporttery/雷速比赛级真实让球线及采集时间。第三方盘口不能代替官方盘口。延期比赛不计入已完赛场次。

## B. 当天赛程与预测窗口（北京时间 2026-10-17）

### 本日已完赛的 5 场（均未找到本次运行前的不可变预测快照）
| 比赛 | 实际比分 | 第三方赛后 xG（主/客） |
|---|---:|---:|
| Eintracht Frankfurt–1. FC Köln | 1-0 | 1.38 / 0.67 |
| Frosinone–Sassuolo | 4-2 | 2.08 / 1.92 |
| Deportivo La Coruña–Levante | 1-1 | 1.11 / 0.83 |
| Le Mans–Toulouse | 0-3 | 0.90 / 1.81 |
| Burnley–Wolverhampton Wanderers | 1-2 | 1.00 / 1.82 |

以上 xG 是第三方赛后估算，供应商模型版本未披露；只能用于该队之后的比赛，不能回填为同场赛前输入。

### 进入开赛前 6–12 小时窗口的 30 场
- **英超 4：** Brentford–Liverpool（22:00）；Fulham–Hull City（22:00）；Manchester City–Ipswich Town（22:00）；Newcastle United–Aston Villa（10 月 18 日 00:30）。
- **西甲 3：** Villarreal–Elche（21:15）；Real Betis–Barcelona（23:30）；Valencia–Athletic Club（10 月 18 日 02:00）。
- **意甲 2：** Bologna–Inter（23:00）；Roma–Genoa（10 月 18 日 01:45）。
- **德甲 6：** Union Berlin–Borussia Dortmund（21:30）；Hamburger SV–VfB Stuttgart（21:30）；Werder Bremen–SC Paderborn 07（21:30）；Schalke–Mainz（21:30）；SV Elversberg–Augsburg（21:30）；Bayern München–RB Leipzig（10 月 18 日 00:30）。
- **法甲 3：** Strasbourg–Paris Saint-Germain（23:15）；Lille–Brest（10 月 18 日 02:45）；Troyes–RC Lens（10 月 18 日 02:45）。
- **英冠 8：** Birmingham City–Queens Park Rangers；Bristol City–Sheffield United；Derby County–Stoke City；Norwich City–Portsmouth；Southampton–Bolton Wanderers；Swansea City–West Ham United；West Bromwich Albion–Blackburn Rovers；Wrexham–Preston North End（均 22:00）。
- **挪超 2：** Kristiansund–Aalesund（22:00）；Viking–Fredrikstad（10 月 18 日 00:00）。
- **芬超 2：** HJK–IF Gnistan（21:00，恰好为开赛前 6 小时）；FC Inter Turku–AC Oulu（23:00）。

这 30 场均**未生成正式预测**：仓库中未找到可验证的 V5.1 正式生产运行器，本次也未取得比赛级官方竞彩/雷速盘口、初盘到即时盘原始快照。首发/伤停、近 3 场、战术、天气、裁判、赛前 xG、新闻、赛程密度及下一场对手重要性没有完成逐场核验。因此不补造比分、90 分钟胜平负、让球胜平负、半全场、置信度或策略编号。其余 19 场仅登记为已开赛、临近开赛或窗口外赛程；详细全量赛程 CSV 未成功写入。

## C. xG 周度数据保存状态

仓库现有 `xG/weekly/2026-W42/` 的 11:59 部分快照包含少量第三方赛后 xG。本次新的 15:00 逐场/周汇总 CSV 未成功写入。当前可核验的 5 场早间完赛比赛有 10 条第三方赛后 xG 估算，其他 98 条比赛级状态未取得终场 xG或比赛尚未开赛。

**完整周覆盖仍未完成：** 数据库未连接，内部球队 ID、比赛 ID、赛事 ID 未解析，未抓取所有球队本 ISO 周全部已赛比赛的 xG。完整周 xG、xGA、xGD 及每 90 分钟均值均不能宣称完成；空值不是 0。详细逐场 xG 与周汇总文件未成功归档。

## 保护与归档

V5.1、main 和冻结验证样本均未修改。本报告已提交并回读验证；只归档了这份报告，未合并到 main。详细赛果/赛程 CSV 和新的 xG 周快照仍是明确缺项。
