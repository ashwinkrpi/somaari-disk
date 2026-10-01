"""Saved scans: a finished ScanResult as JSON Lines, streamed both ways.

Line 1 is a header object; each later line is one node in depth-first order:
[depth, name, is_dir, size, size_on_disk, mtime]. Streaming keeps memory flat for
million-node trees (no second copy of the tree as nested lists). The caller owns the
file (and gzip); this module only reads and writes text streams.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import IO

from .models import Node, ScanResult, ScanWarning

FORMAT = 1
_COUNTS = ("files", "dirs", "links_skipped", "hardlinks_deduped")


class CacheError(ValueError):
    """The saved scan is unreadable, truncated or from another format version."""


def dump(result: ScanResult, fp: IO[str], scanned_at: int) -> None:
    """Write result to a text stream. ensure_ascii keeps non-UTF-8 names (surrogates) safe."""
    header = {
        "format": FORMAT,
        "root_path": list(result.root_path.parts),
        "scanned_at": scanned_at,
        "elapsed": result.elapsed,
        "warnings": [[w.kind, list(w.path), w.message] for w in result.warnings],
        **{k: getattr(result, k) for k in _COUNTS},
    }
    fp.write(json.dumps(header, separators=(",", ":")) + "\n")
    stack: list[tuple[Node, int]] = [(result.root, 0)]
    while stack:
        node, depth = stack.pop()
        row = [depth, node.name, int(node.is_dir), node.size, node.size_on_disk, node.mtime]
        fp.write(json.dumps(row, separators=(",", ":")) + "\n")
        if node.children:
            stack.extend((c, depth + 1) for c in reversed(node.children))
    fp.write('"end"\n')  # a file without this line was cut short (crash, full disk)


def load(fp: Iterable[str]) -> tuple[ScanResult, int]:
    """Read a stream written by dump(). Returns (result, scanned_at). Raises CacheError."""
    lines = iter(fp)
    try:
        header = json.loads(next(lines))
        if not isinstance(header, dict) or header.get("format") != FORMAT:
            raise CacheError("unknown format")
        parents: list[Node] = []  # parents[d] = latest dir at depth d
        names: dict[str, str] = {}  # share repeated names, as the scanner does
        root: Node | None = None
        complete = False
        for line in lines:
            row = json.loads(line)
            if row == "end":
                complete = True
                break
            depth, name, is_dir, size, on_disk, mtime = row
            if (depth == 0) != (root is None) or depth > len(parents):
                raise CacheError("bad tree order")
            parent = parents[depth - 1] if depth else None
            node = Node(names.setdefault(name, name), bool(is_dir), size, on_disk, mtime, parent)
            if parent is None:
                root = node
            else:
                assert parent.children is not None
                parent.children.append(node)
            if is_dir:
                del parents[depth:]
                parents.append(node)
        if root is None or not complete:
            raise CacheError("truncated")
        result = ScanResult(
            root,
            Path(*header["root_path"]),
            [ScanWarning(k, tuple(p), m) for k, p, m in header["warnings"]],
            elapsed=float(header["elapsed"]),
            **{k: int(header[k]) for k in _COUNTS},
        )
        return result, int(header["scanned_at"])
    except CacheError:
        raise
    except (StopIteration, ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
        raise CacheError(str(e) or type(e).__name__) from None
