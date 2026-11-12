"""运行器：批量、单场与实战演练都走同一个 Kernel，只是 trace 开关不同。

场景（Scenario）是唯一入口：它固定对手、样本量、默认种子与构筑集合。
批量结果可逐场落盘（种子、规则摘要、结局、计数器与事件复算值），
供文档直接从原始记录复现展示表，而不是手抄。
"""

from dataclasses import dataclass, field

from . import model
from .kernel import Kernel


@dataclass
class BatchResult:
    results: dict          # build_name -> [RunResult]
    order: list            # 构筑顺序
    runs: int
    base_seed: int
    enemy_name: str
    time_limit: float
    scenario: str | None = None
    scenario_label: str | None = None
    material: dict = field(default_factory=dict)   # build -> [逐场原始记录]


def load_scenario_specs(scenario, data_dir=None):
    """按场景声明的构筑集合加载 (build, rules)；场景未声明则加载全部。"""
    names = list(scenario.builds) if scenario.builds else None
    return model.load_all(data_dir, names, enemy=scenario.enemy)


def run_single(build, enemy, rules, seed, trace=False, max_steps=None,
               scenario=None, should_stop=None):
    return Kernel(build, enemy, rules, seed, trace=trace, max_steps=max_steps,
                  scenario=scenario, should_stop=should_stop).run()


def run_batch(specs, enemy, runs, base_seed, trace=False, scenario=None,
              should_stop=None):
    """specs: [(build, rules)]。所有构筑共用同一组种子，便于同种子配对比较。"""
    results, order = {}, []
    for build, rules in specs:
        order.append(build.name)
        results[build.name] = [
            run_single(build, enemy, rules, seed=base_seed + i, trace=trace,
                       scenario=scenario, should_stop=should_stop)
            for i in range(runs)
        ]
    return BatchResult(results=results, order=order, runs=runs, base_seed=base_seed,
                       enemy_name=enemy.name, time_limit=enemy.time_limit,
                       scenario=getattr(scenario, "name", None),
                       scenario_label=getattr(scenario, "label", None))


def run_scenario(scenario, data_dir=None, runs=None, base_seed=None, trace=False):
    """按场景跑一批，并把每场原始记录附在 BatchResult 上。"""
    _, specs = load_scenario_specs(scenario, data_dir)
    runs = scenario.runs if runs is None else runs
    base_seed = scenario.seed if base_seed is None else base_seed
    rules_by_build = {b.name: rules for b, rules in specs}
    builds_by_name = {b.name: b for b, _ in specs}
    batch = run_batch(specs, scenario.enemy, runs=runs, base_seed=base_seed,
                      trace=trace, scenario=scenario.name)
    batch.scenario_label = scenario.label
    batch.material = {
        name: [material_of(r, builds_by_name[name], scenario, rules_by_build[name])
               for r in batch.results[name]]
        for name in batch.order
    }
    return batch, builds_by_name, rules_by_build


def rule_digest(rules):
    """规则摘要：条件和动作都直接取自加载到的策略，供原始记录留存。"""
    return [{"name": r.name, "if": r.cond, "do": r.action} for r in rules]


def material_of(result, build, scenario, rules):
    """单场原始记录：种子、规则摘要、结局、计数器、事件流与事件复算统计。"""
    trace = result.trace or []
    return {
        "build": result.build,
        "build_label": build.label,
        "seed": result.seed,
        "scenario": result.scenario or getattr(scenario, "name", None),
        "scenario_label": getattr(scenario, "label", None),
        "outcome": result.outcome.value,
        "completed": result.outcome.completed,
        "time": round(result.time, 4),
        "kill_time": None if result.kill_time is None else round(result.kill_time, 4),
        "error": result.error,
        "stop_reason": result.stop_reason,
        "counters": _rounded_counters(result.counters),
        "events": _round_events(trace),
        "event_totals": verify_event_totals(result),
        "rule_summary": rule_digest(rules),
    }


def _round_events(trace):
    """保存可独立复算的精简事件为 [t, kind, data]，每拍噪声事件不进参考批次。"""
    keep = {"damage", "heal", "enemy_attack", "spend", "starved_interval", "potion_used",
            "resource_min", "restore", "cast", "dot_apply", "buff", "enrage",
            "victory", "defeat", "timeout", "aborted", "stopped"}
    out = []
    for entry in trace:
        if entry[0] != "event":
            continue
        _, t, kind, data = entry
        if kind not in keep:
            continue
        out.append([round(t, 2), kind, data])
    return out


def _rounded_counters(c):
    out = {}
    for k, v in c.items():
        if isinstance(v, dict):
            out[k] = {kk: round(vv, 4) for kk, vv in v.items()}
        elif isinstance(v, (int, float)):
            out[k] = round(float(v), 4)
        else:
            out[k] = v
    return out


_MISSING = object()


def verify_event_totals(result):
    """只凭事件流复算关键指标，和内核计数器逐项对齐；返回复算值与是否一致。

    事件设计原则：每个计数器增量都恰好对应一条事件，因此复算不依赖内核内部状态。
    保存到参考批次的 events 字段是精简后的事件流，供 verify 独立重算。
    """
    return reconcile(result.counters, result.trace or [], final_hp=result.counters.get("final_hp"))


def reconcile(counters, trace, final_hp=None):
    acc = {"damage_total": 0.0, "damage_by_ability": {}, "hits": 0, "crits": 0,
           "healing_done": 0.0, "damage_taken": 0.0, "resource_spent": 0.0,
           "starved_events": 0, "starved_time": 0.0, "potions_used": 0,
           "min_resource": None, "final_hp": None}
    for entry in trace:
        if entry[0] != "event":
            continue
        _, _, kind, data = entry
        if kind == "damage":
            acc["damage_total"] += data["amount"]
            acc["damage_by_ability"][data["ability"]] = \
                acc["damage_by_ability"].get(data["ability"], 0.0) + data["amount"]
            if data.get("counts_hit"):
                acc["hits"] += 1
                acc["crits"] += 1 if data["crit"] else 0
        elif kind == "heal":
            acc["healing_done"] += data["amount"]
        elif kind == "enemy_attack":
            acc["damage_taken"] += data["amount"]
        elif kind == "spend":
            acc["resource_spent"] += data["cost"]
        elif kind == "starved_interval":
            acc["starved_events"] += data["events"]
            acc["starved_time"] += data["duration"]
        elif kind == "potion_used":
            acc["potions_used"] += 1
        elif kind == "resource_min":
            r = data["resource"]
            acc["min_resource"] = r if acc["min_resource"] is None else min(acc["min_resource"], r)
        elif kind in ("victory", "defeat", "timeout", "aborted", "stopped"):
            acc["final_hp"] = data.get("hp")

    def near(a, b, tol=1e-6):
        return abs((a or 0.0) - (b or 0.0)) <= tol * max(1.0, abs(b or 0.0))

    checks = {
        "damage_total": near(acc["damage_total"], counters["damage_total"]),
        "healing_done": near(acc["healing_done"], counters["healing_done"]),
        "damage_taken": near(acc["damage_taken"], counters["damage_taken"]),
        "resource_spent": near(acc["resource_spent"], counters["resource_spent"]),
        "starved_time": near(acc["starved_time"], counters["starved_time"]),
        "starved_events": acc["starved_events"] == counters["starved_events"],
        "hits": acc["hits"] == counters["hits"],
        "crits": acc["crits"] == counters["crits"],
        "potions_used": acc["potions_used"] == counters["potions_used"],
    }
    min_ref = acc["min_resource"] if acc["min_resource"] is not None else counters["min_resource"]
    checks["min_resource"] = near(min_ref, counters["min_resource"])
    if final_hp is not None:
        checks["final_hp"] = near(acc["final_hp"] if acc["final_hp"] is not None else final_hp, final_hp)
    for k, v in counters["damage_by_ability"].items():
        checks[f"damage:{k}"] = near(acc["damage_by_ability"].get(k, 0.0), v)
    for k, v in acc["damage_by_ability"].items():
        checks.setdefault(f"damage:{k}", near(v, counters["damage_by_ability"].get(k, 0.0)))

    return {
        "recomputed": _rounded_counters({
            "damage_total": acc["damage_total"], "damage_by_ability": acc["damage_by_ability"],
            "hits": acc["hits"], "crits": acc["crits"],
            "healing_done": acc["healing_done"], "damage_taken": acc["damage_taken"],
            "resource_spent": acc["resource_spent"],
            "starved_events": acc["starved_events"], "starved_time": acc["starved_time"],
            "potions_used": acc["potions_used"],
            "min_resource": min_ref,
            "final_hp": acc["final_hp"] if acc["final_hp"] is not None else final_hp,
        }),
        "matches": all(checks.values()),
        "checks": checks,
    }
