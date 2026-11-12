"""按名称独立的随机流。

每个流用 (seed, 流名) 单独播种，某条流的抽样序列只取决于它自己被抽取的次数，
与其他事件（其他流）无关。因此切换构筑不会改变无关事件的抽样顺序：
例如敌方暴击流 "enemy_crit" 的第 i 个值，在任何玩家构筑下都相同。
"""

import random
from collections import defaultdict


class StreamRng:
    def __init__(self, seed, sink=None, clock=None):
        self.seed = seed
        self._streams = {}
        self._counts = defaultdict(int)
        # sink: 每次抽样的回调 (t, stream, index, value)，用于回放追踪
        self._sink = sink
        self._clock = clock or (lambda: 0.0)

    def uniform(self, stream):
        rng = self._streams.get(stream)
        if rng is None:
            rng = random.Random(f"{self.seed}:{stream}")
            self._streams[stream] = rng
        idx = self._counts[stream]
        self._counts[stream] = idx + 1
        value = rng.random()
        if self._sink is not None:
            self._sink((self._clock(), stream, idx, value))
        return value
