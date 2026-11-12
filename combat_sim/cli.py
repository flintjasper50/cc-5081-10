"""命令行入口。

  python -m combat_sim batch --runs 200 --seed 1     批量对比全部构筑
  python -m combat_sim play --build burst --seed 7   单场播放（--show-draws 显示随机抽样）
  python -m combat_sim list                          列出构筑/策略与可用条件语法
"""

import argparse

from . import model, view
from .runner import run_batch, run_single

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
资源不足会记录 starved 事件并尝试后续规则。技能名写错或条件未知 => 该场记为 ABORTED。
"""


def main(argv=None):
    p = argparse.ArgumentParser(prog="combat_sim", description="战斗构筑演练器")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("batch", help="批量运行并对比构筑")
    b.add_argument("--runs", type=int, default=200, help="每构筑重复次数")
    b.add_argument("--seed", type=int, default=1, help="起始种子")
    b.add_argument("--data", default=None, help="自定义数据目录（默认用内置 data/）")
    b.add_argument("--builds", default=None, help="逗号分隔的构筑名，默认全部")

    pl = sub.add_parser("play", help="单场播放")
    pl.add_argument("--build", required=True)
    pl.add_argument("--seed", type=int, default=1)
    pl.add_argument("--data", default=None)
    pl.add_argument("--show-draws", action="store_true", help="同时显示随机流抽样")

    sub.add_parser("list", help="列出构筑、策略与条件语法")

    args = p.parse_args(argv)

    if args.cmd == "batch":
        names = args.builds.split(",") if args.builds else None
        enemy, specs = model.load_all(args.data, names)
        batch = run_batch(specs, enemy, runs=args.runs, base_seed=args.seed)
        meta = {b.name: b for b, _ in specs}
        print(view.batch_report(batch, meta))
    elif args.cmd == "play":
        enemy, specs = model.load_all(args.data, [args.build])
        build, rules = specs[0]
        result = run_single(build, enemy, rules, seed=args.seed, trace=True)
        print(view.playback(result, build, show_draws=args.show_draws))
    elif args.cmd == "list":
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
                      + (f" 充能={a.charges}" if a.charges else ""))
            print("  策略（按优先级）:")
            for r in rules:
                print(f"    - {r.name}: 若 {r.cond} 则 {r.action}")
        print("\n" + CONDITIONS)


if __name__ == "__main__":
    main()
