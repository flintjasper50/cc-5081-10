"""规则内核：批量运行与单场播放共用同一套模拟逻辑。

固定 dt 推进时间，真正执行：攻击判定、冷却、读条、GCD、资源扣减/回复、
DoT 跳伤、增益、敌方攻击、狂暴与时限。结局四态：
  VICTORY  击杀目标（唯一成功）
  DEFEAT   玩家死亡（有效失败）
  TIMEOUT  超过场景时限（有效失败）
  ABORTED  异常中断：步数上限、非法策略/配置等（单独统计，绝不算成功）
"""

from dataclasses import dataclass
from enum import Enum

from .rng import StreamRng

EPS = 1e-6
BASE_GCD = 1.0


class Outcome(str, Enum):
    VICTORY = "victory"
    DEFEAT = "defeat"
    TIMEOUT = "timeout"
    ABORTED = "aborted"


@dataclass
class RunResult:
    build: str
    seed: int
    outcome: Outcome
    time: float
    kill_time: float | None
    counters: dict
    trace: list | None
    error: str | None = None


class Kernel:
    def __init__(self, build, enemy, rules, seed, dt=0.1, trace=False, max_steps=None):
        self.build = build
        self.enemy = enemy
        self.rules = list(rules)
        self.dt = dt
        self.max_steps = max_steps or int(enemy.time_limit / dt) * 2 + 100

        self.t = 0.0
        self.steps = 0
        self.trace_log = [] if trace else None
        self.rng = StreamRng(
            seed,
            sink=(lambda e: self.trace_log.append(("draw",) + e)) if trace else None,
            clock=lambda: self.t,
        )

        self.hp = build.max_hp
        self.resource = build.max_resource
        self.cooldowns = {a.name: 0.0 for a in build.abilities}
        self.charges = {a.name: a.charges for a in build.abilities if a.charges is not None}
        self.gcd = 0.0
        self.casting = None  # [ability, remaining]
        self.buffs = []      # {"name","mult","expires"}
        self.dots = []       # {"name","damage","interval","next_tick","expires"}
        self.enemy_hp = enemy.max_hp
        self.enemy_next_attack = enemy.attack_interval
        self.enraged = False

        self.counters = {
            "damage_total": 0.0, "damage_by_ability": {}, "casts_by_ability": {},
            "hits": 0, "crits": 0, "healing_done": 0.0, "damage_taken": 0.0,
            "resource_spent": 0.0, "starved_events": 0, "starved_time": 0.0,
            "potions_used": 0, "min_resource": build.max_resource,
        }
        self._last_starved = (None, -10.0)

    # ---------- 主循环 ----------

    def run(self):
        try:
            while True:
                if self.steps >= self.max_steps:
                    return self._finish(Outcome.ABORTED, f"超过步数上限 {self.max_steps}，模拟未能终结")
                self._tick()
                if self.enemy_hp <= 0:
                    return self._finish(Outcome.VICTORY)
                if self.hp <= 0:
                    return self._finish(Outcome.DEFEAT)
                if self.t >= self.enemy.time_limit:
                    return self._finish(Outcome.TIMEOUT)
        except Exception as exc:
            return self._finish(Outcome.ABORTED, f"{type(exc).__name__}: {exc}")

    def _finish(self, outcome, error=None):
        kill_time = self.t if outcome is Outcome.VICTORY else None
        self._emit(outcome.value, error=error)
        return RunResult(
            build=self.build.name, seed=self.rng.seed, outcome=outcome,
            time=self.t, kill_time=kill_time, counters=self.counters,
            trace=self.trace_log, error=error,
        )

    def _tick(self):
        t, dt = self.t, self.dt
        self.resource = min(self.build.max_resource,
                            self.resource + self.build.resource_regen * dt)
        self.counters["min_resource"] = min(self.counters["min_resource"], self.resource)

        if self.buffs:
            self.buffs = [b for b in self.buffs if b["expires"] - t > EPS]
        for dot in list(self.dots):
            while dot["next_tick"] <= t + EPS and dot["next_tick"] <= dot["expires"] + EPS:
                self._player_damage(dot["name"], dot["damage"], can_crit=False)
                dot["next_tick"] += dot["interval"]
            if dot["expires"] - t <= EPS:
                self.dots.remove(dot)

        if (not self.enraged and self.enemy.enrage_at is not None
                and t + EPS >= self.enemy.enrage_at):
            self.enraged = True
            self._emit("enrage")

        if t + EPS >= self.enemy_next_attack:
            self._enemy_attack()
            interval = self.enemy.attack_interval
            if self.enraged:
                interval *= self.enemy.enrage_interval_mult
            self.enemy_next_attack += interval

        if self.casting is not None:
            self.casting[1] -= dt
            if self.casting[1] <= EPS:
                ab = self.casting[0]
                self.casting = None
                self._resolve(ab)
        elif self.gcd <= EPS:
            self._decide()

        for name in self.cooldowns:
            if self.cooldowns[name] > 0.0:
                self.cooldowns[name] = max(0.0, self.cooldowns[name] - dt)
        if self.gcd > 0.0:
            self.gcd = max(0.0, self.gcd - dt)
        self.t = t + dt
        self.steps += 1

    # ---------- 决策（条件触发动作） ----------

    def _decide(self):
        blocked = False
        for rule in self.rules:
            if not self._match(rule.cond):
                continue
            ab = self.build.ability(rule.action)
            if self.cooldowns[ab.name] > EPS:
                continue
            if ab.charges is not None and self.charges.get(ab.name, 0) <= 0:
                continue
            if self.resource + EPS < ab.cost:
                blocked = True
                self.counters["starved_events"] += 1
                last_name, last_t = self._last_starved
                if ab.name != last_name or self.t - last_t > 1.0:
                    self._emit("starved", ability=ab.name, resource=self.resource)
                    self._last_starved = (ab.name, self.t)
                continue
            self._start(ab)
            break
        if blocked:
            self.counters["starved_time"] += self.dt

    def _match(self, cond):
        for key, v in cond.items():
            if key == "always":
                ok = bool(v)
            elif key == "self_hp_pct_lte":
                ok = self.hp <= self.build.max_hp * v / 100.0
            elif key == "self_hp_pct_gte":
                ok = self.hp >= self.build.max_hp * v / 100.0
            elif key == "enemy_hp_pct_lte":
                ok = self.enemy_hp <= self.enemy.max_hp * v / 100.0
            elif key == "enemy_hp_pct_gte":
                ok = self.enemy_hp >= self.enemy.max_hp * v / 100.0
            elif key == "resource_gte":
                ok = self.resource >= v
            elif key == "resource_lte":
                ok = self.resource <= v
            elif key == "cooldown_ready":
                if v not in self.cooldowns:
                    raise ValueError(f"cooldown_ready 引用了未知技能: {v}")
                ok = self.cooldowns[v] <= EPS
            elif key == "debuff_remaining_lte":
                name, th = v
                rem = next((d["expires"] - self.t for d in self.dots if d["name"] == name), 0.0)
                ok = rem <= th
            elif key == "buff_active":
                ok = any(b["name"] == v for b in self.buffs)
            elif key == "charges_gte":
                name, n = v
                ok = self.charges.get(name, 0) >= n
            elif key == "time_gte":
                ok = self.t >= v
            else:
                raise ValueError(f"未知条件: {key}")
            if not ok:
                return False
        return True

    # ---------- 动作执行 ----------

    def _start(self, ab):
        self.resource -= ab.cost
        self.counters["resource_spent"] += ab.cost
        if ab.charges is not None:
            self.charges[ab.name] -= 1
        if ab.type == "restore":
            self.counters["potions_used"] += 1
        self.cooldowns[ab.name] = ab.cooldown
        self.counters["casts_by_ability"][ab.name] = \
            self.counters["casts_by_ability"].get(ab.name, 0) + 1
        cast = ab.cast_time / (1.0 + self.build.haste)
        if cast > self.dt / 2:
            self._emit("cast_start", ability=ab.name, cast_time=cast)
            self.casting = [ab, cast]
        else:
            self._resolve(ab)
            self.gcd = BASE_GCD / (1.0 + self.build.haste)

    def _resolve(self, ab):
        self._emit("cast", ability=ab.name)
        if ab.type == "damage":
            if ab.damage:
                self._player_damage(ab.name, ab.damage, can_crit=True)
            if ab.dot:
                d = ab.dot
                self.dots.append({
                    "name": ab.name, "damage": d["damage"], "interval": d["interval"],
                    "next_tick": self.t + d["interval"], "expires": self.t + d["duration"],
                })
                self._emit("dot_apply", ability=ab.name, duration=d["duration"])
        elif ab.type == "heal":
            before = self.hp
            self.hp = min(self.build.max_hp, self.hp + ab.heal)
            self.counters["healing_done"] += self.hp - before
            self._emit("heal", ability=ab.name, amount=self.hp - before, hp=self.hp)
        elif ab.type == "buff":
            b = ab.buff
            self.buffs.append({"name": b["name"], "mult": b["damage_mult"],
                               "expires": self.t + b["duration"]})
            self._emit("buff", name=b["name"], duration=b["duration"])
        elif ab.type == "restore":
            before = self.resource
            self.resource = min(self.build.max_resource, self.resource + ab.restore)
            self._emit("restore", ability=ab.name, amount=self.resource - before,
                       resource=self.resource)
        else:
            raise ValueError(f"未知技能类型: {ab.type}")

    def _player_damage(self, tag, base, can_crit):
        var = 0.9 + 0.2 * self.rng.uniform("player_damage")
        crit = False
        mult = 1.0
        if can_crit:
            crit = self.rng.uniform("player_crit") < self.build.crit_chance
            if crit:
                mult = self.build.crit_mult
        buff_mult = 1.0
        for b in self.buffs:
            buff_mult *= b["mult"]
        amount = base * var * mult * buff_mult
        self.enemy_hp -= amount
        c = self.counters
        c["damage_total"] += amount
        c["damage_by_ability"][tag] = c["damage_by_ability"].get(tag, 0.0) + amount
        if can_crit:
            c["hits"] += 1
            if crit:
                c["crits"] += 1
        self._emit("damage", ability=tag, amount=amount, crit=crit, enemy_hp=self.enemy_hp)

    def _enemy_attack(self):
        e = self.enemy
        u = self.rng.uniform("enemy_damage")
        amount = e.damage * (1.0 - e.damage_variance + 2.0 * e.damage_variance * u)
        if self.enraged:
            amount *= e.enrage_damage_mult
        crit = self.rng.uniform("enemy_crit") < e.crit_chance
        if crit:
            amount *= e.crit_mult
        self.hp -= amount
        self.counters["damage_taken"] += amount
        self._emit("enemy_attack", amount=amount, crit=crit, hp=self.hp)

    def _emit(self, kind, **data):
        if self.trace_log is not None:
            self.trace_log.append(("event", self.t, kind, data))
