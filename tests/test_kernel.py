import unittest

from combat_sim import model, reference
from combat_sim.kernel import Kernel, Outcome
from combat_sim.model import Ability, Build, Enemy, Rule, load_all, DATA_DIR
from combat_sim.runner import run_single, run_batch, run_scenario, verify_event_totals
from combat_sim.stats import summarize, scenario_warnings
from combat_sim.gui_support import build_payload


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
        self.assertEqual(st["defeats"] + st["timeouts"], 5)
        self.assertEqual(st["n_completed"], 5)
        self.assertEqual(st["aborted"], 0)


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
        self.assertEqual(st["n_completed"], 0)
        # 没有完整结束样本：成功率不可计算（None），不能显示 0% 误导读者
        self.assertIsNone(st["win_rate"])
        self.assertIsNone(st["kill_mean"])

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


class TestDenominatorHonesty(unittest.TestCase):
    """分母口径：成功率只用完整结束样本；异常与主动停止绝不混入。"""

    def test_aborted_excluded_from_denominator(self):
        build, enemy = make_build(), make_enemy()
        rules = [Rule("坏", {"no_such": 1}, "hit")]
        rs = [run_single(build, enemy, rules, seed=i) for i in range(3)]
        st = summarize(rs)
        self.assertEqual(st["n_completed"], 0)
        self.assertEqual(st["aborted"], 3)
        self.assertIsNone(st["win_rate"])

    def test_one_aborted_does_not_drag_win_rate(self):
        build, enemy = make_build(), make_enemy(max_hp=300)
        good = run_single(build, enemy, HIT_RULE, seed=1)
        self.assertEqual(good.outcome, Outcome.VICTORY)
        bad = Kernel(build, enemy, HIT_RULE, seed=2, max_steps=1).run()
        st = summarize([good, bad])
        self.assertEqual(st["n"], 2)
        self.assertEqual(st["n_completed"], 1)
        self.assertEqual(st["aborted"], 1)
        self.assertEqual(st["win_rate"], 1.0)  # 分母 1，不是 2

    def test_stopped_not_counted_as_failure(self):
        build, enemy = make_build(), make_enemy(max_hp=1e9, damage=1, time_limit=100.0)
        res = run_single(build, enemy, HIT_RULE, seed=1,
                         should_stop=lambda k: k.t >= 2.0)
        self.assertEqual(res.outcome, Outcome.STOPPED)
        self.assertEqual(res.stop_reason, "主动停止")
        self.assertFalse(res.outcome.completed)
        st = summarize([res])
        self.assertEqual(st["stopped"], 1)
        self.assertEqual(st["aborted"], 0)
        self.assertIsNone(st["win_rate"])

    def test_single_win_has_no_p90_or_stdev(self):
        build, enemy = make_build(), make_enemy(max_hp=300)
        st = summarize([run_single(build, enemy, HIT_RULE, seed=1)])
        self.assertEqual(st["wins"], 1)
        self.assertIsNone(st["kill_p90"])
        self.assertIsNone(st["kill_stdev"])


class TestScenarioConstraints(unittest.TestCase):
    """场景约束与警示：无胜利、零方差、无完整样本都要显式提示。"""

    def test_zero_win_scenario_warns(self):
        st = summarize([run_single(make_build(), make_enemy(max_hp=1e9, damage=1),
                                   HIT_RULE, seed=1)])
        errs, warns = scenario_warnings(st)
        self.assertFalse(errs)
        self.assertTrue(any("没有胜利" in w for w in warns))

    def test_zero_variance_warns(self):
        build, enemy = make_build(), make_enemy(max_hp=300)
        rs = [run_single(build, enemy, [Rule("打", {"always": True}, "hit")], seed=1)
              for _ in range(3)]
        st = summarize(rs)
        self.assertEqual(st["kill_stdev"], 0.0)
        _, warns = scenario_warnings(st)
        self.assertTrue(any("零方差" in w for w in warns))

    def test_shipped_scenarios_are_constraint_clean(self):
        scenarios = model.load_scenarios()
        for name in reference.REFERENCE_SCENARIOS:
            sc = scenarios[name]
            batch, _, _ = run_scenario(sc, runs=min(sc.runs, 12))
            for build in batch.order:
                st = summarize(batch.results[build])
                errs, _ = scenario_warnings(st, wins_required=1)
                self.assertEqual(errs, [], f"{name}/{build} 出现口径错误: {errs}")


class TestScenarioDataConsistency(unittest.TestCase):
    """场景引用的构筑与策略必须存在，且配置可解析。"""

    def test_all_scenarios_reference_existing_builds(self):
        scenarios = model.load_scenarios()
        for name, sc in scenarios.items():
            _, specs = model.load_all(None, list(sc.builds))
            self.assertEqual(sorted(b.name for b, _ in specs), sorted(sc.builds))

    def test_empty_example_is_runnable_and_uncomputable(self):
        ex = DATA_DIR.parent.parent / "examples" / "empty_no_valid"
        sc = model.load_scenario_by_name("no_valid_samples", ex)
        batch, meta, _ = run_scenario(sc, data_dir=ex)
        st = summarize(batch.results["noop"])
        self.assertEqual(st["n_completed"], 0)
        self.assertEqual(st["aborted"], sc.runs)
        self.assertIsNone(st["win_rate"])
        self.assertIsNone(st["kill_mean"])


class TestEventReconciliation(unittest.TestCase):
    """事件流与统计记录必须逐项一致；图形界面的核对结果必须为通过。"""

    def test_events_reconcile_with_counters(self):
        enemy, specs = load_all()
        build, rules = specs[0]
        res = run_single(build, enemy, rules, seed=3, trace=True)
        ev = verify_event_totals(res)
        self.assertTrue(ev["matches"], ev["checks"])

    def test_starved_events_reconcile(self):
        build = make_build(max_resource=50, resource_regen=0,
                           abilities=[Ability(name="big", type="damage", damage=10, cost=100)])
        res = run_single(build, make_enemy(time_limit=5.0),
                         [Rule("大", {"always": True}, "big")], seed=1, trace=True)
        ev = verify_event_totals(res)
        self.assertTrue(ev["matches"], ev["checks"])
        self.assertGreater(res.counters["starved_events"], 0)

    def test_gui_payload_reconciles(self):
        sc = model.load_scenario_by_name("battle_practice")
        for build_name in sc.builds:
            payload = build_payload(build_name, 5, sc)
            self.assertTrue(payload["reconciled"], payload["mismatch"])
            self.assertEqual(payload["stats"]["outcome"], "victory")
            self.assertIsNotNone(payload["reward"]["total"])

    def test_gui_reports_aborted_as_error(self):
        # 配置错误的构筑在图形入口必须提示异常，而不是静默展示收益
        sc = model.load_scenario_by_name("no_valid_samples",
                                         DATA_DIR.parent.parent / "examples" / "empty_no_valid")
        payload = build_payload("noop", 1, sc,
                                DATA_DIR.parent.parent / "examples" / "empty_no_valid")
        self.assertEqual(payload["stats"]["outcome"], "aborted")
        self.assertIsNone(payload["reward"]["total"])


class TestReferenceRoundTrip(unittest.TestCase):
    """参考批次：清洁生成 → 复核通过；篡改后复核必须失败。"""

    def test_generate_and_verify(self):
        import tempfile, json, shutil, os
        with tempfile.TemporaryDirectory() as td:
            rc = reference.build_reference(td)
            self.assertEqual(rc, 0)
            self.assertEqual(reference.verify_reference(td), 0)

            doc_path = os.path.join(td, "baseline.json")
            doc = json.load(open(doc_path, encoding="utf-8"))
            doc["records"]["burst"][0]["counters"]["damage_total"] += 100
            json.dump(doc, open(doc_path, "w", encoding="utf-8"), ensure_ascii=False)
            self.assertEqual(reference.verify_reference(td), 1)

            doc_path = os.path.join(td, "baseline.json")
            doc = json.load(open(doc_path, encoding="utf-8"))
            doc["records"]["burst"][0]["counters"]["damage_total"] += 100
            json.dump(doc, open(doc_path, "w", encoding="utf-8"), ensure_ascii=False)
            self.assertEqual(reference.verify_reference(td), 1)


class TestDocsMatchCode(unittest.TestCase):
    """开发文档里的可核对数字必须与当前代码一致，防止文档过期。"""

    def _readme(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "README.md").read_text(
            encoding="utf-8")

    def test_readme_replay_numbers_match_gui(self):
        txt = self._readme()
        sc = model.load_scenario_by_name("battle_practice")
        payload = build_payload("burst", 1, sc)
        expected = [
            "20767.6", "6/12", "1340.0", "1810.0", "302.8",
            "9187.2", "10813",
        ]
        for tok in expected:
            self.assertIn(tok, txt, f"README 缺少或已过期的数字: {tok}")
        self.assertEqual(payload["events"][0]["text"], "施放 surge")

    def test_readme_scenario_names_exist(self):
        txt = self._readme()
        for name in ("baseline", "survival_pressure", "resource_exhaustion",
                     "battle_practice", "no_valid_samples", "partial_not_satisfied"):
            self.assertIn(name, txt)
        for name in reference.REFERENCE_SCENARIOS:
            self.assertIn(name, model.load_scenarios())

    def test_readme_commands_resolve(self):
        """文档列出的关键子命令必须存在。"""
        import inspect
        from combat_sim import cli
        source = inspect.getsource(cli.main)
        for sub in ("batch", "play", "scenarios", "reference", "verify", "show-runs",
                    "check", "constraint-check", "gui", "list"):
            self.assertIn(f'"{sub}"', source, f"CLI 缺少文档声明的子命令 {sub}")
