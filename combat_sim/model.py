"""数据模型与 JSON 加载。

构筑(builds)与策略(strategies)都是可编辑的 JSON 文件，改数值/规则不需要动代码。
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


def load_enemy(path=None):
    p = Path(path) if path else DATA_DIR / "enemy.json"
    return Enemy(**json.loads(p.read_text(encoding="utf-8")))


def load_build(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    abilities = tuple(Ability(**a) for a in d.pop("abilities"))
    return Build(abilities=abilities, **d)


def load_strategy(path):
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Rule(name=r.get("name", f"rule{i}"), cond=r["if"], action=r["do"])
            for i, r in enumerate(rules)]


def load_all(data_dir=None, build_names=None):
    """加载敌人与全部（或指定）构筑及其同名策略文件。"""
    root = Path(data_dir) if data_dir else DATA_DIR
    enemy = load_enemy(root / "enemy.json")
    specs = []
    for bp in sorted((root / "builds").glob("*.json")):
        build = load_build(bp)
        if build_names and build.name not in build_names:
            continue
        sp = root / "strategies" / f"{build.name}.json"
        specs.append((build, load_strategy(sp)))
    if not specs:
        raise FileNotFoundError(f"在 {root} 下没有找到构筑")
    return enemy, specs
