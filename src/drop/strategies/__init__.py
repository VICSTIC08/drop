from drop.strategies.base import ReservationStrategy
from drop.strategies.naive import NaiveStrategy

STRATEGIES: dict[str, ReservationStrategy] = {
    NaiveStrategy.name: NaiveStrategy(),
}
