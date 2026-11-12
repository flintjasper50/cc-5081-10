"""收益计算：按真实模拟计数器的终值结算，不做手填。

战斗演练与收益统计共用这一份 compute_reward；图形界面和命令行展示的收益
都由同一场 RunResult 现场算出，异常场（ABORTED/STOPPED）不给任何收益。
"""

from .kernel import Outcome

REWARD_FIELDS = (
    "base", "win_bonus", "speed_bonus", "damage_bonus", "hp_bonus", "total",
)


def compute_reward(result, build, scenario):
    """返回收益明细。只有 VICTORY 有收益；其余结局 total 为 None。"""
    rw = {k: scenario.reward(k) for k in (
        "win_base", "per_remaining_second", "per_damage_hundred", "hp_remaining_pct")}
    zero = {"base": None, "win_bonus": None, "speed_bonus": None,
            "damage_bonus": None, "hp_bonus": None, "total": None}
    if result.outcome != Outcome.VICTORY:
        return zero

    time_limit = scenario.enemy.time_limit
    remaining = max(0.0, time_limit - result.time)
    hp_pct = max(0.0, min(1.0, result.counters["final_hp"] / build.max_hp))
    base = rw["win_base"]
    win_bonus = base
    speed_bonus = remaining * rw["per_remaining_second"]
    damage_bonus = result.counters["damage_total"] / 100.0 * rw["per_damage_hundred"]
    hp_bonus = hp_pct * base * rw["hp_remaining_pct"]
    total = base + win_bonus + speed_bonus + damage_bonus + hp_bonus
    return {
        "base": base, "win_bonus": win_bonus, "speed_bonus": speed_bonus,
        "damage_bonus": damage_bonus, "hp_bonus": hp_bonus, "total": total,
        "remaining_time": remaining, "hp_pct": hp_pct,
    }
