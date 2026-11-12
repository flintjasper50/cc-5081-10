"""统计聚合：成功率只用完整结束样本作分母。

结局分三类，各自计数，绝不混用：
  完整结束 completed = VICTORY + DEFEAT + TIMEOUT（用同一个分母）
  模拟失败 aborted   = 异常、配置错误、步数上限（单独统计，不进任何均值）
  主动停止 stopped   = 调用方中途结束（单独统计，不进任何均值）

没有完整结束样本时，成功率与击杀时间为 None，而不是 0 或 0.0s；
击杀时间只有一个样本时不给 P90，也不参与方差比较。
"""

import math
import statistics

from .kernel import Outcome

COMPLETED = (Outcome.VICTORY, Outcome.DEFEAT, Outcome.TIMEOUT)


def _pctile(sorted_vals, q):
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(q * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def _count(results, outcome):
    return sum(1 for r in results if r.outcome == outcome)


def summarize(results):
    n = len(results)
    wins = [r for r in results if r.outcome == Outcome.VICTORY]
    completed = [r for r in results if r.outcome in COMPLETED]
    aborted = _count(results, Outcome.ABORTED)
    stopped = _count(results, Outcome.STOPPED)
    kt = sorted(r.kill_time for r in wins)

    def mean_of(key):
        vals = [r.counters[key] for r in completed]
        return statistics.mean(vals) if vals else None

    total_damage = spent = spent_time = 0.0
    crits = hits = 0
    dmg = {}
    for r in completed:
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

    n_completed = len(completed)
    completed_denominator = n_completed or None

    return {
        "n": n, "n_completed": n_completed,
        "wins": len(wins),
        "defeats": _count(results, Outcome.DEFEAT),
        "timeouts": _count(results, Outcome.TIMEOUT),
        "aborted": aborted,
        "stopped": stopped,
        "n_excluded": aborted + stopped,
        # 分母是完整结束样本；没有完整样本时返回 None，避免「0% 胜率」的误导
        "win_rate": (len(wins) / completed_denominator) if completed_denominator else None,
        "kill_n": len(kt),
        "kill_mean": statistics.mean(kt) if kt else None,
        "kill_median": statistics.median(kt) if kt else None,
        "kill_p90": _pctile(kt, 0.9) if len(kt) >= 2 else None,
        "kill_min": kt[0] if kt else None,
        "kill_max": kt[-1] if kt else None,
        # 只有一个样本时不计算离散度：单点方差是 0，不能据此下「稳定/不稳定」结论
        "kill_stdev": statistics.stdev(kt) if len(kt) >= 2 else None,
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
    """同种子配对：只在双方都完整结束的种子里比较，返回同种子数、A 更快数、A 胜数。"""
    a = {r.seed: r for r in rs_a if r.outcome in COMPLETED}
    b = {r.seed: r for r in rs_b if r.outcome in COMPLETED}
    common = sorted(set(a) & set(b))
    faster = 0
    a_wins = 0
    for s in common:
        ra, rb = a[s], b[s]
        if ra.outcome == Outcome.VICTORY and rb.outcome == Outcome.VICTORY:
            if ra.kill_time < rb.kill_time:
                faster += 1
        if ra.outcome == Outcome.VICTORY and rb.outcome != Outcome.VICTORY:
            a_wins += 1
    return {"n": len(common), "a_faster": faster, "a_wins": a_wins}


def scenario_warnings(stats, wins_required=30):
    """按统计口径给出可执行的数据警示；返回 (errors, warnings)。"""
    errors, warnings = [], []
    if stats["n_completed"] == 0:
        errors.append("没有完整结束样本：成功率/击杀时间不可计算，请检查配置或延长时限")
    elif stats["wins"] == 0:
        warnings.append(
            f"完整样本 {stats['n_completed']} 个但没有胜利：胜率 0%，无击杀时间可比较；"
            f"这是有效失败子集，不代表构筑相对速度")
    elif stats["wins"] < wins_required:
        warnings.append(
            f"胜利样本仅 {stats['wins']} 个，少于建议的 {wins_required}；"
            f"击杀时间与排名只代表这一小撮样本")
    if stats["aborted"]:
        errors.append(f"有 {stats['aborted']} 场模拟失败(aborted)，已排除出成功率分母；需修复配置")
    if stats["stopped"]:
        warnings.append(f"有 {stats['stopped']} 场主动停止(stopped)，已排除出成功率分母")
    if stats["kill_n"] == 1:
        warnings.append("只有 1 个胜利样本，无法给出 P90 与离散度，不要据此判断稳定性")
    elif stats["kill_n"] and stats["kill_stdev"] == 0.0:
        warnings.append("胜利样本击杀时间完全相同（零方差），多为确定性场景或样本重叠，需扩大样本")
    return errors, warnings
