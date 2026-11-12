"""测试与自检共用的合成样本：覆盖五类结局，用于校验统计分母。"""

from .kernel import Kernel
from .model import Ability, Build, Enemy, Rule


def _build(name, **kw):
    base = dict(name=name, label=name, max_hp=300, max_resource=100, resource_regen=10,
                crit_chance=0.2, crit_mult=1.5, haste=0.0,
                abilities=(
                    Ability(name="hit", type="damage", damage=60, cost=5),
                    Ability(name="heal", type="heal", heal=200, cost=5, cooldown=5),
                ))
    base.update(kw)
    return Build(**base)


def _enemy(**kw):
    base = dict(name="合成木桩", max_hp=500, attack_interval=1.0, damage=40,
                time_limit=20.0, enrage_at=None)
    base.update(kw)
    return Enemy(**base)


HIT = [Rule("打", {"always": True}, "hit")]


def synthetic_runs():
    """返回胜/败/超时/模拟失败/主动停止各若干场，用于分母口径自检。"""
    b = _build("synthetic")
    runs = []
    # victory
    runs.append(Kernel(b, _enemy(max_hp=300), HIT, seed=1).run())
    # defeat
    runs.append(Kernel(b, _enemy(damage=10000), HIT, seed=1).run())
    # timeout
    runs.append(Kernel(b, _enemy(max_hp=10 ** 9, damage=1, time_limit=5.0), HIT, seed=1).run())
    # aborted (bad rule)
    runs.append(Kernel(b, _enemy(), [Rule("坏", {"nope": 1}, "hit")], seed=1).run())
    # stopped
    runs.append(Kernel(b, _enemy(max_hp=10 ** 9, damage=1, time_limit=30.0), HIT, seed=1,
                       should_stop=lambda k: k.t >= 1.0).run())
    return runs
