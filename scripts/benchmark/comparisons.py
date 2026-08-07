from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from .common import BenchmarkError


PREFERENCE_ORDINAL = {
    "left_clear": 2,
    "left_slight": 1,
    "tie": 0,
    "right_slight": -1,
    "right_clear": -2,
}


def reject_comparison_cycle(
    labels: Sequence[str], comparisons: Mapping[str, str]
) -> None:
    """Reject pairwise answers that cannot represent one consistent ordering."""
    parent = {label: label for label in labels}

    def find(label: str) -> str:
        while parent[label] != label:
            parent[label] = parent[parent[label]]
            label = parent[label]
        return label

    for key, value in comparisons.items():
        if value == "tie":
            left, right = key.split(":")
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[right_root] = left_root

    edges: dict[str, set[str]] = defaultdict(set)
    for key, value in comparisons.items():
        ordinal = PREFERENCE_ORDINAL[value]
        if not ordinal:
            continue
        left, right = key.split(":")
        better, worse = (left, right) if ordinal > 0 else (right, left)
        better_root, worse_root = find(better), find(worse)
        if better_root == worse_root:
            raise BenchmarkError("comparison cycle contradicts a tie")
        edges[better_root].add(worse_root)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise BenchmarkError("contradictory three-way comparison cycle")
        if node in visited:
            return
        visiting.add(node)
        for child in edges.get(node, ()):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in labels:
        visit(find(node))
