"""Which of the Docker host's cores a pod's Android is pinned to (``--cpuset-cpus``).

The host's cores are cut into whole slices of ``EMULATOR__CPUSET_CORES``; pod N takes
slice N, and pods past the last whole slice start again at the first. Cores left
over after the last whole slice are not handed out; a host with no more cores than
one slice gives every pod all of them.
"""

from __future__ import annotations


def cpuset_for(index: int, size: int, host_cpus: int) -> str:
    """The cpuset list (``"8-15"``, ``"0"``) of pod ``index``, ``size`` cores of ``host_cpus``."""
    if host_cpus <= size:
        first, count = 0, host_cpus
    else:
        first, count = (index % (host_cpus // size)) * size, size
    last = first + count - 1
    return str(first) if first == last else f"{first}-{last}"
