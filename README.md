# 构筑演练器

固定对手、固定场景约束下，真实执行攻击判定、冷却、读条、GCD、资源扣减与回复、
DoT 跳伤、增益、敌方攻击、狂暴和时限，用来比较不同构筑的**持续输出、存活与资源耗尽**表现。

- 批量演练与单场播放共用同一个规则内核（`combat_sim/kernel.py`），不是两套逻辑。
- 展示表里的每个数字都能从原始记录（种子、规则摘要、事件流、计数器）复算。
- 成功率、击杀时间、资源受限时间的分母只含**完整结束**的样本；模拟失败与主动停止单独统计。
- 图形入口是**构筑演练界面**（本地网页），可选构筑、播放单场并核对战斗过程与收益统计。

本文件即开发文档，其中所有示例都可由文中的命令在清洁目录重新生成，不依赖手工改过的数据。

---

## 1. 快速开始

需要一个 Python 3.10+（只依赖标准库）：

```bash
# 图形入口：构筑演练界面（选择构筑 -> 播放单场 -> 核对战斗过程与收益）
python3 -m combat_sim gui

# 命令行辅助：列出场景与构筑
python3 -m combat_sim scenarios
python3 -m combat_sim list
```

图形界面默认监听 `http://127.0.0.1:8765/`。某端口被占用时可换端口：

```bash
python3 -c "from combat_sim.gui_support import serve; serve(port=8801)"
```

---

## 2. 构筑演练图形入口

`python3 -m combat_sim gui` 打开「战斗演练场」，它读场景 `battle_practice`
（`combat_sim/data/scenarios/battle_practice.json`）：

1. **选择构筑**：下拉框列出场景声明的构筑（短时爆发 / 资源受限 / 持续输出）。
2. **填种子**：默认取场景种子；相同构筑 + 相同种子必然得到相同结果。
3. **播放单场并核对**：页面用同一个内核跑这一场，逐条渲染战斗过程事件。
4. **看右侧统计与收益**：统计来自本次运行的计数器；收益由 `combat_sim/rewards.py`
   按实际数值现场结算（基础、胜利、速度、伤害、血量五项相加）。
5. **看顶部横幅**：界面会按事件流独立复算关键指标，和统计记录逐项比对。
   - 一致 -> 绿色「核对通过」；
   - 不一致 -> 红色「异常：战斗过程与统计不一致 → <具体字段>」。

异常（配置错误、运行异常）不会静默变成收益：非胜利或未完整结束的场次收益一栏显示
「无可结算收益」；`aborted`/`stopped` 这类未完整结束的场次，即使事件与统计一致，
顶部横幅仍显示红色「异常：本场未完整结束（aborted）→ 原因」，并注明收益不予结算。
构筑配置有问题时接口直接返回错误，页面顶部红色提示。

**如何核对一场**（可照做的步骤）：

```bash
# 单场演算的数字，与下方命令行回放同源
python3 -m combat_sim play --build burst --seed 1 --scenario battle_practice
```

图形界面里选「短时爆发」、种子 `1`，会看到：

- 战斗过程事件开头是 `t+0.00s 施放 surge`、`t+0.00s 获得增益 surge（12s）`，
  随后 `t+1.00s 消耗资源 50（obliterate，当前 170.0）`、
  `t+1.00s obliterate 造成 9187.2 暴击!（目标余 10813）`；
- 统计：总伤害 `20767.6`、暴击 `6/12`、承伤 `1340.0`、资源受限 `0.0s / 0 次`、剩余血量 `1810.0`；
- 收益：`基础 100.0 + 胜利 100.0 + 速度 67.7 + 伤害 10.4 + 血量 24.7 = 302.8`。

命令行 `play` 打印的「总伤害 暴击 治疗 承伤 资源消耗 资源受限 剩余血量」和收益五项
与界面右侧逐项相同（伤害显示取整为 `20768`，界面保留一位小数 `20767.6`）；
页面顶部同时显示「核对通过」。

---

## 3. 命令行（辅助）

命令行只做辅助与复核，**图形入口才是构筑演练的主入口**（见上一节）。

```bash
# 列出全部场景
python3 -m combat_sim scenarios

# 按场景批量对比（默认基准场景 baseline）
python3 -m combat_sim batch --scenario baseline
python3 -m combat_sim batch --scenario survival_pressure
python3 -m combat_sim batch --scenario resource_exhaustion

# 单场回放（--show-draws 连随机抽样一起显示）
python3 -m combat_sim play --build sustained --seed 9 --scenario resource_exhaustion
python3 -m combat_sim play --build sustained --seed 9 --scenario resource_exhaustion --show-draws

# 列出构筑、策略与条件语法
python3 -m combat_sim list

# 仓库自检：配置引用、终止条件、统计分母
python3 -m combat_sim check

# 按场景阈值报告构筑约束（不中断，用于查看部分不满足的真实结果）
python3 -m combat_sim constraint-check --scenario partial_not_satisfied \
    --data examples/partial_constraint
```

---

## 4. 场景与口径

### 4.1 结局五态，两类分母

| 结局 | 含义 | 是否计入成功率分母 |
|---|---|---|
| `victory` | 击杀目标（唯一成功） | 是 |
| `defeat` | 玩家死亡（有效失败） | 是 |
| `timeout` | 超过场景时限（有效失败） | 是 |
| `aborted` | **模拟失败**：步数上限、非法策略/配置、运行异常 | 否，单独统计 |
| `stopped` | **主动停止**：调用方（界面/脚本）提前结束 | 否，单独统计 |

因此：

- 成功率 = 胜利 /（胜利+战败+超时），**不是** 胜利 / 总场次；
- 没有任何完整结束样本时，成功率与击杀时间显示为「不可计算（None）」，不得写成 0%；
- 只有 1 个胜利样本时不给 P90、不给离散度（单点方差是 0，不能据此讲「稳定」）；
- `aborted` 与 `stopped` 只在「失败」「停止」两列单独显示，绝不被平均进任何指标。

`stats.scenario_warnings()` 会把上述情况变成显式警示；`batch` 报告的「统计口径警示」
一栏就是它的输出。`check` 子命令从仓库层面验证这套口径（见第 7 节）。

### 4.2 为什么替换了旧样例

旧样例只有一套基准场景：三套构筑全部 100% 获胜、资源受限时间都在 0.1s 量级，
展示表里所有构筑几乎一样。这样的样例会给出「零方差」的误导性结论——看起来任何构筑都能赢、
资源从来不是问题。**替换原因**：原样例无法暴露构筑差异，收益/受限等指标没有区分度。

现在的做法不是改报告标题，而是：

- 保留 `baseline` 作为**回归基线**（只验证确定性与统计口径，不用于能力排名）；
- 新增两个演练场景，用真实事件流把差异拉开（见 4.3）；
- 新增两个可运行示例，专门演示「没有有效样本」和「部分构筑不满足约束」时不输出误导性结论。

### 4.3 内置场景（`combat_sim/data/scenarios/`）

| 场景 `name` | 标签 | 用途 | 结果特征（n=60，种子 1..60） |
|---|---|---|---|
| `baseline` | 基准回归场景 | 回归与口径自检 | 三构筑 100% 胜，速度 25.4s / 50.4s / 66.6s |
| `survival_pressure` | 持续输出·生存压力 | 长线承伤与自愈节奏 | 短时爆发 96.7%，持续输出 26.7%，资源受限 0%（战败，不是模拟失败） |
| `resource_exhaustion` | 持续输出·资源耗尽 | 资源回复与消耗的长期平衡 | 持续输出 100%，短时爆发 0%（资源受限 5.6s/112 次后战败），资源受限 0%（60 场超时） |
| `battle_practice` | 战斗演练场 | 图形入口的单场演练 | 单场，收益由实际计数器结算 |

`resource_exhaustion` 里的「短时爆发 0%」是**有效失败子集**：它的失败是 `defeat`
（资源耗尽后被打死），不是 `aborted`。展示表在该构筑的击杀时间留空并给出警示，
不会拿它的空集去和 100% 的持续输出比较速度。

---

## 5. 参考批次（短时可执行）

一次运行几秒钟，逐场保存种子、规则摘要、事件流与结果，再从原始记录复算展示表。

```bash
# 一步到位：自检 -> 生成 -> 复核 -> 复算表格 -> 跑示例
bash scripts/run_reference.sh runs

# 或分步
python3 -m combat_sim reference --out runs
python3 -m combat_sim verify --dir runs
python3 -m combat_sim show-runs --dir runs
```

产物（`runs/` 已被 `.gitignore` 忽略，因为含逐场事件流，体积较大）：

```
runs/index.json                 # 场景清单、场次计数
runs/baseline.json              # 场景参数 + 规则摘要 + 逐场记录 + 展示表
runs/survival_pressure.json
runs/resource_exhaustion.json
```

每场记录 `records[构筑][i]` 至少含：

- `seed`、`outcome`、`completed`、`time`、`kill_time`、`error`/`stop_reason`
- `counters`（伤害、暴击、治疗、承伤、资源消耗、受限时间/次数、药水、最低资源、终值血量）
- `events`（精简事件流，供独立重算）
- `event_totals`（当次事件的复算值与逐项一致性）
- `rule_summary`（该构筑当次加载到的策略，逐条 `{name, if, do}`）

`show-runs` **只读这些记录**，不重新模拟，因此表格可以完全脱离进程复现。
`verify` 也从 `events` 独立重算并与 `counters` 比对，篡改任何计数器都会被判定失败
（测试 `TestReferenceRoundTrip` 覆盖了这条）。

---

## 6. 可运行示例

### 6.1 无有效样本（`examples/empty_no_valid/`）

策略引用未知条件，每场在开打前记为 `aborted`：

```bash
python3 -m combat_sim batch --scenario no_valid_samples --data examples/empty_no_valid
```

输出要点：

- 胜率列显示「不可计算」，不是 `0.0%`；
- 失败列为 6（全是 `aborted`），完整样本为 0；
- 警示明确写出「没有完整结束样本：成功率/击杀时间不可计算」「有 6 场模拟失败」；
- 结论区拒绝给出速度排名。

### 6.2 部分构筑不满足场景约束（`examples/partial_constraint/`）

同一生存压力场景下，三构筑的结局分布不同：

```bash
python3 -m combat_sim constraint-check --scenario partial_not_satisfied \
    --data examples/partial_constraint
```

输出要点（n=20）：

| 构筑 | 完整 | 胜利 | 战败 | 模拟失败 | 停止 | 胜率 | 约束 |
|---|---|---|---|---|---|---|---|
| 短时爆发 | 20 | 19 | 1 | 0 | 0 | 95.0% | 满足 |
| 资源受限 | 20 | 0 | 20 | 0 | 0 | 0.0% | 不满足（<95%） |
| 持续输出 | 20 | 7 | 13 | 0 | 0 | 35.0% | 不满足（<95%） |

这里的失败都是 `defeat`（有效结局），不是 `aborted`；报告按构筑分别标注，
不会把「0%」和「95%」混成一个平均值。

---

## 7. 仓库自检（`python3 -m combat_sim check`）

自检三类问题，任一失败返回非 0，适合放进 CI 或提交前钩子：

1. **配置引用**：场景声明的构筑/策略存在且同名；策略里的技能、条件键、
   `cooldown_ready`/`buff_active`/`charges_gte`/`debuff_remaining_lte` 的引用目标
   都能在对应构筑里找到；血量百分比在 0..100；DoT/增益字段齐全。
2. **终止条件**：每个场景用声明种子实跑 1 场，必须落到 `victory/defeat/timeout`，
   不能落到 `aborted`、不能触发步数上限、不能超时失控；首场事件流必须与计数器一致。
3. **统计分母**：用合成样本覆盖五类结局，确认成功率分母只含完整结束样本；
   只有异常/停止时胜率必须为 `None` 而不是 `0.0`。

```bash
python3 -m combat_sim check
# >>> 自检通过：配置引用、终止条件与统计分母均符合口径。

# 故意写坏一条策略引用后，自检会指出具体位置：
python3 -m combat_sim check --data /path/to/broken_data
# >>> 错误: 构筑 sustained 规则「急救」引用未知技能 not_a_skill
# >>> 错误: 场景 baseline/sustained 未完整终止（aborted）: KeyError: ...
```

---

## 8. 数据格式

### 8.1 构筑 `data/builds/<name>.json`

```json
{
  "name": "burst", "label": "短时爆发",
  "max_hp": 2200, "max_resource": 220, "resource_regen": 9,
  "crit_chance": 0.30, "crit_mult": 2.2, "haste": 0.0,
  "abilities": [
    {"name": "slam", "type": "damage", "damage": 180, "cost": 6},
    {"name": "lance", "type": "damage", "damage": 900, "cost": 35, "cooldown": 6, "cast_time": 1.2},
    {"name": "surge", "type": "buff", "cooldown": 60,
     "buff": {"name": "surge", "damage_mult": 1.8, "duration": 12}}
  ]
}
```

- `type`: `damage` | `heal` | `buff` | `restore`
- 急速只缩短读条与 GCD，不缩短冷却；DoT 跳伤不暴击；治疗量固定不掷随机。
- `potion` 这类 `restore` 技能可带 `charges`（整场可用次数）。

### 8.2 策略 `data/strategies/<构筑名>.json`

```json
[
  {"name": "急救", "if": {"self_hp_pct_lte": 50, "cooldown_ready": "mend"}, "do": "mend"},
  {"name": "填充", "if": {"always": true}, "do": "slam"}
]
```

规则按列表顺序求值，命中第一条**可执行**（冷却、充能、资源都满足）的规则。
资源不足会记 `starved` 事件并继续尝试后续规则。

可用条件键（同一规则内为 AND）：

| 键 | 值 | 含义 |
|---|---|---|
| `always` | `true` | 恒真（兜底） |
| `self_hp_pct_lte` / `_gte` | 百分比 | 自身血量百分比 |
| `enemy_hp_pct_lte` / `_gte` | 百分比 | 目标血量百分比 |
| `resource_gte` / `_lte` | 值 | 当前资源 |
| `cooldown_ready` | 技能名 | 冷却就绪 |
| `debuff_remaining_lte` | `[技能名, 秒]` | 目标 DoT 剩余时间 |
| `buff_active` | 增益名 | 自身增益生效中 |
| `charges_gte` | `[技能名, 数量]` | 剩余充能 |
| `time_gte` | 秒 | 战斗时长 |

技能名写错或条件未知 => 该场记为 `aborted`，由自检提前拦下。

### 8.3 场景 `data/scenarios/<name>.json`

```json
{
  "name": "survival_pressure", "label": "持续输出·生存压力",
  "purpose": "……", "kind": "batch",
  "enemy": {"name": "重压守卫·裂甲", "max_hp": 16000, "attack_interval": 1.8,
            "damage": 260, "time_limit": 150.0, "enrage_at": 110.0},
  "runs": 60, "seed": 1,
  "builds": ["burst", "resource", "sustained"],
  "requirements": {"min_completed": 60, "min_win_rate": 0.0,
                   "max_timeouts": null, "max_aborted": 0, "max_stopped": 0},
  "rewards": {"win_base": 100.0, "per_remaining_second": 0.4,
              "per_damage_hundred": 0.05, "hp_remaining_pct": 1.0},
  "notes": "……"
}
```

- `requirements` 的五项都会被 `verify`/`constraint-check` 读取；
  `null` 表示不限制该项（如允许超时的资源耗尽场景）。
- `rewards` 五项用于收益结算，见 `combat_sim/rewards.py`。
- `kind`: `batch`（批量）或 `battle_practice`（图形单场演练的默认场景）。

### 8.4 追加场景

新增一个 JSON 到 `combat_sim/data/scenarios/` 即可被 `scenarios`/`batch`/`play` 发现；
要让 `reference` 也生成它，把它加入 `combat_sim/reference.py` 的 `REFERENCE_SCENARIOS`
（或改 `build_reference(scenarios=...)` 的调用）。新增构筑只需 `builds/<name>.json`
和同名 `strategies/<name>.json`，`check` 会核对两者引用一致。

---

## 9. 收益口径

`combat_sim/rewards.compute_reward()` 只对 `victory` 结算：

```
合计 = 基础 + 胜利加成 + 速度加成 + 伤害加成 + 血量加成
      = base + base
      + (时限 - 用时) * per_remaining_second
      + 总伤害 / 100 * per_damage_hundred
      + (终值血量 / 最大血量) * base * hp_remaining_pct
```

界面与命令行的收益都调用这一个函数；参考批次的收益由
`reference.compute_reward_from_record()` 用记录里的终值血量与总伤害算出，
两者公式相同。非胜利、`aborted`、`stopped` 一律 `total=None`。

---

## 10. 从清洁目录重新生成（验收清单）

在干净检出后依次执行：

```bash
# 0) 依赖：仅 Python 标准库
python3 -m unittest discover -s tests            # 全绿

# 1) 自检
python3 -m combat_sim check

# 2) 整批演练（生成 + 复核 + 复算表格 + 示例）
bash scripts/run_reference.sh runs

# 3) 打开图形入口，核对其中一场
python3 -m combat_sim gui
```

第 3 步打开页面后：

1. 选「短时爆发」，种子 `1`，点「播放单场并核对」；
2. 顶部应显示绿色「核对通过」；
3. 对照事件流与右侧统计：总伤害 `20767.6`、暴击 `6/12`、承伤 `1340.0`、
   资源受限 `0.0s / 0 次`、剩余血量 `1810.0`，收益 `302.8`（五项明细相加）；
4. 再对照命令行 `python3 -m combat_sim play --build burst --seed 1 --scenario battle_practice`
   的尾部统计，两者必须一致。

所有表格都可以用 `show-runs` 从 `runs/` 的原始记录重新打印，
文中示例不再依赖任何手工改过的数据。

---

## 11. 文件地图

```
combat_sim/
  kernel.py       规则内核（五态结局、事件流、步数上限、主动停止）
  runner.py       批量/单场/场景运行、逐场原始记录、事件复算
  stats.py        分母口径、配对比较、口径警示
  rewards.py      收益结算（唯一公式）
  view.py         批量报告、回放、异常与警示输出
  reference.py    参考批次生成、复核、从记录复算表格、约束检查
  checks.py       仓库自检（配置引用/终止条件/统计分母）
  gui_support.py  图形入口（本地网页，单场演练与核对）
  cli.py          命令行（辅助）
  make_fixture.py 自检用合成样本
  data/
    builds/       构筑
    strategies/   同名策略
    scenarios/    场景（含 battle_practice 图形演练场景）
examples/
  empty_no_valid/       无有效样本示例
  partial_constraint/   部分构筑不满足约束示例
scripts/run_reference.sh  清洁目录整批生成与复核
tests/test_kernel.py      内核、口径、参考批次、图形核对
```
