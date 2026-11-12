"""参考批次：生成、落盘与复核。

一次运行做两件事：
1. 对每个演练场景，按场景声明的种子逐场运行，保存种子、规则摘要、结局、
   计数器与事件复算统计到 runs/<scenario>.json，并附一份展示表。
2. 复核：从原始记录重新聚合展示表（不信任进程内对象），核对每场事件流
   与统计记录一致，并检查场景约束（分母、异常、零方差、失败/停止分类）。

`verify` 只读落盘文件，因此可在清洁目录里完整复现，不依赖任何手工数据。
"""

import json
import os
from pathlib import Path

from . import model
from .rewards import compute_reward
from .runner import reconcile, rule_digest, run_scenario
from .stats import summarize


REFERENCE_SCENARIOS = (
    "baseline",
    "survival_pressure",
    "resource_exhaustion",
)


def build_reference(out_dir, data_dir=None, scenarios=None, runs_override=None):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scenarios = scenarios or REFERENCE_SCENARIOS
    summary = {"generated": [], "skipped": [], "errors": []}

    available = model.load_scenarios(data_dir)
    for name in scenarios:
        if name not in available:
            summary["skipped"].append({"scenario": name, "reason": "场景不存在"})
            continue
        sc = available[name]
        batch, meta, rules_by_build = run_scenario(
            sc, data_dir, runs=runs_override, trace=True)
        records = batch.material
        doc = {
            "scenario": {
                "name": sc.name, "label": sc.label, "purpose": sc.purpose,
                "kind": sc.kind, "notes": sc.notes,
                "runs": batch.runs, "seed": batch.base_seed,
                "enemy": _enemy_doc(sc.enemy),
                "requirements": {k: v for k, v in sc.requirements.items()},
                "rewards": {k: v for k, v in sc.rewards.items()},
                "builds": list(batch.order),
            },
            "rules": {n: rule_digest(rules_by_build[n]) for n in batch.order},
            "builds": {n: _build_doc(meta[n]) for n in batch.order},
            "records": records,
            "table": _table_from_records(records, sc, meta),
        }
        _atomic_write(out / f"{name}.json", doc)
        summary["generated"].append({"scenario": name, "records": sum(
            len(v) for v in records.values())})

    _atomic_write(out / "index.json", {
        "scenarios": [g["scenario"] for g in summary["generated"]],
        "counts": {g["scenario"]: g["records"] for g in summary["generated"]},
        "skipped": summary["skipped"],
        "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}",
        "note": "由 combat_sim reference 生成；展示表可由 records 复算，不含手工数据。",
    })
    print(f"参考批次已写入 {out.resolve()}")
    for g in summary["generated"]:
        print(f"  {g['scenario']}: {g['records']} 场原始记录")
    for s in summary["skipped"]:
        print(f"  跳过 {s['scenario']}: {s['reason']}")
    return 0


def _enemy_doc(e):
    return {k: getattr(e, k) for k in (
        "name", "max_hp", "attack_interval", "damage", "time_limit",
        "damage_variance", "crit_chance", "crit_mult", "enrage_at",
        "enrage_damage_mult", "enrage_interval_mult")}


def _build_doc(b):
    return {
        "name": b.name, "label": b.label, "max_hp": b.max_hp,
        "max_resource": b.max_resource, "resource_regen": b.resource_regen,
        "crit_chance": b.crit_chance, "crit_mult": b.crit_mult, "haste": b.haste,
        "abilities": [
            {"name": a.name, "type": a.type, "cost": a.cost, "cooldown": a.cooldown,
             "cast_time": a.cast_time, "damage": a.damage, "heal": a.heal,
             "restore": a.restore, "charges": a.charges, "dot": a.dot, "buff": a.buff}
            for a in b.abilities],
    }


def _table_from_records(records, scenario, meta):
    """完全从逐场原始记录聚合展示表；与内核对象无关，可独立复算。"""
    rows = {}
    for build_name, recs in records.items():
        rows[build_name] = _aggregate(recs, meta[build_name], scenario)
    return rows


def _aggregate(recs, build, scenario):
    st = summarize_records(recs)
    totals = [compute_reward_from_record(r, build, scenario)["total"] for r in recs]
    rewards = [t for t in totals if t is not None]
    st["reward_mean"] = sum(rewards) / len(rewards) if rewards else None
    return st


def summarize_records(recs):
    """只读原始记录聚合；口径与 stats.summarize 一致（完整样本作分母）。"""
    import statistics
    n = len(recs)
    completed = [r for r in recs if r["completed"]]
    wins = [r for r in recs if r["outcome"] == "victory"]
    kt = sorted(r["kill_time"] for r in wins if r["kill_time"] is not None)
    aborted = sum(1 for r in recs if r["outcome"] == "aborted")
    stopped = sum(1 for r in recs if r["outcome"] == "stopped")
    nc = len(completed)

    def mean_of(key):
        vals = [r["counters"][key] for r in completed if key in r["counters"]]
        return statistics.mean(vals) if vals else None

    dmg_total = sum(r["counters"]["damage_total"] for r in completed)
    spent = sum(r["counters"]["resource_spent"] for r in completed)
    elapsed = sum(r["time"] for r in completed)
    crits = sum(r["counters"]["crits"] for r in completed)
    hits = sum(r["counters"]["hits"] for r in completed)
    return {
        "n": n, "n_completed": nc, "wins": len(wins),
        "defeats": sum(1 for r in recs if r["outcome"] == "defeat"),
        "timeouts": sum(1 for r in recs if r["outcome"] == "timeout"),
        "aborted": aborted, "stopped": stopped,
        "win_rate": (len(wins) / nc) if nc else None,
        "kill_n": len(kt),
        "kill_mean": statistics.mean(kt) if kt else None,
        "kill_median": statistics.median(kt) if kt else None,
        "kill_p90": (kt[min(len(kt) - 1, max(0, -(-9 * len(kt) // 10) - 1))]
                     if len(kt) >= 2 else None),
        "kill_stdev": statistics.stdev(kt) if len(kt) >= 2 else None,
        "starved_time_mean": mean_of("starved_time"),
        "starved_events_mean": mean_of("starved_events"),
        "potions_mean": mean_of("potions_used"),
        "damage_taken_mean": mean_of("damage_taken"),
        "min_resource_mean": mean_of("min_resource"),
        "crit_rate": crits / hits if hits else None,
        "efficiency": dmg_total / spent if spent else None,
        "spend_rate": spent / elapsed if elapsed else None,
    }


def compute_reward_from_record(rec, build, scenario):
    """结算收益只用原始记录里的结局、时间、伤害与终值血量。"""
    zero = {"total": None}
    if rec["outcome"] != "victory":
        return zero
    c = rec["counters"]
    remaining = max(0.0, scenario.enemy.time_limit - rec["time"])
    hp_pct = max(0.0, min(1.0, c.get("final_hp", 0.0) / build.max_hp))
    base = scenario.reward("win_base")
    total = (base + base
             + remaining * scenario.reward("per_remaining_second")
             + c["damage_total"] / 100.0 * scenario.reward("per_damage_hundred")
             + hp_pct * base * scenario.reward("hp_remaining_pct"))
    return {"total": total}


def _atomic_write(path, doc):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def show_from_records(dir_path, scenario_name=None):
    """从已保存的原始记录复算展示表并打印；不重新模拟，不读取任何手填数据。"""
    d = Path(dir_path)
    index = d / "index.json"
    if not index.is_file():
        print(f"未找到参考批次 {d}/index.json，请先运行 reference")
        return 2
    idx = json.loads(index.read_text(encoding="utf-8"))
    names = [scenario_name] if scenario_name else idx.get("scenarios", [])
    for name in names:
        f = d / f"{name}.json"
        if not f.is_file():
            print(f"缺少 {f}")
            return 2
        doc = json.loads(f.read_text(encoding="utf-8"))
        sc = doc["scenario"]
        records = doc["records"]
        meta = {b: BuildStub(doc["builds"][b]["max_hp"]) for b in records}
        rows = {b: _aggregate(recs, meta[b], _ScenarioStub(sc))
                for b, recs in records.items()}
        print("=" * 100)
        print(f"[{sc['label']}] 场景={sc['name']}  对手={sc['enemy']['name']}"
              f"  时限={sc['enemy']['time_limit']}s  n={sc['runs']}  种子={sc['seed']}.."
              f"{sc['seed'] + sc['runs'] - 1}")
        print(f"{'构筑':<12}{'胜率':>8}{'完整':>6}{'胜利':>6}{'战败':>6}{'超时':>6}"
              f"{'失败':>6}{'停止':>6}{'击杀均时':>10}{'中位':>8}{'P90':>8}{'σ':>7}"
              f"{'收益':>8}")
        for b in doc["scenario"]["builds"]:
            r = rows[b]
            label = doc["builds"][b]["label"]
            print(f"{label:<12}{_fmt_rate(r['win_rate']):>8}{r['n_completed']:>6}{r['wins']:>6}"
                  f"{r['defeats']:>6}{r['timeouts']:>6}{r['aborted']:>6}{r['stopped']:>6}"
                  f"{_fmt_num(r['kill_mean'], 's'):>10}{_fmt_num(r['kill_median']):>8}"
                  f"{_fmt_num(r['kill_p90']):>8}{_fmt_num(r['kill_stdev']):>7}"
                  f"{_fmt_num(r['reward_mean'], '', 0):>8}")
        notes = []
        for b in doc["scenario"]["builds"]:
            r = rows[b]
            if r["n_completed"] == 0:
                notes.append(f"{doc['builds'][b]['label']}: 无完整结束样本，胜率/击杀不可计算")
            elif r["wins"] == 0:
                notes.append(f"{doc['builds'][b]['label']}: 有效失败子集，无击杀时间")
            elif r["kill_n"] == 1:
                notes.append(f"{doc['builds'][b]['label']}: 仅 1 个胜利样本，不给 P90/σ")
            elif r["kill_stdev"] == 0.0:
                notes.append(f"{doc['builds'][b]['label']}: 击杀时间零方差，需扩大样本")
        for n in notes:
            print(f"  警示: {n}")
    return 0


def _fmt_rate(v):
    return "不可计算" if v is None else f"{v * 100:.1f}%"


def _fmt_num(v, suffix="", nd=1):
    if v is None:
        return "-"
    return f"{v:.{nd}f}{suffix}"


class BuildStub:
    def __init__(self, max_hp):
        self.max_hp = max_hp


class _ScenarioStub:
    def __init__(self, sc):
        self.enemy = type("E", (), {"time_limit": sc["enemy"]["time_limit"]})()
        self._rewards = sc["rewards"]

    def reward(self, key):
        return self._rewards.get(key, model.REWARD_DEFAULTS[key])


def verify_reference(dir_path):
    d = Path(dir_path)
    index = d / "index.json"
    if not index.is_file():
        print(f"未找到参考批次 {d}/index.json，请先运行 reference")
        return 2
    idx = json.loads(index.read_text(encoding="utf-8"))
    problems = []
    checked = 0
    for name in idx.get("scenarios", []):
        f = d / f"{name}.json"
        if not f.is_file():
            problems.append(f"{name}: 缺少文件 {f.name}")
            continue
        doc = json.loads(f.read_text(encoding="utf-8"))
        checked += _verify_scenario(doc, name, problems)
    if problems:
        print(f"复核失败：{len(problems)} 个问题")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"复核通过：{checked} 场原始记录，事件流独立重算与统计一致、展示表可由记录复算、场景约束全部满足。")
    return 0


def _verify_scenario(doc, name, problems):
    sc_doc = doc["scenario"]
    records = doc["records"]
    checked = 0
    for build, recs in records.items():
        for rec in recs:
            checked += 1
            # 1) 用保存的事件流独立重算，再与保存的计数器比对（不信任预先算好的 matches）
            events = [("event", e[0], e[1], e[2]) for e in rec.get("events", [])]
            if not events:
                problems.append(f"{name}/{build}/seed{rec['seed']}: 缺少事件流，无法复核")
                continue
            recalc = reconcile(rec["counters"], events,
                               final_hp=rec["counters"].get("final_hp"))
            if not recalc["matches"]:
                bad = [k for k, ok in recalc["checks"].items() if not ok]
                problems.append(
                    f"{name}/{build}/seed{rec['seed']}: 事件流重算与统计不一致 {bad}")
            # 2) 规则摘要必须来自文档保存的策略
            if rec.get("rule_summary") != doc["rules"].get(build):
                problems.append(f"{name}/{build}/seed{rec['seed']}: 规则摘要与策略文件不一致")
    # 3) 场景约束：分母、胜率、异常、停止、零方差
    for build, recs in records.items():
        st = summarize_records(recs)
        req = sc_doc.get("requirements", {})
        errs, _ = _requirement_errors(st, req, name, build)
        problems.extend(errs)
        # 4) 展示表必须能由原始记录复算
        saved = doc["table"].get(build, {})
        for key in ("n_completed", "wins", "defeats", "timeouts", "aborted", "stopped",
                    "win_rate", "kill_mean", "kill_n"):
            if not _same(saved.get(key), st.get(key)):
                problems.append(
                    f"{name}/{build}: 展示表字段 {key}={saved.get(key)} 与记录复算 {st.get(key)} 不一致")
    return checked


def _same(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 1e-6 * max(1.0, abs(b))
    return a == b


def _requirement_errors(st, req, scenario_name, build):
    errs = []
    min_completed = req.get("min_completed")
    if min_completed is not None and st["n_completed"] < min_completed:
        errs.append(f"{scenario_name}/{build}: 完整样本 {st['n_completed']} < 要求 {min_completed}")
    min_wr = req.get("min_win_rate")
    if min_wr is not None and st["win_rate"] is not None and st["win_rate"] < min_wr:
        errs.append(f"{scenario_name}/{build}: 胜率 {st['win_rate']:.1%} < 要求 {min_wr:.0%}")
    max_to = req.get("max_timeouts")
    if max_to is not None and st["timeouts"] > max_to:
        errs.append(f"{scenario_name}/{build}: 超时 {st['timeouts']} > 上限 {max_to}")
    max_ab = req.get("max_aborted")
    if max_ab is not None and st["aborted"] > max_ab:
        errs.append(f"{scenario_name}/{build}: 模拟失败 {st['aborted']} > 上限 {max_ab}")
    max_sp = req.get("max_stopped")
    if max_sp is not None and st["stopped"] > max_sp:
        errs.append(f"{scenario_name}/{build}: 主动停止 {st['stopped']} > 上限 {max_sp}")
    if st["kill_n"] == 1:
        errs.append(f"{scenario_name}/{build}: 只有 1 个胜利样本，不可给出稳定性结论")
    return errs, []


def check_constraints(scenario, data_dir=None):
    """只报告约束满足情况，不中断；用于查看「部分构筑不满足」这类真实数据。

    返回 (batch, per_build, ok)。每项含胜率、结局分布、违反条目与口径警示。
    """
    batch, meta, rules_by_build = run_scenario(scenario, data_dir)
    per_build = {}
    ok = True
    for name in batch.order:
        st = summarize(batch.results[name])
        errs, warns = _requirement_errors(
            st, scenario.requirements, scenario.name, name)
        # 警示只作提示，不算违反场景阈值
        per_build[name] = {
            "label": meta[name].label, "stats": st,
            "violations": errs, "advisories": warns,
        }
        ok = ok and not errs
    return batch, per_build, ok
