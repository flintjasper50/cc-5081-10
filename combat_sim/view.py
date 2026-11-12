"""结果视图：批量对比报告、单场回放、结论生成。"""

from .stats import summarize, paired_compare


def _s(v, fmt="{:.1f}"):
    return fmt.format(v) if v is not None else "-"


def batch_report(batch, builds_meta):
    stats = {name: summarize(rs) for name, rs in batch.results.items()}
    L = []
    L.append("=" * 78)
    L.append(f"批量对比报告  对手: {batch.enemy_name}  场景时限: {batch.time_limit:.0f}s")
    L.append(f"每构筑样本 n={batch.runs}（种子 {batch.base_seed}..{batch.base_seed + batch.runs - 1}，"
             f"各构筑使用同一组种子，可配对比较）")
    L.append("-" * 78)
    header = (f"{'构筑':<10}{'胜率':>7}{'击杀均时':>9}{'中位':>7}{'P90':>7}"
              f"{'战败':>5}{'超时':>5}{'异常':>5}{'资源受限均时':>11}{'受限事件':>8}{'承伤':>8}")
    L.append(header)
    for name in batch.order:
        st = stats[name]
        label = builds_meta[name].label
        L.append(
            f"{label:<10}{st['win_rate'] * 100:>6.1f}%"
            f"{_s(st['kill_mean']) + 's':>9}{_s(st['kill_median']):>7}{_s(st['kill_p90']):>7}"
            f"{st['defeats']:>5}{st['timeouts']:>5}{st['aborted']:>5}"
            f"{_s(st['starved_time_mean']) + 's':>11}{_s(st['starved_events_mean']):>8}"
            f"{_s(st['damage_taken_mean'], '{:.0f}'):>8}"
        )
    L.append("")
    L.append("---- 差异证据（全部由模拟事件流聚合，可用回放逐条核对）----")
    for name in batch.order:
        st = stats[name]
        label = builds_meta[name].label
        shares = sorted(st["damage_shares"].items(), key=lambda kv: -kv[1])[:3]
        share_txt = " / ".join(f"{k} {v * 100:.1f}%" for k, v in shares)
        crit = f"{st['crit_rate'] * 100:.1f}%" if st["crit_rate"] is not None else "-"
        L.append(f"[{label}] 伤害构成: {share_txt}；实测暴击率 {crit}"
                 f"（面板 {builds_meta[name].crit_chance * 100:.0f}%）；"
                 f"每点资源伤害 {_s(st['efficiency'])}；"
                 f"资源消耗速率 {_s(st['spend_rate'])}/s；平均药水 {_s(st['potions_mean'])} 瓶")
        if st["median_seed"] is not None:
            L.append(f"    中位代表样本: python -m combat_sim play --build {name} "
                     f"--seed {st['median_seed']}")
    L.append("")
    L.append("---- 同种子配对比较（双方均胜利的种子）----")
    names = batch.order
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            pc = paired_compare(batch.results[a], batch.results[b])
            if pc["n"]:
                la, lb = builds_meta[a].label, builds_meta[b].label
                L.append(f"{la} 快于 {lb}: {pc['a_faster']}/{pc['n']} 个种子")
    L.append("")
    L.extend(_conclusions(batch, stats, builds_meta))
    L.append("=" * 78)
    return "\n".join(L)


def _conclusions(batch, stats, builds_meta):
    L = ["---- 结论（附样本量与适用场景）----"]
    n = batch.runs
    ranked = [nm for nm in batch.order
              if stats[nm]["kill_mean"] is not None and stats[nm]["win_rate"] >= 0.95]
    ranked.sort(key=lambda nm: stats[nm]["kill_mean"])
    if not ranked:
        L.append(f"n={n}/构筑 下没有构筑达到 95% 胜率，无法给出速度排名；请先检查策略与数值。")
        return L
    fastest = ranked[0]
    L.append(f"样本量: 每构筑 n={n}；异常中断已单独统计且不计入任何指标均值。")
    L.append(f"击杀速度排名: "
             + " < ".join(f"{builds_meta[nm].label} {stats[nm]['kill_mean']:.1f}s" for nm in ranked))
    L.append("资源压力（有技能想放但放不出的时间）: "
             + "，".join(f"{builds_meta[nm].label} {_s(stats[nm]['starved_time_mean'])}s"
                        for nm in batch.order))
    viable = [nm for nm in batch.order if stats[nm]["win_rate"] >= 0.95]
    by_starve = min(viable, key=lambda nm: stats[nm]["starved_time_mean"] or 0, default=None)
    by_spend = min(viable, key=lambda nm: stats[nm]["spend_rate"] or 1e9, default=None)
    picks = {"输出窗口/竞速": fastest, "稳定长线": by_starve, "资源受限环境": by_spend}
    L.append("适用场景:")
    for scene, nm in picks.items():
        if nm is None:
            continue
        st = stats[nm]
        L.append(f"- {scene}: {builds_meta[nm].label}"
                 f"（击杀 {_s(st['kill_mean'])}s，胜率 {st['win_rate'] * 100:.1f}%，"
                 f"资源受限 {_s(st['starved_time_mean'])}s/场，"
                 f"资源消耗 {_s(st['spend_rate'])}/s）")
    if len(set(picks.values())) == 1:
        L.append(f"注意: {builds_meta[fastest].label} 在本场景各维度全面领先；"
                 f"若想暴露构筑差异，可提高敌方血量或缩短时限后重新验证。")
    for nm in batch.order:
        if stats[nm]["win_rate"] < 0.95:
            L.append(f"风险提示: {builds_meta[nm].label} 胜率仅 "
                     f"{stats[nm]['win_rate'] * 100:.1f}%（n={n}），"
                     f"上述速度指标对其只代表胜利子集，参与推荐需谨慎。")
    L.append(f"边界: 结论仅适用于对手「{batch.enemy_name}」、{batch.time_limit:.0f}s 时限"
             f"及当前策略文件；更换对手、时限或策略需重新批量验证。")
    return L


def playback(result, build, show_draws=False):
    L = [f"==== 单场回放 构筑={build.label}({build.name}) 种子={result.seed} ===="]
    for entry in result.trace or []:
        if entry[0] == "draw":
            if not show_draws:
                continue
            _, t, stream, idx, value = entry
            L.append(f"t+{t:7.2f}s  [rng:{stream}#{idx}] {value:.4f}")
        else:
            _, t, kind, data = entry
            L.append(f"t+{t:7.2f}s  [{kind}] {_fmt_event(kind, data)}")
    c = result.counters
    L.append("-" * 60)
    L.append(f"结局: {result.outcome.value}  用时: {result.time:.1f}s"
             + (f"  击杀时间: {result.kill_time:.1f}s" if result.kill_time else ""))
    if result.error:
        L.append(f"异常信息: {result.error}")
    L.append(f"总伤害 {c['damage_total']:.0f}  暴击 {c['crits']}/{c['hits']}"
             f"  治疗 {c['healing_done']:.0f}  承伤 {c['damage_taken']:.0f}"
             f"  资源消耗 {c['resource_spent']:.0f}"
             f"  资源受限 {c['starved_time']:.1f}s/{c['starved_events']}次")
    return "\n".join(L)


def _fmt_event(kind, data):
    if kind == "damage":
        return (f"{data['ability']} 造成 {data['amount']:.1f}"
                f"{' 暴击!' if data['crit'] else ''}（目标余 {data['enemy_hp']:.0f}）")
    if kind == "enemy_attack":
        return (f"敌方攻击 {data['amount']:.1f}"
                f"{' 暴击!' if data['crit'] else ''}（玩家余 {data['hp']:.0f}）")
    if kind == "heal":
        return f"{data['ability']} 治疗 {data['amount']:.0f}（玩家 {data['hp']:.0f}）"
    if kind == "cast_start":
        return f"开始读条 {data['ability']}（{data['cast_time']:.2f}s）"
    if kind == "cast":
        return f"施放 {data['ability']}"
    if kind == "starved":
        return f"资源不足，无法施放 {data['ability']}（当前 {data['resource']:.1f}）"
    if kind == "restore":
        return f"{data['ability']} 回复资源 {data['amount']:.0f}（当前 {data['resource']:.1f}）"
    if kind == "buff":
        return f"获得增益 {data['name']}（{data['duration']}s）"
    if kind == "dot_apply":
        return f"{data['ability']} 施加持续伤害（{data['duration']}s）"
    if kind == "enrage":
        return "敌方进入狂暴！"
    return str(data)
