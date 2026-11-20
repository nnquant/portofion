"""Nested portfolio evaluation over a DAG of portfolio nodes and leaf series."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

from portofion_engine.portfolio.simulate import PortfolioResult, simulate
from portofion_engine.portfolio.spec import PortfolioSpec

MAX_DEPTH = 8


class CycleError(ValueError):
    def __init__(self, path: list[str]):
        self.path = path
        super().__init__("portfolio cycle: " + " -> ".join(path))


class DepthError(ValueError):
    pass


@dataclass
class Member:
    key: str  # member key inside the parent (usually the ref string)
    node: str  # "leaf:<id>" or "pf:<id>"


@dataclass
class PortfolioNode:
    id: str
    spec: PortfolioSpec
    members: list[Member]
    weights: dict[str, float] = field(default_factory=dict)
    reported: pd.DataFrame | None = None


def find_cycle(edges: Mapping[str, list[str]], root: str) -> list[str] | None:
    """DFS from root; returns the cycle path if one is reachable."""
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        stack.append(n)
        for c in edges.get(n, []):
            if state.get(c) == 1:
                return [*stack[stack.index(c):], c]
            if state.get(c) is None:
                found = visit(c)
                if found:
                    return found
        stack.pop()
        state[n] = 2
        return None

    return visit(root)


def evaluate(
    root: str,
    nodes: Mapping[str, PortfolioNode],
    leaves: Mapping[str, tuple[pd.Series, float]],
    *,
    max_depth: int = MAX_DEPTH,
) -> dict[str, PortfolioResult]:
    """Evaluate ``root`` and every portfolio below it (post-order, memoized)."""
    edges = {
        f"pf:{nid}": [m.node for m in n.members if m.node.startswith("pf:")]
        for nid, n in nodes.items()
    }
    cyc = find_cycle(edges, f"pf:{root}")
    if cyc:
        raise CycleError(cyc)
    memo: dict[str, PortfolioResult] = {}

    def run(nid: str, depth: int) -> PortfolioResult:
        if depth > max_depth:
            raise DepthError(f"portfolio nesting deeper than {max_depth}")
        if nid in memo:
            return memo[nid]
        node = nodes[nid]
        series: dict[str, pd.Series] = {}
        periods: dict[str, float] = {}
        for m in node.members:
            if m.node.startswith("pf:"):
                child = run(m.node[3:], depth + 1)
                series[m.key] = child.returns
                periods[m.key] = child.periods_per_year
            else:
                s, p = leaves.get(m.node[5:], (pd.Series(dtype="float64"), 252.0))
                series[m.key] = s
                periods[m.key] = p
        res = simulate(series, node.spec, weights=node.weights, reported=node.reported, periods=periods)
        memo[nid] = res
        return res

    run(root, 0)
    return memo
