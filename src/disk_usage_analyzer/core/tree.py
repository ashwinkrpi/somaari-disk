"""Tree queries: paths, top-N children with roll-up, largest files."""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from operator import attrgetter

from .models import Node, RollUp


def path_segments(node: Node) -> tuple[str, ...]:
    """Segments from the scan root (excluded) down to node."""
    parts: list[str] = []
    while node.parent is not None:
        parts.append(node.name)
        node = node.parent
    return tuple(reversed(parts))


def iter_nodes(root: Node) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        if node.children:
            stack.extend(node.children)


def top_children(node: Node, n: int, by: str = "size") -> tuple[list[Node], RollUp | None]:
    """Largest n children, plus a roll-up of the rest (None if nothing is left over)."""
    children = sorted(node.children or (), key=attrgetter(by), reverse=True)
    top, rest = children[:n], children[n:]
    if not rest:
        return top, None
    return top, RollUp(len(rest), sum(c.size for c in rest), sum(c.size_on_disk for c in rest))


def largest_files(root: Node, n: int, by: str = "size") -> list[Node]:
    files = (x for x in iter_nodes(root) if not x.is_dir)
    return heapq.nlargest(n, files, key=attrgetter(by))
