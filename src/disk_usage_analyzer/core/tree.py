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


SORT_KEYS = ("size", "size_on_disk", "name", "mtime")


def sorted_children(
    node: Node, n: int, sort: str = "size", by: str = "size"
) -> tuple[list[Node], RollUp | None]:
    """Largest n children by `by`, shown in `sort` order (name ascending, others descending)."""
    if sort not in SORT_KEYS:
        raise ValueError(f"unknown sort: {sort}")
    top, rest = top_children(node, n, by)
    if sort == "name":
        top.sort(key=lambda c: (c.name.casefold(), c.name))
    elif sort != by:
        top.sort(key=attrgetter(sort), reverse=True)
    return top, rest


def display_name(name: str) -> str:
    """Name safe to print or encode as UTF-8. Bytes that are not valid UTF-8 (kept by the OS
    as lone surrogates) become U+FFFD; valid names come back unchanged."""
    try:
        name.encode("utf-8")
        return name
    except UnicodeEncodeError:
        return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _child(node: Node, name: str) -> Node | None:
    children = node.children or ()
    for c in children:
        if c.name == name:
            return c
    # A non-UTF-8 name may come back from the UI with U+FFFD in place of its raw bytes:
    # accept that only when exactly one child matches.
    shown = display_name(name)
    matches = [c for c in children if c.name != shown and display_name(c.name) == shown]
    return matches[0] if len(matches) == 1 else None


def find_node(root: Node, segments: tuple[str, ...] | list[str]) -> Node | None:
    """Node at segments below root, or None if it is not in the tree."""
    node: Node | None = root
    for name in segments:
        node = _child(node, name) if node is not None else None
    return node


def remove_node(node: Node) -> None:
    """Detach node and subtract its sizes from every ancestor (after Move to Trash)."""
    parent = node.parent
    if parent is None:
        raise ValueError("cannot remove the scan root")
    assert parent.children is not None
    parent.children.remove(node)
    node.parent = None
    while parent is not None:
        parent.size -= node.size
        parent.size_on_disk -= node.size_on_disk
        parent = parent.parent


def largest_files(root: Node, n: int, by: str = "size") -> list[Node]:
    files = (x for x in iter_nodes(root) if not x.is_dir)
    return heapq.nlargest(n, files, key=attrgetter(by))
