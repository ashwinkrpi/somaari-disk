// Keyboard rules shared by the UI. Pure: no DOM, no api.

const PREV = new Set(["ArrowLeft", "ArrowUp"]);
const NEXT = new Set(["ArrowRight", "ArrowDown"]);

// Arrow keys in a group of buttons (dialog actions, side panel actions, breadcrumb).
// count buttons, focus on index (-1 = none), disabled[i] true = skip it. Left/Up go back,
// Right/Down go forward (both wrap), Home/End go to the first/last enabled one.
// Returns the index to focus, or null when the key isn't handled or nothing is enabled.
export function arrowTarget(count, index, key, disabled = []) {
  const enabled = [];
  for (let i = 0; i < count; i += 1) if (!disabled[i]) enabled.push(i);
  if (!enabled.length) return null;
  if (key === "Home") return enabled[0];
  if (key === "End") return enabled[enabled.length - 1];
  if (!PREV.has(key) && !NEXT.has(key)) return null;
  const step = NEXT.has(key) ? 1 : -1;
  for (let k = 1; k <= count; k += 1) {
    const i = (((index + step * k) % count) + count) % count;
    if (!disabled[i]) return i;
  }
  return null;
}

// F6 / Shift+F6: the next area to focus, cycling through `count` areas from `current`
// (-1 = focus is in none of them, so F6 goes to the first and Shift+F6 to the last).
export function nextArea(count, current, back = false) {
  if (count <= 0) return null;
  if (current < 0) return back ? count - 1 : 0;
  return (((current + (back ? -1 : 1)) % count) + count) % count;
}
