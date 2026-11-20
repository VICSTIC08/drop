from drop.strategies.atomic_update import AtomicUpdateStrategy
from drop.strategies.base import ReservationStrategy
from drop.strategies.naive import NaiveStrategy
from drop.strategies.row_lock import RowLockStrategy

STRATEGIES: dict[str, ReservationStrategy] = {
    strategy.name: strategy
    for strategy in (NaiveStrategy(), RowLockStrategy(), AtomicUpdateStrategy())
}

# Strategies that are expected never to oversell. The naive one is the control group.
SAFE_STRATEGIES = [name for name in STRATEGIES if name != "naive"]
