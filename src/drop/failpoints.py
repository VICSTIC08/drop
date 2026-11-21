"""Named pause points inside the code that tests can hook into.

In normal running no hook is set and `hit` does nothing. A test can attach a hook
to force two requests to interleave in an exact order, which turns a race that
happens "sometimes" into one that happens every time.
"""

from collections.abc import Awaitable, Callable

Hook = Callable[[], Awaitable[None]]

_hooks: dict[str, Hook] = {}


def set_hook(name: str, hook: Hook) -> None:
    _hooks[name] = hook


def clear() -> None:
    _hooks.clear()


async def hit(name: str) -> None:
    hook = _hooks.get(name)
    if hook is not None:
        await hook()


class Crash(BaseException):
    """Raised by a test hook to imitate the process dying at this exact line.

    Code under test must let it pass straight through without cleaning up,
    because a process that was killed does not get to clean up either.
    """
