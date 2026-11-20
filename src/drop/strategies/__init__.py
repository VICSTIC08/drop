from redis.asyncio import Redis

from drop.strategies.atomic_update import AtomicUpdateStrategy
from drop.strategies.base import ReservationStrategy
from drop.strategies.naive import NaiveStrategy
from drop.strategies.redis_counter import RedisCounterStrategy
from drop.strategies.row_lock import RowLockStrategy

STRATEGY_NAMES = ["naive", "row_lock", "atomic_update", "redis_counter"]

# Strategies that are expected never to oversell. The naive one is the control group.
SAFE_STRATEGIES = [name for name in STRATEGY_NAMES if name != "naive"]


def build_strategies(redis: Redis) -> dict[str, ReservationStrategy]:
    strategies = [
        NaiveStrategy(),
        RowLockStrategy(),
        AtomicUpdateStrategy(),
        RedisCounterStrategy(redis),
    ]
    return {strategy.name: strategy for strategy in strategies}
