// Multi-select rules for the row list, like a desktop file manager. Pure: no DOM, no api.
// names = row names in list order; selection = Set of names; anchor = where Shift ranges start.

// The rows from a to b (either order), inclusive. Unknown names give an empty range.
export function rangeOf(names, a, b) {
  const i = names.indexOf(a);
  const j = names.indexOf(b);
  if (i < 0 || j < 0) return [];
  return names.slice(Math.min(i, j), Math.max(i, j) + 1);
}

// Click (or keyboard move) on `name`. ctrl toggles one row, shift selects the range from the
// anchor, ctrl+shift adds that range. Returns { selection, anchor, focus }.
export function clickSelection(names, selection, anchor, name, { ctrl = false, shift = false } = {}) {
  if (shift && anchor !== null && names.includes(anchor)) {
    const range = rangeOf(names, anchor, name);
    return { selection: new Set(ctrl ? [...selection, ...range] : range), anchor, focus: name };
  }
  if (ctrl) {
    const next = new Set(selection);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return { selection: next, anchor: name, focus: name };
  }
  return { selection: new Set([name]), anchor: name, focus: name };
}

// Selected names that are still listed, in list order (largest first in a size-sorted list).
export function inListOrder(names, selection) {
  return names.filter((n) => selection.has(n));
}

// After a refresh: drop selected names that are no longer listed.
export function pruneSelection(names, selection) {
  return new Set(inListOrder(names, selection));
}

// The row to select after `removed` rows go away: the first row below the last removed one,
// else the nearest one above, else null.
export function nextAfterRemoval(names, removed) {
  const gone = new Set(removed);
  const last = Math.max(-1, ...removed.map((n) => names.indexOf(n)));
  const below = names.slice(last + 1).find((n) => !gone.has(n));
  if (below !== undefined) return below;
  return names.slice(0, Math.max(0, last)).reverse().find((n) => !gone.has(n)) ?? null;
}
