"""统计聚合：只对 VICTORY 计成功；ABORTED 单独统计并排除出指标均值。"""

import math
import statistics

from .kernel import Outcome


def _pctile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(q * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def summarize(results):
    n = len(results)
    wins = [r for r in results if r.outcome == Outcome.VICTORY]
    valid = [r for r in results if r.outcome != Outcome.ABORTED]
    kt = sorted(r.kill_time for r in wins)

    def mean_of(key):
        vals = [r.counters[key] for r in valid]
        return statistics.mean(vals) if vals else None

    total_damage = spent = spent_time = 0.0
    crits = hits = 0
    dmg = {}
    for r in valid:
        for k, v in r.counters["damage_by_ability"].items():
            dmg[k] = dmg.get(k, 0.0) + v
        total_damage += r.counters["damage_total"]
        spent += r.counters["resource_spent"]
        spent_time += r.time
        crits += r.counters["crits"]
        hits += r.counters["hits"]

    median_seed = None
    if kt:
        med = statistics.median(kt)
        median_seed = min(wins, key=lambda r: abs(r.kill_time - med)).seed

    return {
        "n": n, "n_valid": len(valid),
        "wins": len(wins),
        "defeats": sum(1 for r in results if r.outcome == Outcome.DEFEAT),
        "timeouts": sum(1 for r in results if r.outcome == Outcome.TIMEOUT),
        "aborted": sum(1 for r in results if r.outcome == Outcome.ABORTED),
        "win_rate": len(wins) / n if n else 0.0,
        "kill_mean": statistics.mean(kt) if kt else None,
        "kill_median": statistics.median(kt) if kt else None,
        "kill_p90": _pctile(kt, 0.9),
        "kill_min": kt[0] if kt else None,
        "kill_max": kt[-1] if kt else None,
        "starved_time_mean": mean_of("starved_time"),
        "starved_events_mean": mean_of("starved_events"),
        "potions_mean": mean_of("potions_used"),
        "damage_taken_mean": mean_of("damage_taken"),
        "min_resource_mean": mean_of("min_resource"),
        "damage_shares": {k: v / total_damage for k, v in dmg.items()} if total_damage else {},
        "crit_rate": crits / hits if hits else None,
        "efficiency": total_damage / spent if spent else None,
        "spend_rate": spent / spent_time if spent_time else None,
        "median_seed": median_seed,
    }


def paired_compare(rs_a, rs_b):
    """同种子配对：双方都胜利的种子里，A 击杀更快的比例。"""
    a = {r.seed: r.kill_time for r in rs_a if r.outcome == Outcome.VICTORY}
    b = {r.seed: r.kill_time for r in rs_b if r.outcome == Outcome.VICTORY}
    common = sorted(set(a) & set(b))
    faster = sum(1 for s in common if a[s] < b[s])
    return {"n": len(common), "a_faster": faster}
