"""命令行入口（辅助构筑演练；图形入口见 README 的 play 子命令说明）。

  python -m combat_sim scenarios                   列出全部场景（含图形演练用的 battle_practice）
  python -m combat_sim batch --scenario baseline   按场景批量对比（默认 baseline）
  python -m combat_sim play --build burst --seed 7 单场播放（--show-draws 显示随机抽样）
  python -m combat_sim play --build burst --seed 7 --scenario survival_pressure
  python -m combat_sim reference                   生成短时参考批次并逐场落盘到 runs/
  python -m combat_sim verify                      复核参考批次：事件与统计一致、场景约束满足
  python -m combat_sim list                        列出构筑/策略与可用条件语法

图形演练入口（构筑演练界面）由 `python -m combat_sim gui` 启动，能选择构筑、
播放单场并核对战斗过程与收益统计；本命令行仅辅助与校验。
"""

import argparse
import sys

from . import checks, gui_support, model, reference, view
from .rewards import compute_reward
from .runner import run_scenario, run_single

CONDITIONS = """可用条件键（同一规则内为 AND 关系）:
  always: true                     恒真（兜底填充）
  self_hp_pct_lte / _gte: 百分比   自身血量百分比
  enemy_hp_pct_lte / _gte: 百分比  目标血量百分比
  resource_gte / resource_lte: 值  当前资源
  cooldown_ready: "技能名"         冷却就绪
  debuff_remaining_lte: ["技能名", 秒]  目标 DoT 剩余时间
  buff_active: "增益名"            自身增益生效中
  charges_gte: ["技能名", 数量]    剩余充能
  time_gte: 秒                     战斗时长
规则按列表顺序求值，命中第一条可执行（冷却/资源/充能满足）的规则；
资源不足会记 starved 事件并尝试后续规则。技能名写错或条件未知 => 该场记为 ABORTED。
结局口径: victory/defeat/timeout 为完整结束样本，是成功率分母；
         aborted（模拟失败）与 stopped（主动停止）单独统计，绝不计入分母。"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="combat_sim", description="战斗构筑演练器")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scenarios", help="列出全部场景")

    b = sub.add_parser("batch", help="按场景批量运行并对比构筑")
    b.add_argument("--scenario", default="baseline", help="场景名（默认 baseline）")
    b.add_argument("--runs", type=int, default=None, help="覆盖场景的每构筑重复次数")
    b.add_argument("--seed", type=int, default=None, help="覆盖场景起始种子")
    b.add_argument("--data", default=None, help="自定义数据目录（默认用内置 data/）")

    pl = sub.add_parser("play", help="单场播放")
    pl.add_argument("--build", required=True)
    pl.add_argument("--seed", type=int, default=1)
    pl.add_argument("--scenario", default="baseline")
    pl.add_argument("--data", default=None)
    pl.add_argument("--show-draws", action="store_true", help="同时显示随机流抽样")

    ref = sub.add_parser("reference", help="生成短时参考批次并逐场保存原始记录")
    ref.add_argument("--out", default="runs", help="输出目录（默认 runs/）")
    ref.add_argument("--data", default=None)

    ver = sub.add_parser("verify", help="复核参考批次：事件统计一致、场景约束满足")
    ver.add_argument("--dir", default="runs", help="参考批次目录（默认 runs/）")

    sh = sub.add_parser("show-runs", help="从参考批次原始记录复算展示表（不重新模拟）")
    sh.add_argument("--dir", default="runs", help="参考批次目录（默认 runs/）")
    sh.add_argument("--scenario", default=None, help="只显示指定场景")

    sub.add_parser("list", help="列出构筑、策略与条件语法")
    cs = sub.add_parser("check", help="仓库自检：配置引用、终止条件、统计分母")
    cs.add_argument("--data", default=None, help="自定义数据目录（默认内置 data/）")
    cs.add_argument("--skip-run", action="store_true", help="跳过实跑终止条件检查")
    cc = sub.add_parser("constraint-check", help="按场景阈值报告构筑约束满足情况（不中断）")
    cc.add_argument("--scenario", required=True)
    cc.add_argument("--data", default=None)
    sub.add_parser("gui", help="打开构筑演练图形入口（战斗演练场）")

    args = p.parse_args(argv)

    if args.cmd == "scenarios":
        for sc in sorted(model.load_scenarios().values(), key=lambda s: s.name):
            print(f"{sc.name:<22} {sc.label:<22} kind={sc.kind:<14} runs={sc.runs} seed={sc.seed}")
            print(f"    {sc.purpose}")
        return 0

    if args.cmd == "batch":
        sc = model.load_scenario_by_name(args.scenario, args.data)
        batch, meta, _ = run_scenario(sc, args.data, runs=args.runs, base_seed=args.seed)
        rewards = {name: _mean_reward(batch, meta[name], sc) for name in batch.order}
        print(view.batch_report(batch, meta, rewards=rewards))
        return 0

    if args.cmd == "play":
        sc = model.load_scenario_by_name(args.scenario, args.data)
        _, specs = model.load_all(args.data, [args.build], enemy=sc.enemy)
        build, rules = specs[0]
        result = run_single(build, sc.enemy, rules, seed=args.seed, trace=True,
                            scenario=sc.name)
        reward = compute_reward(result, build, sc)
        print(view.playback(result, build, show_draws=args.show_draws,
                            scenario=sc, reward=reward))
        return 0

    if args.cmd == "reference":
        return reference.build_reference(args.out, args.data)

    if args.cmd == "verify":
        return reference.verify_reference(args.dir)

    if args.cmd == "show-runs":
        return reference.show_from_records(args.dir, args.scenario)

    if args.cmd == "check":
        rep = checks.check_all(args.data, run_termination=not args.skip_run)
        print(f"仓库自检：构筑 {rep.checked.get('builds', 0)} 套、规则 "
              f"{rep.checked.get('rules', 0)} 条、场景 {rep.checked.get('scenarios', 0)} 个；"
              f"分母口径 {rep.checked.get('denominator', '-')}")
        for w in rep.warnings:
            print(f"  警示: {w}")
        for e in rep.errors:
            print(f"  错误: {e}")
        if rep.errors:
            print(f"自检失败：{len(rep.errors)} 个错误")
            return 1
        print("自检通过：配置引用、终止条件与统计分母均符合口径。")
        return 0

    if args.cmd == "constraint-check":
        sc = model.load_scenario_by_name(args.scenario, args.data)
        batch, per_build, ok = reference.check_constraints(sc, args.data)
        print(f"场景 {sc.label}（{sc.name}）  约束最低完整样本={sc.requirement('min_completed')}"
              f"  最低胜率={sc.requirement('min_win_rate'):.0%}")
        for name, info in per_build.items():
            st = info["stats"]
            print(f"\n[{info['label']}] 完整={st['n_completed']} 胜利={st['wins']}"
                  f" 战败={st['defeats']} 超时={st['timeouts']}"
                  f" 模拟失败={st['aborted']} 停止={st['stopped']}"
                  f" 胜率={view._rate(st['win_rate'])}"
                  f" 击杀={view._s(st['kill_mean'])}s")
            for v in info["violations"]:
                print(f"  不满足: {v}")
            for w in info["advisories"]:
                print(f"  警示: {w}")
            if not info["violations"]:
                print("  满足场景约束")
        print("\n结论: " + ("全部构筑满足约束" if ok
                          else "部分构筑不满足场景约束（上述失败为有效结局，不是模拟失败）；"
                               "展示表需按构筑分别标注，不能混为一个平均结论"))
        return 0

    if args.cmd == "gui":
        return gui_support.launch()

    # list
    enemy, specs = model.load_all(None)
    print(f"对手: {enemy.name}  HP={enemy.max_hp}  攻击={enemy.damage}/{enemy.attack_interval}s"
          f"  狂暴={enemy.enrage_at}s  时限={enemy.time_limit}s")
    for build, rules in specs:
        print(f"\n构筑 [{build.label}]({build.name})  HP={build.max_hp} "
              f"资源={build.max_resource}(+{build.resource_regen}/s) "
              f"暴击={build.crit_chance}/{build.crit_mult} 急速={build.haste}")
        for a in build.abilities:
            print(f"  技能 {a.name}: type={a.type} 伤害={a.damage} 治疗={a.heal} "
                  f"消耗={a.cost} 冷却={a.cooldown} 读条={a.cast_time}"
                  + (f" DoT={a.dot}" if a.dot else "")
                  + (f" 增益={a.buff}" if a.buff else "")
                  + (f" 充能={a.charges}" if a.charges is not None else ""))
        print("  策略（按优先级）:")
        for r in rules:
            print(f"    - {r.name}: 若 {r.cond} 则 {r.action}")
    print("\n" + CONDITIONS)
    return 0


def _mean_reward(batch, build, scenario):
    totals = []
    for r in batch.results[build.name]:
        rw = compute_reward(r, build, scenario)
        if rw["total"] is not None:
            totals.append(rw["total"])
    return sum(totals) / len(totals) if totals else None


if __name__ == "__main__":
    sys.exit(main())
