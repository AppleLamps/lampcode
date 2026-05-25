from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

PARALLEL_READ_TOOLS = frozenset({"read_file", "search_repo", "web_search"})


def can_parallelize_tool_round(tool_names: list[str]) -> bool:
    return bool(tool_names) and all(name in PARALLEL_READ_TOOLS for name in tool_names)


def run_parallel_tool_dispatches(
    jobs: list[tuple[int, Callable[[], str]]],
    *,
    max_workers: int,
) -> list[tuple[int, str]]:
    if len(jobs) <= 1 or max_workers <= 1:
        return [(index, fn()) for index, fn in jobs]

    results: list[tuple[int, str]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(jobs))) as pool:
        futures = [(index, pool.submit(fn)) for index, fn in jobs]
        for index, future in futures:
            results.append((index, future.result()))
    results.sort(key=lambda item: item[0])
    return results
