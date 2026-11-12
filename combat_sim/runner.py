"""运行器：批量与单场都走同一个 Kernel，只是 trace 开关不同。"""

from dataclasses import dataclass

from .kernel import Kernel


@dataclass
class BatchResult:
    results: dict          # build_name -> [RunResult]
    order: list            # 构筑顺序
    runs: int
    base_seed: int
    enemy_name: str
    time_limit: float


def run_single(build, enemy, rules, seed, trace=False, max_steps=None):
    return Kernel(build, enemy, rules, seed, trace=trace, max_steps=max_steps).run()


def run_batch(specs, enemy, runs, base_seed, trace=False):
    """specs: [(build, rules)]。所有构筑共用同一组种子，便于同种子配对比较。"""
    results, order = {}, []
    for build, rules in specs:
        order.append(build.name)
        results[build.name] = [
            run_single(build, enemy, rules, seed=base_seed + i, trace=trace)
            for i in range(runs)
        ]
    return BatchResult(results=results, order=order, runs=runs, base_seed=base_seed,
                       enemy_name=enemy.name, time_limit=enemy.time_limit)
