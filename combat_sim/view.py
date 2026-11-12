"""结果视图：批量对比报告、单场回放、实战演练视图与结论生成。

展示表只由 summarize 的输出和逐场原始记录（BatchResult.material）拼装，
不手填任何数字；分母、异常和停止单列，没有完整样本时明确写出「不可计算」。
"""

from .stats import paired_compare, scenario_warnings, summarize


def _s(v, fmt="{:.1f}", empty="-"):
    return empty if v is None else fmt.format(v)


def _rate(v):
    return "不可计算" if v is None else f"{v * 100:.1f}%"


def batch_report(batch, builds_meta, scenarios=None, material=None, rewards=None):
    """scenarios: {build_name: Scenario}；material: build -> 逐场记录；rewards: build -> 收益。"""
    stats = {name: summarize(rs) for name, rs in batch.results.items()}
    L = []
    L.append("=" * 100)
    L.append(f"构筑演练报告  场景: {batch.scenario_label or '-'}  对手: {batch.enemy_name}"
             f"  时限: {batch.time_limit:.0f}s")
    L.append(f"每构筑样本 n={batch.runs}（种子 {batch.base_seed}..{batch.base_seed + batch.runs - 1}，"
             f"各构筑使用同一组种子，可同种子配对比较）")
    L.append(f"统计分母 = 完整结束样本（胜利+战败+超时）；模拟失败(aborted)与主动停止(stopped) 不计入分母。")
    L.append("-" * 100)
    header = (f"{'构筑':<12}{'胜率':>8}{'完整':>6}{'胜利':>6}{'战败':>6}{'超时':>6}{'失败':>6}{'停止':>6}"
              f"{'击杀均时':>10}{'中位':>8}{'P90':>8}{'σ':>7}"
              f"{'资源受限均时':>12}{'受限事件':>9}{'承伤':>8}{'收益':>8}")
    L.append(header)
    for name in batch.order:
        st = stats[name]
        label = builds_meta[name].label
        rew = rewards.get(name) if rewards else None
        rew_txt = _s(rew, "{:.0f}") if rew is not None else "-"
        L.append(
            f"{label:<12}{_rate(st['win_rate']):>8}{st['n_completed']:>6}{st['wins']:>6}"
            f"{st['defeats']:>6}{st['timeouts']:>6}{st['aborted']:>6}{st['stopped']:>6}"
            f"{_s(st['kill_mean']) + ('' if st['kill_mean'] is None else 's'):>10}"
            f"{_s(st['kill_median']):>8}{_s(st['kill_p90']):>8}{_s(st['kill_stdev']):>7}"
            f"{_s(st['starved_time_mean']) + ('' if st['starved_time_mean'] is None else 's'):>12}"
            f"{_s(st['starved_events_mean']):>9}"
            f"{_s(st['damage_taken_mean'], '{:.0f}'):>8}{rew_txt:>8}"
        )
    L.append("")
    L.append("---- 差异证据（全部由模拟事件流聚合，可用回放逐条核对）----")
    for name in batch.order:
        st = stats[name]
        label = builds_meta[name].label
        shares = sorted(st["damage_shares"].items(), key=lambda kv: -kv[1])[:3]
        share_txt = " / ".join(f"{k} {v * 100:.1f}%" for k, v in shares) or "无伤害记录"
        crit = f"{st['crit_rate'] * 100:.1f}%" if st["crit_rate"] is not None else "-"
        L.append(f"[{label}] 伤害构成: {share_txt}；实测暴击率 {crit}"
                 f"（面板 {builds_meta[name].crit_chance * 100:.0f}%）；"
                 f"每点资源伤害 {_s(st['efficiency'])}；"
                 f"资源消耗速率 {_s(st['spend_rate'])}/s；平均药水 {_s(st['potions_mean'])} 瓶")
        if st["median_seed"] is not None:
            L.append(f"    中位代表样本: python -m combat_sim play --build {name} "
                     f"--seed {st['median_seed']}"
                     f"{' --scenario ' + batch.scenario if batch.scenario else ''}")
    L.append("")
    L.append("---- 统计口径警示（不满足即不可下结论）----")
    any_warn = False
    for name in batch.order:
        errs, warns = scenario_warnings(stats[name], wins_required=max(30, batch.runs // 2))
        for e in errs:
            any_warn = True
            L.append(f"[{builds_meta[name].label}] 错误: {e}")
        for w in warns:
            any_warn = True
            L.append(f"[{builds_meta[name].label}] 警示: {w}")
    if not any_warn:
        L.append("未发现分母、异常或零方差问题。")
    L.append("")
    L.extend(_paired_section(batch, builds_meta))
    L.append("")
    L.extend(_conclusions(batch, stats, builds_meta))
    L.append("=" * 100)
    return "\n".join(L)


def _paired_section(batch, builds_meta):
    L = ["---- 同种子配对比较（仅双方都完整结束的种子）----"]
    names = batch.order
    shown = False
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            pc = paired_compare(batch.results[a], batch.results[b])
            if not pc["n"]:
                L.append(f"{builds_meta[a].label} vs {builds_meta[b].label}: 无共同完整种子，无法配对")
                shown = True
                continue
            la, lb = builds_meta[a].label, builds_meta[b].label
            faster_txt = (f"{la} 快于 {lb}: {pc['a_faster']}/{pc['n']} 个双方均胜利的种子"
                          if pc["a_faster"] else "双方均胜利种子里无人更快（或没有双方均胜利的种子）")
            L.append(f"{la} vs {lb}（完整同种子 {pc['n']}）: {faster_txt}；"
                     f"更快胜出(一方胜利一方未胜利): {pc['a_wins']}/{pc['n']}")
            shown = True
    if not shown:
        L.append("只有一个构筑，无配对比较。")
    return L


def _conclusions(batch, stats, builds_meta):
    L = ["---- 结论（附样本量与适用场景）----"]
    n = batch.runs
    L.append(f"样本量: 每构筑 n={n}；异常中断(aborted)与主动停止(stopped) 已单独统计且不计入任何指标均值。")
    ranked = [nm for nm in batch.order
              if stats[nm]["kill_mean"] is not None
              and stats[nm]["win_rate"] is not None
              and stats[nm]["win_rate"] >= 0.95]
    ranked.sort(key=lambda nm: stats[nm]["kill_mean"])
    if not ranked:
        L.append(f"n={n}/构筑 下没有构筑达到 95% 胜率或没有可比较的击杀时间，"
                 f"无法给出速度排名；请先检查策略与数值，或调整场景难度。")
        return L
    fastest = ranked[0]
    L.append("击杀速度排名: "
             + " < ".join(f"{builds_meta[nm].label} {stats[nm]['kill_mean']:.1f}s" for nm in ranked))
    L.append("资源压力（有技能想放但放不出的时间）: "
             + "，".join(f"{builds_meta[nm].label} {_s(stats[nm]['starved_time_mean'])}s"
                        for nm in batch.order))
    viable = [nm for nm in batch.order if (stats[nm]["win_rate"] or 0) >= 0.95]
    by_starve = min(viable, key=lambda nm: stats[nm]["starved_time_mean"] if stats[nm]["starved_time_mean"] is not None else 1e9, default=None)
    by_spend = min(viable, key=lambda nm: stats[nm]["spend_rate"] if stats[nm]["spend_rate"] is not None else 1e9, default=None)
    picks = {"输出窗口/竞速": fastest, "稳定长线": by_starve, "资源受限环境": by_spend}
    L.append("适用场景:")
    for scene, nm in picks.items():
        if nm is None:
            continue
        st = stats[nm]
        L.append(f"- {scene}: {builds_meta[nm].label}"
                 f"（击杀 {_s(st['kill_mean'])}s，胜率 {_rate(st['win_rate'])}，"
                 f"资源受限 {_s(st['starved_time_mean'])}s/场，"
                 f"资源消耗 {_s(st['spend_rate'])}/s）")
    if len(set(picks.values())) == 1:
        L.append(f"注意: {builds_meta[fastest].label} 在本场景各维度全面领先；"
                 f"若想暴露构筑差异，可提高敌方血量或缩短时限后重新验证（不要只改标题）。")
    for nm in batch.order:
        st = stats[nm]
        if st["win_rate"] is None:
            L.append(f"风险提示: {builds_meta[nm].label} 没有完整结束样本，任何速度/胜率结论都不成立。")
        elif st["win_rate"] < 0.95:
            L.append(f"风险提示: {builds_meta[nm].label} 胜率仅 {_rate(st['win_rate'])}（n={n}），"
                     f"上述速度指标只代表胜利子集，参与推荐需谨慎。")
        if st["kill_n"] == 1:
            L.append(f"风险提示: {builds_meta[nm].label} 只有 1 个胜利样本，无法判断稳定性。")
    L.append(f"边界: 结论仅适用于场景「{batch.scenario_label or batch.enemy_name}」、"
             f"{batch.time_limit:.0f}s 时限及当前策略文件；更换场景或策略需重新批量验证。")
    return L


def playback(result, build, show_draws=False, scenario=None, reward=None):
    L = [f"==== 单场回放 构筑={build.label}({build.name}) 种子={result.seed}"
         + (f" 场景={scenario.label}" if scenario else "") + " ===="]
    for entry in result.trace or []:
        if entry[0] == "draw":
            if not show_draws:
                continue
            _, t, stream, idx, value = entry
            L.append(f"t+{t:7.2f}s  [rng:{stream}#{idx}] {value:.4f}")
        else:
            _, t, kind, data = entry
            line = _fmt_event(kind, data)
            if line is not None:
                L.append(f"t+{t:7.2f}s  [{kind}] {line}")
    c = result.counters
    L.append("-" * 72)
    L.append(f"结局: {result.outcome.value}  用时: {result.time:.1f}s"
             + (f"  击杀时间: {result.kill_time:.1f}s" if result.kill_time else ""))
    if result.error:
        L.append(f"异常信息: {result.error}")
    if result.stop_reason:
        L.append(f"停止原因: {result.stop_reason}")
    L.append(f"总伤害 {c['damage_total']:.0f}  暴击 {c['crits']}/{c['hits']}"
             f"  治疗 {c['healing_done']:.0f}  承伤 {c['damage_taken']:.0f}"
             f"  资源消耗 {c['resource_spent']:.0f}"
             f"  资源受限 {c['starved_time']:.1f}s/{c['starved_events']}次"
             f"  剩余血量 {c.get('final_hp', 0):.0f}")
    if reward is not None:
        L.append("-" * 72)
        L.append(_fmt_reward(reward))
    return "\n".join(L)


def _fmt_reward(r):
    if r.get("total") is None:
        return "收益: 本场无可结算收益（非胜利或未完整结束）"
    return (f"收益: 合计 {r['total']:.1f} = 基础 {r['base']:.1f} + 胜利 {r['win_bonus']:.1f}"
            f" + 速度 {r['speed_bonus']:.1f} + 伤害 {r['damage_bonus']:.1f}"
            f" + 血量 {r['hp_bonus']:.1f}（剩余 {r.get('remaining_time', 0):.1f}s，"
            f"血量 {r.get('hp_pct', 0) * 100:.1f}%）")


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
    if kind == "spend":
        return f"消耗资源 {data['cost']:.0f}（{data['ability']}，当前 {data['resource']:.1f}）"
    if kind == "starved_interval":
        return (f"资源不足 {data['duration']:.1f}s，{data['events']} 次无法施放 "
                f"{data['ability']}（当前 {data['resource']:.1f}）")
    if kind == "restore":
        return f"{data['ability']} 回复资源 {data['amount']:.0f}（当前 {data['resource']:.1f}）"
    if kind == "potion_used":
        return f"使用药水 {data['ability']}"
    if kind == "buff":
        return f"获得增益 {data['name']}（{data['duration']}s）"
    if kind == "dot_apply":
        return f"{data['ability']} 施加持续伤害（{data['duration']}s）"
    if kind == "enrage":
        return "敌方进入狂暴！"
    if kind == "resource_sample" or kind == "resource_min":
        return None  # 资源最低点由统计区体现，回放不逐行打印
    if kind in ("victory", "defeat", "timeout", "aborted", "stopped"):
        return (f"结局 {kind}"
                + (f"（{data['error']}）" if data.get("error") else "")
                + (f"（{data['reason']}）" if data.get("reason") else ""))
    return str(data)
