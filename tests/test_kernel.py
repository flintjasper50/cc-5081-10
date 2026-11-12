import unittest

from combat_sim.kernel import Kernel, Outcome
from combat_sim.model import Ability, Build, Enemy, Rule, load_all, DATA_DIR
from combat_sim.runner import run_single, run_batch
from combat_sim.stats import summarize


def make_build(**kw):
    base = dict(name="t", label="测试", max_hp=300, max_resource=100, resource_regen=10,
                crit_chance=0.2, crit_mult=1.5, haste=0.0,
                abilities=(Ability(name="hit", type="damage", damage=50, cost=5),))
    base.update(kw)
    if "abilities" in kw:
        base["abilities"] = tuple(base["abilities"])
    return Build(**base)


def make_enemy(**kw):
    base = dict(name="木桩", max_hp=1000, attack_interval=1.0, damage=50,
                time_limit=20.0, enrage_at=None)
    base.update(kw)
    return Enemy(**base)


HIT_RULE = [Rule("打", {"always": True}, "hit")]


class TestEmptyActionSet(unittest.TestCase):
    """空动作集：玩家永远不出手，绝不能被记为胜利。"""

    def test_empty_strategy_not_victory(self):
        res = run_single(make_build(), make_enemy(), [], seed=3)
        self.assertIn(res.outcome, (Outcome.DEFEAT, Outcome.TIMEOUT))
        self.assertNotEqual(res.outcome, Outcome.VICTORY)
        self.assertEqual(sum(res.counters["casts_by_ability"].values()), 0)
        self.assertEqual(res.counters["damage_total"], 0.0)

    def test_empty_strategy_batch_zero_wins(self):
        build, enemy = make_build(), make_enemy()
        batch = run_batch([(build, [])], enemy, runs=5, base_seed=1)
        st = summarize(batch.results["t"])
        self.assertEqual(st["wins"], 0)
        self.assertEqual(st["win_rate"], 0.0)
        self.assertEqual(st["defeats"] + st["timeouts"] + st["aborted"], 5)


class TestUnbeatableTarget(unittest.TestCase):
    """无法击败的目标：0 胜率，统计不崩溃，未结束不算成功。"""

    def test_unbeatable_all_timeout_no_wins(self):
        build = make_build(abilities=[Ability(name="hit", type="damage", damage=50, cost=0),
                                      Ability(name="heal", type="heal", heal=500, cost=0,
                                              cooldown=5)])
        rules = [Rule("奶", {"self_hp_pct_lte": 80, "cooldown_ready": "heal"}, "heal"),
                 Rule("打", {"always": True}, "hit")]
        enemy = make_enemy(max_hp=1e12, damage=1, time_limit=15.0)
        batch = run_batch([(build, rules)], enemy, runs=4, base_seed=1)
        st = summarize(batch.results["t"])
        self.assertEqual(st["wins"], 0)
        self.assertEqual(st["timeouts"], 4)
        self.assertIsNone(st["kill_mean"])
        self.assertIsNone(st["kill_p90"])
        self.assertEqual(st["win_rate"], 0.0)


class TestRandomStreamReplay(unittest.TestCase):
    """随机流：同种子完全可重放；换构筑不改变无关流的抽样序列。"""

    def setUp(self):
        self.enemy = make_enemy(max_hp=4000, damage=30, attack_interval=2.0, time_limit=60)
        self.build_a = make_build(crit_chance=0.2,
                                  abilities=[Ability(name="hit", type="damage", damage=60, cost=0)])
        self.build_b = make_build(name="t2", crit_chance=0.9, haste=0.5,
                                  abilities=[Ability(name="hit", type="damage", damage=35, cost=0)])

    def test_same_seed_identical_trace(self):
        r1 = run_single(self.build_a, self.enemy, HIT_RULE, seed=11, trace=True)
        r2 = run_single(self.build_a, self.enemy, HIT_RULE, seed=11, trace=True)
        self.assertEqual(r1.trace, r2.trace)
        self.assertEqual(r1.kill_time, r2.kill_time)

    def test_unrelated_streams_unchanged_by_build_switch(self):
        ra = run_single(self.build_a, self.enemy, HIT_RULE, seed=11, trace=True)
        rb = run_single(self.build_b, self.enemy, HIT_RULE, seed=11, trace=True)

        def stream_draws(res, stream):
            out = []
            for e in res.trace:
                if e[0] == "draw" and e[2] == stream:
                    out.append((e[3], e[4]))  # (index, value)
            return out

        for stream in ("enemy_damage", "enemy_crit", "player_crit"):
            da, db = stream_draws(ra, stream), stream_draws(rb, stream)
            self.assertTrue(da, f"{stream} 应有抽样")
            k = min(len(da), len(db))
            # 同一下标的抽样值必须一致：构筑切换不改变无关流的抽样顺序
            self.assertEqual(da[:k], db[:k], f"{stream} 流被构筑切换污染")

    def test_run_order_does_not_leak(self):
        run_single(self.build_a, self.enemy, HIT_RULE, seed=1)
        run_single(self.build_a, self.enemy, HIT_RULE, seed=2)
        r_alone = run_single(self.build_a, self.enemy, HIT_RULE, seed=5, trace=True)
        r_after = run_single(self.build_a, self.enemy, HIT_RULE, seed=5, trace=True)
        self.assertEqual(r_alone.trace, r_after.trace)


class TestUnfinishedNotSuccess(unittest.TestCase):
    """未终结的模拟（步数上限/异常）记 ABORTED，单独统计，绝不算成功。"""

    def test_step_limit_is_aborted_not_success(self):
        k = Kernel(make_build(), make_enemy(), HIT_RULE, seed=1, max_steps=5)
        res = k.run()
        self.assertEqual(res.outcome, Outcome.ABORTED)
        self.assertIn("步数上限", res.error)
        st = summarize([res])
        self.assertEqual(st["wins"], 0)
        self.assertEqual(st["aborted"], 1)
        self.assertEqual(st["win_rate"], 0.0)

    def test_invalid_strategy_is_aborted_not_defeat(self):
        bad = [Rule("坏规则", {"nonexistent_condition": 1}, "hit")]
        res = run_single(make_build(), make_enemy(), bad, seed=1)
        self.assertEqual(res.outcome, Outcome.ABORTED)
        self.assertIn("未知条件", res.error)

    def test_unknown_ability_is_aborted(self):
        bad = [Rule("坏技能", {"always": True}, "no_such_spell")]
        res = run_single(make_build(), make_enemy(), bad, seed=1)
        self.assertEqual(res.outcome, Outcome.ABORTED)


class TestSurvivalAndResourceRules(unittest.TestCase):
    def test_deadly_enemy_defeats_player(self):
        enemy = make_enemy(damage=10000, attack_interval=1.0)
        res = run_single(make_build(), enemy, HIT_RULE, seed=1)
        self.assertEqual(res.outcome, Outcome.DEFEAT)
        self.assertGreater(res.counters["damage_taken"], 0)

    def test_resource_never_negative_and_starved_tracked(self):
        build = make_build(max_resource=50, resource_regen=0,
                           abilities=[Ability(name="big", type="damage", damage=10, cost=100)])
        rules = [Rule("大技能", {"always": True}, "big")]
        res = run_single(build, make_enemy(time_limit=5.0), rules, seed=1)
        self.assertGreaterEqual(res.counters["min_resource"], 0.0)
        self.assertGreater(res.counters["starved_events"], 0)
        self.assertEqual(res.counters["casts_by_ability"].get("big", 0), 0)
        self.assertEqual(res.counters["damage_total"], 0.0)


class TestShippedData(unittest.TestCase):
    """内置三套构筑：批量确定性、都能取胜、指标齐备。"""

    def test_batch_deterministic_and_winnable(self):
        enemy, specs = load_all()
        b1 = run_batch(specs, enemy, runs=8, base_seed=100)
        b2 = run_batch(specs, enemy, runs=8, base_seed=100)
        for name in b1.order:
            kt1 = [r.kill_time for r in b1.results[name]]
            kt2 = [r.kill_time for r in b2.results[name]]
            self.assertEqual(kt1, kt2)
            st = summarize(b1.results[name])
            self.assertEqual(st["aborted"], 0)
            self.assertGreater(st["wins"], 0, f"{name} 应能取胜")
            self.assertIsNotNone(st["kill_mean"])


if __name__ == "__main__":
    unittest.main()
