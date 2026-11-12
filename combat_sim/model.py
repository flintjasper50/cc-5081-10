"""数据模型与 JSON 加载。

构筑(builds)、策略(strategies)、场景(scenarios)都是可编辑的 JSON，改数值/规则
不需要动代码。场景固定对手、样本量与约束，是批量演练与单场播放的唯一入口参数。

急速只缩短读条与 GCD，不缩短冷却；DoT 跳伤害不暴击；治疗量固定不掷随机。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class Ability:
    name: str
    type: str  # damage | heal | buff | restore
    cost: float = 0.0
    cooldown: float = 0.0
    cast_time: float = 0.0
    damage: float = 0.0
    heal: float = 0.0
    restore: float = 0.0
    charges: int | None = None
    dot: dict | None = None   # {"damage","interval","duration"}
    buff: dict | None = None  # {"name","damage_mult","duration"}


@dataclass(frozen=True)
class Build:
    name: str
    label: str
    max_hp: float
    max_resource: float
    resource_regen: float
    crit_chance: float
    crit_mult: float
    haste: float
    abilities: tuple

    def ability(self, name):
        for a in self.abilities:
            if a.name == name:
                return a
        raise KeyError(f"构筑 {self.name} 中不存在技能 {name}")


@dataclass(frozen=True)
class Enemy:
    name: str
    max_hp: float
    attack_interval: float
    damage: float
    time_limit: float
    damage_variance: float = 0.1
    crit_chance: float = 0.0
    crit_mult: float = 1.5
    enrage_at: float | None = None
    enrage_damage_mult: float = 2.0
    enrage_interval_mult: float = 0.5


@dataclass(frozen=True)
class Rule:
    name: str
    cond: dict
    action: str


REQUIREMENT_DEFAULTS = {
    "min_completed": 1,
    "min_win_rate": 0.0,
    "max_timeouts": None,   # None = 不限制
    "max_aborted": 0,
    "max_stopped": 0,
}

REWARD_DEFAULTS = {
    "win_base": 100.0,
    "per_remaining_second": 0.5,
    "per_damage_hundred": 0.1,
    "hp_remaining_pct": 0.5,
}


@dataclass(frozen=True)
class Scenario:
    name: str
    label: str
    purpose: str
    enemy: Enemy
    runs: int
    seed: int
    builds: tuple
    requirements: dict = field(default_factory=dict)
    rewards: dict = field(default_factory=dict)
    kind: str = "batch"          # batch | battle_practice
    notes: str = ""

    def requirement(self, key):
        return self.requirements.get(key, REQUIREMENT_DEFAULTS[key])

    def reward(self, key):
        return self.rewards.get(key, REWARD_DEFAULTS[key])


def load_enemy(path=None):
    p = Path(path) if path else DATA_DIR / "enemy.json"
    return Enemy(**json.loads(p.read_text(encoding="utf-8")))


def enemy_from_dict(d):
    return Enemy(**d)


def load_build(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    abilities = tuple(Ability(**a) for a in d.pop("abilities"))
    return Build(abilities=abilities, **d)


def load_strategy(path):
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Rule(name=r.get("name", f"rule{i}"), cond=r["if"], action=r["do"])
            for i, r in enumerate(rules)]


def load_scenario(path):
    p = Path(path)
    d = json.loads(p.read_text(encoding="utf-8"))
    unknown = set(d) - {"name", "label", "purpose", "enemy", "runs", "seed",
                        "builds", "requirements", "rewards", "kind", "notes"}
    if unknown:
        raise ValueError(f"场景 {p} 含未知字段: {sorted(unknown)}")
    if "name" not in d or "enemy" not in d:
        raise ValueError(f"场景 {p} 缺少必填字段 name/enemy")
    requirements = dict(REQUIREMENT_DEFAULTS)
    requirements.update(d.get("requirements", {}))
    unknown_req = set(requirements) - set(REQUIREMENT_DEFAULTS)
    if unknown_req:
        raise ValueError(f"场景 {p} 含未知约束: {sorted(unknown_req)}")
    rewards = dict(REWARD_DEFAULTS)
    rewards.update(d.get("rewards", {}))
    unknown_rw = set(rewards) - set(REWARD_DEFAULTS)
    if unknown_rw:
        raise ValueError(f"场景 {p} 含未知收益参数: {sorted(unknown_rw)}")
    return Scenario(
        name=d["name"], label=d.get("label", d["name"]),
        purpose=d.get("purpose", ""), enemy=enemy_from_dict(d["enemy"]),
        runs=int(d.get("runs", 60)), seed=int(d.get("seed", 1)),
        builds=tuple(d.get("builds", ())), requirements=requirements,
        rewards=rewards, kind=d.get("kind", "batch"), notes=d.get("notes", ""),
    )


def load_scenarios(data_dir=None):
    root = Path(data_dir) if data_dir else DATA_DIR
    sdir = root / "scenarios"
    if not sdir.is_dir():
        raise FileNotFoundError(f"在 {root} 下没有 scenarios/ 目录")
    out = {}
    for p in sorted(sdir.glob("*.json")):
        sc = load_scenario(p)
        if sc.name in out:
            raise ValueError(f"场景名重复: {sc.name}")
        out[sc.name] = sc
    if not out:
        raise FileNotFoundError(f"{sdir} 下没有场景文件")
    return out


def load_scenario_by_name(name, data_dir=None):
    if name.endswith(".json") and Path(name).is_file():
        return load_scenario(name)
    scenarios = load_scenarios(data_dir)
    if name not in scenarios:
        raise KeyError(f"未知场景 {name}，可选: {', '.join(sorted(scenarios))}")
    return scenarios[name]


def load_all(data_dir=None, build_names=None, enemy=None):
    """加载对手与全部（或指定）构筑及其同名策略文件。enemy 可被场景覆盖。"""
    root = Path(data_dir) if data_dir else DATA_DIR
    if enemy is None:
        enemy = load_enemy(root / "enemy.json")
    specs = []
    for bp in sorted((root / "builds").glob("*.json")):
        build = load_build(bp)
        if build_names and build.name not in build_names:
            continue
        sp = root / "strategies" / f"{build.name}.json"
        if not sp.is_file():
            raise FileNotFoundError(f"构筑 {build.name} 缺少同名策略 {sp}")
        specs.append((build, load_strategy(sp)))
    if not specs:
        want = "、".join(build_names) if build_names else "任意构筑"
        raise FileNotFoundError(f"在 {root} 下没有找到{(' ' + want) if build_names else ''}的构筑")
    if build_names:
        missing = set(build_names) - {b.name for b, _ in specs}
        if missing:
            raise FileNotFoundError(f"未找到场景要求的构筑: {', '.join(sorted(missing))}")
    return enemy, specs
