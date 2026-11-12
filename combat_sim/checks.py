"""仓库自检：配置引用、终止条件、统计分母。

1) 配置引用：场景声明的构筑/策略存在；策略里的技能、条件、充能、DoT、增益
   都能在对应构筑/场景里找到出处。
2) 终止条件：时限为正；每个场景在声明种子下都能完整结束（victory/defeat/timeout），
   不带异常（aborted），不出现步数上限。
3) 统计分母：summarize 的成功率分母只含完整结束样本；无完整样本时为 None；
   aborted/stopped 单独计数；参考批次展示表必须能由逐场记录复算。

任何一项失败返回非 0，可直接在 CI 或提交前跑。
"""

from dataclasses import dataclass, field
from pathlib import Path

from . import model
from .runner import run_scenario, verify_event_totals
from .stats import summarize

KNOWN_CONDITIONS = {
    "always", "self_hp_pct_lte", "self_hp_pct_gte",
    "enemy_hp_pct_lte", "enemy_hp_pct_gte", "resource_gte", "resource_lte",
    "cooldown_ready", "debuff_remaining_lte", "buff_active", "charges_gte", "time_gte",
}
BUILD_TYPES = {"damage", "heal", "buff", "restore"}


@dataclass
class Report:
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checked: dict = field(default_factory=dict)

    def add_error(self, msg):
        self.errors.append(msg)

    def add_warning(self, msg):
        self.warnings.append(msg)


def check_all(data_dir=None, run_termination=True):
    root = Path(data_dir) if data_dir else model.DATA_DIR
    rep = Report()
    _check_builds_and_strategies(root, rep)
    scenarios = _load_scenarios(root, rep)
    _check_scenario_references(root, scenarios, rep)
    _check_termination(scenarios, root, rep, run_termination)
    _check_statistics_denominator(rep)
    return rep


# ---------- 配置引用 ----------

def _check_builds_and_strategies(root, rep):
    n_builds = n_rules = 0
    for bp in sorted((root / "builds").glob("*.json")):
        build = model.load_build(bp)
        n_builds += 1
        sp = root / "strategies" / f"{build.name}.json"
        if not sp.is_file():
            rep.add_error(f"构筑 {build.name} 缺少策略文件 {sp}")
            continue
        names = {a.name for a in build.abilities}
        for a in build.abilities:
            if a.type not in BUILD_TYPES:
                rep.add_error(f"构筑 {build.name} 技能 {a.name} 类型未知: {a.type}")
            if a.type == "damage" and a.damage <= 0 and not a.dot:
                rep.add_warning(f"构筑 {build.name} 技能 {a.name} 为伤害型但无伤害/DoT")
            if a.type == "heal" and a.heal <= 0:
                rep.add_warning(f"构筑 {build.name} 技能 {a.name} 为治疗型但治疗量为 0")
            if a.type == "restore" and a.restore <= 0:
                rep.add_warning(f"构筑 {build.name} 技能 {a.name} 为回资源型但回量为 0")
            if a.type == "buff" and not a.buff:
                rep.add_error(f"构筑 {build.name} 技能 {a.name} 为 buff 但缺少 buff 配置")
            if a.dot:
                for k in ("damage", "interval", "duration"):
                    if k not in a.dot:
                        rep.add_error(f"构筑 {build.name} 技能 {a.name} 的 DoT 缺少 {k}")
        rules = model.load_strategy(sp)
        n_rules += len(rules)
        _check_rules(build, rules, rep)
    rep.checked["builds"] = n_builds
    rep.checked["rules"] = n_rules


def _check_rules(build, rules, rep):
    buff_names = {a.buff["name"] for a in build.abilities if a.buff}
    charged = {a.name for a in build.abilities if a.charges is not None}
    dot_names = {a.name for a in build.abilities if a.dot}
    for r in rules:
        if r.action not in {a.name for a in build.abilities}:
            rep.add_error(f"构筑 {build.name} 规则「{r.name}」引用未知技能 {r.action}")
        for key, val in r.cond.items():
            if key not in KNOWN_CONDITIONS:
                rep.add_error(f"构筑 {build.name} 规则「{r.name}」引用未知条件 {key}")
                continue
            if key == "cooldown_ready" and val not in {a.name for a in build.abilities}:
                rep.add_error(f"构筑 {build.name} 规则「{r.name}」cooldown_ready 引用未知技能 {val}")
            if key == "buff_active" and val not in buff_names:
                rep.add_error(f"构筑 {build.name} 规则「{r.name}」buff_active 引用未知增益 {val}")
            if key == "charges_gte" and (not isinstance(val, (list, tuple)) or val[0] not in charged):
                rep.add_error(f"构筑 {build.name} 规则「{r.name}」charges_gte 引用无充能技能 {val}")
            if key == "debuff_remaining_lte":
                if not isinstance(val, (list, tuple)) or len(val) != 2:
                    rep.add_error(f"构筑 {build.name} 规则「{r.name}」debuff_remaining_lte 需 [技能, 秒]")
                elif val[0] not in dot_names:
                    rep.add_error(f"构筑 {build.name} 规则「{r.name}」debuff_remaining_lte 引用无 DoT 技能 {val[0]}")
            if key in ("self_hp_pct_lte", "self_hp_pct_gte", "enemy_hp_pct_lte",
                       "enemy_hp_pct_gte") and not (0 <= val <= 100):
                rep.add_error(f"构筑 {build.name} 规则「{r.name}」{key} 超出 0..100：{val}")


def _load_scenarios(root, rep):
    try:
        return model.load_scenarios(root)
    except Exception as exc:
        rep.add_error(f"场景加载失败: {exc}")
        return {}


def _check_scenario_references(root, scenarios, rep):
    rep.checked["scenarios"] = len(scenarios)
    for name, sc in scenarios.items():
        if not sc.builds:
            rep.add_error(f"场景 {name} 未声明构筑集合")
            continue
        try:
            _, specs = model.load_all(root, list(sc.builds), enemy=sc.enemy)
        except Exception as exc:
            rep.add_error(f"场景 {name} 构筑/策略引用失败: {exc}")
            continue
        if len(specs) != len(sc.builds):
            rep.add_error(f"场景 {name} 声明 {len(sc.builds)} 个构筑，实际加载 {len(specs)}")
        if sc.enemy.time_limit <= 0:
            rep.add_error(f"场景 {name} 时限必须为正: {sc.enemy.time_limit}")
        if sc.enemy.attack_interval <= 0:
            rep.add_error(f"场景 {name} 敌方攻击间隔必须为正")
        if sc.enemy.max_hp <= 0:
            rep.add_error(f"场景 {name} 敌方血量必须为正")
        if sc.runs <= 0:
            rep.add_error(f"场景 {name} 样本量必须为正")


# ---------- 终止条件 ----------

def _check_termination(scenarios, root, rep, run_termination):
    if not run_termination:
        return
    for name, sc in scenarios.items():
        try:
            batch, _, _ = run_scenario(sc, root, runs=1, base_seed=sc.seed, trace=True)
        except Exception as exc:
            rep.add_error(f"场景 {name} 运行失败（终止条件不成立）: {exc}")
            continue
        for build in batch.order:
            r = batch.results[build][0]
            if r.error and "步数上限" in r.error:
                rep.add_error(f"场景 {name}/{build} 触发步数上限，未能终止")
            if not r.outcome.completed:
                rep.add_error(
                    f"场景 {name}/{build} 未完整终止（{r.outcome.value}）: {r.error or r.stop_reason}")
            # dt=0.1，超时判定在 tick 之后，允许一拍过冲
            if r.outcome.completed and r.time > sc.enemy.time_limit + 0.1 + 1e-6:
                rep.add_warning(
                    f"场景 {name}/{build} 用时 {r.time:.1f}s 超过时限 {sc.enemy.time_limit}s")
            ev = verify_event_totals(r)
            if not ev["matches"]:
                rep.add_error(f"场景 {name}/{build} 首场事件与统计不一致: "
                              f"{[k for k, v in ev['checks'].items() if not v]}")


# ---------- 统计分母 ----------

def _check_statistics_denominator(rep):
    from .kernel import Outcome
    from .make_fixture import synthetic_runs  # 保证有万用样本

    rs = synthetic_runs()
    st = summarize(rs)
    expected_completed = sum(1 for r in rs if r.outcome in (
        Outcome.VICTORY, Outcome.DEFEAT, Outcome.TIMEOUT))
    if st["n_completed"] != expected_completed:
        rep.add_error(f"完整样本计数错误: {st['n_completed']} != {expected_completed}")
    if st["aborted"] + st["stopped"] != st["n"] - expected_completed:
        rep.add_error("异常/停止计数与完整样本不互补")
    wins = sum(1 for r in rs if r.outcome is Outcome.VICTORY)
    if expected_completed and abs(st["win_rate"] - wins / expected_completed) > 1e-12:
        rep.add_error("成功率分母不是完整结束样本")
    only_bad = [r for r in rs if not r.outcome.completed]
    st_bad = summarize(only_bad)
    if st_bad["n_completed"] != 0 or st_bad["win_rate"] is not None:
        rep.add_error("无完整样本时应报告胜率不可计算(None)，而不是 0%")
    rep.checked["denominator"] = "ok"
