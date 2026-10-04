// UI shell. Talks only to window.pywebview.api; formats sizes and dates. No OS access.

import { clickSelection, inListOrder, nextAfterRemoval, pruneSelection } from "./selection.js";

const $ = (id) => document.getElementById(id);

const state = {
  theme: "system",
  volumes: [],
  home: null,
  location: null,
  scanning: false,
  scanId: 0,
  progress: null,
  folder: null, // last get_children result
  path: [], // segments of the open folder
  selected: null, // name of the focused (last clicked) row in the open folder
  selection: new Set(), // names of the selected rows (Ctrl/Shift+click for several)
  anchor: null, // where a Shift range starts
  usage: null, // last get_usage result
  lastScan: null, // last scan_done payload
  loadingCache: false, // showing the saved scan of this location while it loads
  disconnected: false, // the scanned drive went away; the list shows the last results
  error: null,
  flash: null, // one-off list notice, e.g. after Move to Trash
  sideError: null, // last failed action in the side panel, in plain words
  listSeq: 0, // drop replies to superseded get_children / side panel requests
  sideSeq: 0,
};

// ---- formatting (UI only) -------------------------------------------------

export function formatSize(n) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = n;
  for (const unit of units) {
    if (size < 1000 || unit === "TB") {
      return unit === "B" ? `${size} B` : `${size.toFixed(1)} ${unit}`;
    }
    size /= 1000;
  }
}

const count = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function formatPercent(share) {
  const pct = share * 100;
  if (pct > 0 && pct < 1) return "<1%";
  return `${Math.round(pct)}%`;
}

const formatDate = (epoch) => (epoch ? new Date(epoch * 1000).toLocaleString() : "Unknown");

// ---- dom helpers --------------------------------------------------------------

const ICONS = {
  ok: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.2L6.6 12l1.4-1.4 2.8 2.8 5.2-5.2 1.4 1.4z",
  filling: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 2a8 8 0 0 1 0 16z",
  low: "M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z",
  full: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm5 13.6L15.6 17 12 13.4 8.4 17 7 15.6 10.6 12 7 8.4 8.4 7 12 10.6 15.6 7 17 8.4 13.4 12z",
  unplugged: "M16 7V3h-2v4h-4V3H8v4H6v6l4 4v4h4v-4l4-4V7zM3.3 2 2 3.3l18.7 18.7 1.3-1.3z",
  folder: "M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8z",
  empty: "M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8zM4 8h16v10H4z",
  file: "M6 2h8l6 6v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 1.5V9h5.5z",
  lock: "M17 9V7a5 5 0 0 0-10 0v2H5v13h14V9zM9 7a3 3 0 0 1 6 0v2H9z",
  error: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-2h2zm0-4h-2V7h2z",
  drive: "M4 5h16l2 9v5H2v-5zm-1 11v1h18v-1zm14 0h2v1h-2z",
  scan: "M12 4V1L8 5l4 4V6a6 6 0 1 1-6 6H4a8 8 0 1 0 8-8z",
  trash: "M9 3v1H4v2h16V4h-5V3zM5 8l1 13h12l1-13zm4 2h2v9H9zm4 0h2v9h-2z",
  reveal: "M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8zm3 5 4 4-4 4v-3H8v-2h5z",
  more: "M6 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm6 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4zm6 0a2 2 0 1 0 0 4 2 2 0 0 0 0-4z",
};

const iconTemplate = document.createElement("template");

// The HTML parser puts <svg> in the SVG namespace; ICONS are constants, never user data.
function icon(name) {
  iconTemplate.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name]}"/></svg>`;
  return iconTemplate.content.firstElementChild;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

// Usage levels come from the api (thresholds live in Python); never color alone.
const LEVELS = {
  ok: { label: "Plenty of space", icon: "ok" },
  filling: { label: "Filling up", icon: "filling" },
  low: { label: "Running low", icon: "low" },
  full: { label: "Disk full", icon: "full" },
};

const PROBLEM_TAGS = { denied: "No access", unreadable: "Unreadable", other_drive: "Other drive" };

// ---- api ------------------------------------------------------------------

function api() {
  return window.pywebview && window.pywebview.api;
}

// pywebview creates `pywebview.api` as an empty object first and adds the methods just before
// "pywebviewready"; calling in between fails every call (no drives, no prefs, no scan).
function apiReady() {
  const a = api();
  return !!a && typeof a.list_volumes === "function";
}

async function call(name, ...args) {
  try {
    return await api()[name](...args);
  } catch (e) {
    return { ok: false, error: { code: "bridge", message: String(e) } };
  }
}

// ---- theme ----------------------------------------------------------------

const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

function applyTheme() {
  const dark = state.theme === "dark" || (state.theme === "system" && darkQuery.matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  $("theme").value = state.theme;
}

// ---- render ---------------------------------------------------------------

function locationOptions() {
  const opts = [];
  if (state.home) opts.push({ id: state.home.id, text: `${state.home.label} (${state.home.path})` });
  for (const v of state.volumes) {
    const flags = [v.is_removable && "removable", v.is_read_only && "read-only"].filter(Boolean);
    const extra = flags.length ? `, ${flags.join(", ")}` : "";
    opts.push({ id: v.id, text: `${v.label} (${v.mount_point}${extra})` });
  }
  return opts;
}

function renderTop() {
  const sel = $("location");
  const opts = locationOptions();
  sel.replaceChildren(
    ...opts.map((o) => {
      const el = document.createElement("option");
      el.value = o.id;
      el.textContent = o.text;
      return el;
    }),
  );
  const known = opts.some((o) => o.id === state.location);
  if (state.location && !known) {
    const el = document.createElement("option");
    el.value = state.location;
    el.textContent = "Disconnected drive";
    el.disabled = true;
    sel.append(el);
  }
  sel.value = state.location ?? "";
  sel.disabled = opts.length === 0;

  $("up").disabled = !state.folder || state.folder.is_root;
  $("rescan").disabled = !state.location || (!known && !state.scanning);
  $("rescan-label").textContent = state.scanning ? "Stop" : "Rescan";
  $("rescan").title = state.scanning ? "Stop scanning (Esc)" : "Rescan (F5)";
}

function renderBreadcrumb() {
  const ol = $("breadcrumb");
  const crumbs = state.folder ? state.folder.breadcrumb : [];
  ol.replaceChildren(
    ...crumbs.map((c, i) => {
      const li = document.createElement("li");
      const last = i === crumbs.length - 1;
      if (c.segments === null || last) {
        const span = document.createElement("span");
        span.textContent = c.name;
        if (last) span.setAttribute("aria-current", "page");
        li.append(span);
      } else {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = c.name;
        b.addEventListener("click", () => openFolder(c.segments));
        li.append(b);
      }
      return li;
    }),
  );
}

function renderUsage() {
  const box = $("usage");
  const u = state.usage;
  const gone = state.disconnected;
  box.hidden = !u && !gone;
  if (box.hidden) return;
  const info = gone ? { label: "Drive disconnected", icon: "unplugged" } : LEVELS[u.level];
  const pct = gone ? 0 : Math.max(0, Math.min(100, Math.floor(u.percent)));
  box.dataset.level = gone ? "gone" : u.level;
  $("usage-state").replaceChildren(icon(info.icon), el("span", null, info.label));
  $("usage-text").textContent = gone
    ? "Reconnect it, then choose it from Location to scan again."
    : `${formatSize(u.used)} used of ${formatSize(u.total)} · ${formatSize(u.free)} free · ${pct}%`;
  $("usage-fill").style.width = `${pct}%`;
  // The list covers only the scanned folder; say so when that is not the whole drive.
  const scope = $("usage-scope");
  scope.hidden = gone || u.whole_volume || u.scanned_size == null;
  if (!scope.hidden) {
    const name = state.home && state.location === state.home.id ? "Home folder" : "This folder";
    scope.textContent = `${name}: ${formatSize(u.scanned_size)} of the ${formatSize(u.used)} used on this drive`;
  }
  const bar = $("usage-bar");
  bar.setAttribute("aria-valuenow", String(pct));
  bar.setAttribute("aria-valuetext", gone ? info.label : `${pct}% used: ${info.label}`);
}

function progressText() {
  const p = state.progress;
  if (state.loadingCache) return "Opening the last scan...";
  return p ? `${count(p.files, "file", "files")} · ${formatSize(p.size)} so far` : "Starting...";
}

function renderListMeta() {
  const f = state.folder;
  const parts = [];
  if (f) parts.push(formatSize(f.node.size), count(f.node.child_count, "item", "items"));
  if (state.scanning) parts.push(`Scanning: ${progressText()}`);
  $("list-meta").textContent = parts.join(" · ");
}

function childByName(name) {
  return state.folder && name !== null ? state.folder.children.find((c) => c.name === name) : null;
}

function rowNames() {
  return state.folder ? state.folder.children.map((c) => c.name) : [];
}

// Selected rows of the open folder, in list order.
function selectedItems() {
  return inListOrder(rowNames(), state.selection).map(childByName);
}

function setSelection(selection, focus, anchor = focus) {
  state.selection = selection;
  state.selected = focus;
  state.anchor = anchor;
}

function makeRow(c) {
  const b = el("button", "row");
  b.type = "button";
  b.tabIndex = -1;
  b.dataset.name = c.name;
  b.setAttribute("aria-pressed", String(state.selection.has(c.name)));
  const kind = c.is_dir ? "folder" : "file";
  const flag = c.problem ? PROBLEM_TAGS[c.problem] : "";
  b.setAttribute(
    "aria-label",
    `${c.name}, ${kind}, ${formatSize(c.size)}, ${formatPercent(c.share_parent)}${flag ? `, ${flag}` : ""}`,
  );
  const name = el("span", "row-name");
  name.append(icon(kind), el("span", "row-text", c.name));
  if (flag) name.append(el("span", "row-flag", flag));
  const bar = el("span", "row-bar");
  const fill = el("span", "row-fill");
  fill.style.width = `${Math.min(100, c.share_parent * 100)}%`;
  bar.append(fill);
  b.append(name, bar, el("span", "row-size", formatSize(c.size)), el("span", "row-pct", formatPercent(c.share_parent)));
  b.addEventListener("click", (e) => select(c.name, { ctrl: e.ctrlKey || e.metaKey, shift: e.shiftKey }));
  if (c.is_dir) b.addEventListener("dblclick", () => openFolder(c.segments, null, true));
  const li = el("li");
  li.append(b);
  return li;
}

function makeRestRow(rest, parentSize) {
  const share = parentSize > 0 ? rest.size / parentSize : 0;
  const d = el("div", "row row-rest");
  const name = el("span", "row-name");
  name.append(icon("more"), el("span", "row-text", `Other (${count(rest.count, "item", "items")})`));
  const bar = el("span", "row-bar");
  const fill = el("span", "row-fill");
  fill.style.width = `${Math.min(100, share * 100)}%`;
  bar.append(fill);
  d.append(name, bar, el("span", "row-size", formatSize(rest.size)), el("span", "row-pct", formatPercent(share)));
  const li = el("li");
  li.append(d);
  return li;
}

function rowButtons() {
  return [...$("rows").querySelectorAll("button.row")];
}

// Roving tabindex: one row is in the tab order (the selected one, else the first).
function tabbableRow() {
  const btns = rowButtons();
  const target = btns.find((b) => b.dataset.name === state.selected) || btns[0];
  for (const b of btns) b.tabIndex = b === target ? 0 : -1;
  return target;
}

function renderRows(focus) {
  const f = state.folder;
  const rows = $("rows");
  const hadFocus = rows.contains(document.activeElement);
  const items = f ? f.children.map(makeRow) : [];
  if (f && f.rest) items.push(makeRestRow(f.rest, f.node.size));
  rows.replaceChildren(...items);
  rows.hidden = items.length === 0;
  const row = tabbableRow();
  if (row && (focus || hadFocus)) row.focus();
}

function noticeFor() {
  const f = state.folder;
  if (!f) return null;
  if (state.disconnected) {
    return { kind: "gone", icon: "unplugged", text: "Drive disconnected. These are the last results and may be incomplete." };
  }
  if (state.error) return { kind: "error", icon: "error", text: state.error };
  if (state.flash) return { kind: state.flash.kind, icon: state.flash.icon, text: state.flash.text };
  if (f.node.problem && f.children.length) {
    return { icon: "lock", text: "Some items in this folder couldn't be read, so its size may be too small." };
  }
  const d = state.lastScan;
  if (state.scanning || !d) return null;
  if (d.volume_lost) return { icon: "unplugged", text: "The drive was removed during the scan. Rescan for full totals." };
  if (d.cancelled) return { icon: "scan", text: "Scan stopped early. Sizes are incomplete; press Rescan for full totals." };
  if (d.cached) return { icon: "scan", text: `Showing the last scan, from ${formatDate(d.scanned_at)}. Press Rescan for current sizes.` };
  const denied = d.warnings.permission || 0;
  if (denied) {
    return { icon: "lock", text: `${count(denied, "item", "items")} couldn't be read (no permission) and aren't counted.` };
  }
  return null;
}

function renderNotice() {
  const box = $("notice");
  const n = noticeFor();
  box.hidden = !n;
  if (!n) return;
  box.dataset.kind = n.kind || "";
  box.replaceChildren(icon(n.icon), el("span", null, n.text));
}

function listState() {
  const f = state.folder;
  if (!api()) return { icon: "folder", title: "Open this page from the Somaari Disk app." };
  if (!f) {
    if (state.disconnected) {
      return { kind: "gone", icon: "unplugged", title: "Drive disconnected", detail: "Reconnect it, then choose it from Location." };
    }
    if (state.error) {
      return { kind: "error", icon: "error", title: "Couldn't scan this location", detail: state.error, retry: true };
    }
    if (state.scanning) return { icon: "scan", title: "Scanning...", detail: progressText() };
    return { icon: "folder", title: "Choose a location to scan." };
  }
  if (f.children.length) return null;
  const problem = f.node.problem;
  if (problem === "denied") {
    return {
      icon: "lock",
      title: "No permission to open this folder",
      detail: "Its contents can't be read, so they aren't counted in the totals.",
    };
  }
  if (problem === "unreadable") {
    return {
      kind: "error",
      icon: "error",
      title: "This folder couldn't be read",
      detail: "It may have been removed, or the drive may have a problem.",
      retry: true,
    };
  }
  if (problem === "other_drive") {
    return {
      icon: "drive",
      title: "This folder is another drive",
      detail: "Drives are scanned one at a time. Choose it from Location to scan it.",
    };
  }
  if (f.partial) return { icon: "scan", title: "Scanning this folder...", detail: progressText() };
  return { icon: "empty", title: "This folder is empty" };
}

function renderListState() {
  const box = $("list-state");
  const s = listState();
  box.hidden = !s;
  if (!s) return;
  box.dataset.kind = s.kind || "";
  const parts = [icon(s.icon), el("p", "state-title", s.title)];
  if (s.detail) parts.push(el("p", "state-detail", s.detail));
  if (s.retry && state.location && !state.scanning && !state.disconnected) {
    const b = el("button", "action", "Try again");
    b.type = "button";
    b.addEventListener("click", () => startScan(state.location));
    parts.push(b);
  }
  box.replaceChildren(...parts);
}

function sideHead(kind, kicker, title) {
  const head = el("div", "side-head");
  const text = el("div");
  text.append(el("span", "side-kicker", kicker), el("h2", null, title));
  head.append(icon(kind), text);
  return head;
}

function fact(dl, term, value) {
  dl.append(el("dt", null, term), el("dd", null, value));
}

function rootName() {
  const crumb = state.folder && state.folder.breadcrumb.find((c) => c.segments && !c.segments.length);
  return crumb ? crumb.name : "the scan";
}

function fileType(d) {
  const type = d.extension ? `${d.extension.toUpperCase()} file` : "File";
  return d.hidden ? `${type} (hidden)` : type;
}

function sideButton(label, iconName, cls, onClick) {
  const b = el("button", cls);
  b.type = "button";
  b.append(icon(iconName), el("span", null, label));
  b.addEventListener("click", onClick);
  return b;
}

// File details per the boards: facts, then Show in folder + Move to Trash.
function fileDetails(body, item, d) {
  const dl = el("dl", "facts");
  fact(dl, "Type", d.ok ? fileType(d) : "File");
  fact(dl, "Size", formatSize(item.size));
  fact(dl, "Of this folder", formatPercent(item.share_parent));
  if (d.ok) {
    fact(dl, `Of ${rootName()}`, formatPercent(d.share_root));
    fact(dl, "Full path", d.path);
    fact(dl, "Size on disk", formatSize(d.size_on_disk));
  }
  fact(dl, "Modified", formatDate(item.mtime));
  if (d.ok) {
    fact(dl, "Created", formatDate(d.created));
    const group = d.group && d.group !== d.owner ? ` (group ${d.group})` : "";
    fact(dl, "Owner", d.owner ? `${d.owner}${group}` : "Unknown");
    fact(dl, "Permissions", d.mode ? `${d.mode}${d.read_only ? " (read-only)" : ""}` : "Unknown");
  }
  body.append(dl);
  if (!d.ok) return;
  const actions = el("div", "side-actions");
  const trash = sideButton("Move to Trash", "trash", "action danger", () => trashItem(item));
  actions.append(sideButton("Show in folder", "reveal", "action", () => reveal(item)), trash);
  body.append(actions);
  if (d.trash_blocked) {
    // plain-language reason next to the disabled button (read-only drive, scan running)
    trash.disabled = true;
    const why = el("p", "side-note", d.trash_blocked);
    why.id = "trash-why";
    trash.setAttribute("aria-describedby", why.id);
    body.append(why);
  }
}

// Several rows selected: the total, then every name and size (largest first), then trash.
function renderMulti(side, f, items) {
  const body = el("div", "side-body");
  if (state.sideError) {
    const err = el("p", "side-error");
    err.setAttribute("role", "alert");
    err.append(icon("error"), el("span", null, state.sideError));
    body.append(err);
  }
  const total = items.reduce((sum, c) => sum + c.size, 0);
  const files = items.filter((c) => !c.is_dir);
  const dirs = items.length - files.length;
  const kinds = [files.length && count(files.length, "file", "files"), dirs && count(dirs, "folder", "folders")];
  const share = f.node.size > 0 ? total / f.node.size : 0;
  body.append(el("p", "side-meta", `${formatSize(total)} · ${formatPercent(share)} of ${f.node.name}`));
  body.append(el("p", "side-path", kinds.filter(Boolean).join(", ")));
  body.append(el("h3", null, "Selected items"));
  const ul = el("ul", "mini mini-scroll");
  ul.setAttribute("aria-label", "Selected items");
  for (const c of [...items].sort((a, b) => b.size - a.size)) {
    const li = el("li");
    const n = el("span", "n");
    n.append(icon(c.is_dir ? "folder" : "file"), el("span", null, c.name));
    li.append(n, el("span", "s", formatSize(c.size)));
    ul.append(li);
  }
  body.append(ul);
  const actions = el("div", "side-actions");
  const label = files.length ? `Move ${count(files.length, "file", "files")} to Trash` : "Move to Trash";
  const trash = sideButton(label, "trash", "action danger", () => trashItems(items));
  actions.append(trash);
  body.append(actions);
  if (!files.length || dirs) {
    // same rule as Delete on a single row: only files go to the Trash from here
    const why = el("p", "side-note", files.length ? "Folders are skipped; only the selected files are moved." : "Folders can't be moved to the Trash from here.");
    why.id = "trash-why";
    trash.setAttribute("aria-describedby", why.id);
    trash.disabled = !files.length;
    body.append(why);
  }
  side.replaceChildren(sideHead("file", "Selection", `${count(items.length, "item", "items")} selected`), body);
  side.hidden = false;
}

// Right panel: the selected row, several selected rows, or the open folder when none is selected.
async function renderSide() {
  const side = $("side");
  const f = state.folder;
  const seq = ++state.sideSeq;
  if (!f) {
    side.hidden = true;
    side.replaceChildren();
    return;
  }
  const picked = selectedItems();
  if (picked.length > 1) {
    renderMulti(side, f, picked);
    return;
  }
  const item = picked[0] || f.node;
  const wantKids = item.is_dir && !item.problem;
  const [details, kids] = await Promise.all([
    call("get_details", item.segments),
    wantKids ? call("get_children", item.segments, "size", 10) : null,
  ]);
  if (seq !== state.sideSeq) return;
  const body = el("div", "side-body");
  if (state.sideError) {
    const err = el("p", "side-error");
    err.setAttribute("role", "alert");
    err.append(icon("error"), el("span", null, state.sideError));
    body.append(err);
  }
  if (item.is_dir) {
    if (details.ok) body.append(el("p", "side-path", details.path));
    const meta = [formatSize(item.size), count(item.child_count, "item", "items")];
    if (item.segments.length) meta.push(`${formatPercent(item.share_parent)} of ${f.node.name}`);
    body.append(el("p", "side-meta", meta.join(" · ")));
    if (item.problem) {
      const note = el("p", "side-note");
      note.append(icon(item.problem === "other_drive" ? "drive" : "lock"), el("span", null, PROBLEM_TAGS[item.problem]));
      body.append(note);
    }
    if (kids && kids.ok) {
      body.append(el("h3", null, "Largest inside"));
      const ul = el("ul", "mini");
      for (const k of kids.children) {
        const li = el("li");
        li.append(el("span", "n", k.name), el("span", "s", formatSize(k.size)));
        ul.append(li);
      }
      if (kids.rest) ul.append(el("li", "more", `${count(kids.rest.count, "more item", "more items")}`));
      if (!kids.children.length) ul.append(el("li", "more", kids.partial ? "Scanning..." : "Empty folder"));
      body.append(ul);
    }
  } else {
    fileDetails(body, item, details);
  }
  const kicker = item === f.node ? "Open folder" : item.is_dir ? "Folder" : "File details";
  side.replaceChildren(sideHead(item.is_dir ? "folder" : "file", kicker, item.name), body);
  side.hidden = false;
}

function renderList(focus = false) {
  renderListMeta();
  renderNotice();
  renderRows(focus);
  renderListState();
}

function render() {
  renderTop();
  renderBreadcrumb();
  renderUsage();
  renderList();
  renderSide();
}

// ---- actions --------------------------------------------------------------

async function loadVolumes() {
  const res = await call("list_volumes");
  if (!res.ok) return;
  state.volumes = res.volumes;
  state.home = res.home;
}

function locationPresent() {
  if (!state.location) return false;
  if (state.home && state.location === state.home.id) return true;
  return state.volumes.some((v) => v.id === state.location);
}

function defaultLocation() {
  if (state.home) return state.home.id;
  const sys = state.volumes.find((v) => v.is_system);
  return (sys || state.volumes[0] || {}).id ?? null;
}

async function loadUsage() {
  if (!state.location) return;
  const res = await call("get_usage", state.location);
  if (res.ok) state.usage = res;
  else if (res.error.code !== "unknown_location") state.usage = null; // gone: keep the last one
  renderUsage();
}

// Rescanning the same location keeps the open folder; a new location starts at its root.
// cached: show the location's saved scan if it has one (startup, Location change).
async function startScan(location, cached = false) {
  if (!location) return;
  if (location !== state.location) {
    state.folder = null;
    state.path = [];
    setSelection(new Set(), null);
    state.usage = null;
  }
  state.location = location;
  state.error = null;
  state.flash = null;
  state.sideError = null;
  state.disconnected = false;
  state.lastScan = null;
  state.progress = null;
  state.scanning = true;
  state.loadingCache = cached;
  render();
  const res = await call("scan", location, cached);
  if (!res.ok) {
    state.scanning = false;
    state.loadingCache = false;
    state.error = res.error.message;
  } else {
    if (state.scanning && res.scan_id >= state.scanId) state.loadingCache = res.cached; // scan_done may come first
    state.scanId = Math.max(state.scanId, res.scan_id);
  }
  render();
  loadUsage();
}

// Show a folder. selectName picks a row; focus moves keyboard focus into the list.
async function openFolder(segments, selectName = null, focus = false) {
  const seq = ++state.listSeq;
  state.path = segments; // a scan refresh fired before the reply must load this folder too
  const res = await call("get_children", segments, "size", 100);
  if (seq !== state.listSeq) return;
  if (!res.ok) {
    const code = res.error.code;
    if (state.scanning && (code === "no_scan" || code === "not_found")) return; // not listed yet
    if (code === "not_found" && segments.length) return openFolder([], null, focus); // gone after rescan
    state.error = res.error.message;
    render();
    return;
  }
  const key = (segs) => segs.join("\u0000");
  const moved = !state.folder || key(res.node.segments) !== key(state.folder.node.segments);
  state.folder = res;
  state.path = res.node.segments;
  state.error = null;
  if (moved) {
    setSelection(new Set(selectName === null ? [] : [selectName]), selectName);
    state.flash = null;
    state.sideError = null;
  } else {
    state.selection = pruneSelection(rowNames(), state.selection); // rows can vanish on refresh
  }
  renderTop();
  renderBreadcrumb();
  renderList(focus);
  renderSide();
}

// mods: { ctrl, shift } from the click or key, like a desktop file manager.
function select(name, mods = {}) {
  const next = clickSelection(rowNames(), state.selection, state.anchor, name, mods);
  applySelection(next.selection, next.focus, next.anchor);
}

function applySelection(selection, focus, anchor) {
  const same =
    focus === state.selected && selection.size === state.selection.size && [...selection].every((n) => state.selection.has(n));
  setSelection(selection, focus, anchor);
  if (same) return;
  state.sideError = null;
  if (state.flash) {
    state.flash = null;
    renderNotice();
  }
  for (const b of rowButtons()) b.setAttribute("aria-pressed", String(selection.has(b.dataset.name)));
  tabbableRow();
  renderSide();
}

function goUp() {
  if (!state.folder || state.folder.is_root) return;
  const segs = state.folder.node.segments;
  openFolder(segs.slice(0, -1), segs[segs.length - 1], true);
}

async function reveal(item) {
  const res = await call("open_in_file_manager", item.segments);
  state.sideError = res.ok ? null : res.error.message;
  if (!res.ok) renderSide();
}

// Resolves true only if the user picks "Move to Trash". Cancel has focus first; Esc cancels.
// Several items: the count and total, then every name.
function confirmTrash(item, items = null) {
  const dlg = $("confirm");
  const list = $("confirm-list");
  const many = items && items.length > 1;
  $("confirm-title").textContent = many ? `Move ${count(items.length, "file", "files")} to Trash?` : "Move to Trash?";
  $("confirm-name").textContent = many ? count(items.length, "file", "files") : item.name;
  $("confirm-size").textContent = formatSize(many ? items.reduce((sum, c) => sum + c.size, 0) : item.size);
  list.replaceChildren(...(many ? items.map((c) => el("li", null, c.name)) : []));
  list.hidden = !many;
  const back = document.activeElement;
  dlg.returnValue = "";
  dlg.showModal();
  $("confirm-cancel").focus();
  return new Promise((resolve) => {
    const done = () => {
      if (back && back.isConnected) back.focus();
      resolve(dlg.returnValue === "trash");
    };
    dlg.addEventListener("close", done, { once: true });
  });
}

// After a move the totals update in place (no rescan); the next row gets the selection.
async function trashItem(item) {
  const d = await call("get_details", item.segments);
  if (!d.ok || d.trash_blocked) {
    state.sideError = d.ok ? d.trash_blocked : d.error.message; // read-only drive, scan running
    renderSide();
    return;
  }
  if (!(await confirmTrash(item))) return;
  const res = await call("move_to_trash", item.segments, true);
  const gone = res.ok || res.error.code === "not_found";
  if (!gone) {
    state.sideError = res.error.message;
    renderSide();
    return;
  }
  const after = nextAfterRemoval(rowNames(), [item.name]);
  setSelection(new Set(after === null ? [] : [after]), after);
  state.sideError = null;
  state.flash = res.ok
    ? { kind: "done", icon: "trash", text: `Moved "${item.name}" (${formatSize(item.size)}) to the Trash.` }
    : { kind: "error", icon: "error", text: res.error.message };
  await openFolder(state.path, null, true);
  loadUsage();
}

// Several rows: files only (folders are skipped, as with Delete), one confirmation, then one
// move_to_trash per file. Totals update in place; the row below the last one gets the selection.
async function trashItems(items) {
  const files = items.filter((c) => !c.is_dir);
  if (files.length <= 1) {
    if (files.length) await trashItem(files[0]);
    return;
  }
  const checks = await Promise.all(files.map((c) => call("get_details", c.segments)));
  const blocked = checks.find((d) => d.ok && d.trash_blocked);
  if (blocked) {
    state.sideError = blocked.trash_blocked; // read-only drive, scan running: same for all
    renderSide();
    return;
  }
  if (!(await confirmTrash(files[0], files))) return;
  let moved = 0;
  let movedSize = 0;
  const failed = [];
  for (const c of files) {
    const res = await call("move_to_trash", c.segments, true);
    if (res.ok) {
      moved += 1;
      movedSize += c.size;
    } else if (res.error.code !== "not_found") {
      failed.push(res.error.message); // gone already counts as done: it's not there any more
    }
  }
  const after = nextAfterRemoval(rowNames(), files.map((c) => c.name));
  setSelection(new Set(after === null ? [] : [after]), after);
  state.sideError = null;
  const done = `Moved ${count(moved, "file", "files")} (${formatSize(movedSize)}) to the Trash.`;
  state.flash = failed.length
    ? { kind: "error", icon: "error", text: `${done} ${count(failed.length, "file", "files")} couldn't be moved: ${failed[0]}` }
    : { kind: "done", icon: "trash", text: done };
  await openFolder(state.path, null, true);
  loadUsage();
}

// Arrow keys move the selection (Shift extends it), Ctrl+A selects all, Esc keeps only the
// focused row, Enter opens a folder, Delete trashes the selected files, Backspace goes up.
function onRowsKey(e) {
  const btns = rowButtons();
  if (!btns.length || e.altKey) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    const names = rowNames();
    applySelection(new Set(names), state.selected ?? names[0], state.anchor ?? names[0]);
    return;
  }
  if (e.ctrlKey || e.metaKey) return;
  const i = btns.indexOf(document.activeElement);
  const page = 8;
  const moves = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: btns.length - 1, PageDown: i + page, PageUp: i - page };
  if (e.key in moves) {
    e.preventDefault();
    const target = btns[Math.max(0, Math.min(btns.length - 1, moves[e.key]))];
    select(target.dataset.name, { shift: e.shiftKey });
    target.focus();
  } else if (e.key === "Escape" && state.selection.size > 1 && state.selected !== null) {
    e.preventDefault();
    e.stopPropagation(); // not also "stop the scan"
    applySelection(new Set([state.selected]), state.selected, state.selected);
  } else if (e.key === "Enter" && i >= 0) {
    e.preventDefault();
    const c = childByName(btns[i].dataset.name);
    if (c && c.is_dir) openFolder(c.segments, null, true);
    else if (c) select(c.name);
  } else if (e.key === "Delete" && state.selection.size > 1) {
    e.preventDefault();
    trashItems(selectedItems());
  } else if (e.key === "Delete" && i >= 0) {
    const c = childByName(btns[i].dataset.name);
    if (c && !c.is_dir) {
      e.preventDefault();
      select(c.name);
      trashItem(c); // asks first; blocked items (read-only drive) show why instead
    }
  }
}

// Refresh the open folder while scanning, at most about once a second.
let refreshTimer = null;
function scheduleRefresh() {
  if (refreshTimer || document.hidden) return;
  refreshTimer = setTimeout(() => {
    refreshTimer = null;
    if (state.scanning) openFolder(state.path);
  }, 800);
}

function rescanOrStop() {
  if (state.scanning) call("cancel_scan");
  else startScan(state.location);
}

// ---- events from Python -----------------------------------------------------

// Events can beat the scan() reply, so the newest scan id seen wins.
function isCurrent(p) {
  if (p.scan_id < state.scanId) return false;
  state.scanId = p.scan_id;
  return true;
}

const handlers = {
  scan_progress(p) {
    if (!isCurrent(p)) return;
    state.progress = p;
    renderListMeta();
    if (!state.folder) renderListState();
    scheduleRefresh();
  },
  scan_done(p) {
    if (!isCurrent(p)) return;
    state.scanning = false;
    state.loadingCache = false;
    state.lastScan = p;
    if (!p.ok) {
      state.error = p.error.message;
      state.folder = null;
      render();
      return;
    }
    if (p.volume_lost) state.disconnected = !locationPresent();
    openFolder(state.path, state.selected);
    loadUsage();
  },
  volumes_changed(p) {
    state.volumes = p.volumes;
    const present = locationPresent();
    if (!present) state.disconnected = Boolean(state.location);
    else if (state.disconnected) state.disconnected = false; // plugged back in
    render();
    if (present) loadUsage();
  },
  error(p) {
    if (p.scan_id === state.scanId) state.scanning = state.loadingCache = false;
    state.error = p.message;
    render();
  },
};

window.duaEvent = (event, payload) => {
  const h = handlers[event];
  if (h) h(payload);
};

// ---- startup ----------------------------------------------------------------

function bindControls() {
  $("location").addEventListener("change", (e) => startScan(e.target.value, true));
  $("up").addEventListener("click", goUp);
  $("rescan").addEventListener("click", rescanOrStop);
  $("rows").addEventListener("keydown", onRowsKey);
  $("theme").addEventListener("change", async (e) => {
    state.theme = e.target.value;
    applyTheme();
    await call("set_theme", state.theme);
  });
  darkQuery.addEventListener("change", applyTheme);
  document.addEventListener("visibilitychange", () => {
    if (api()) call("set_visible", !document.hidden);
  });
  document.addEventListener("keydown", (e) => {
    if ($("confirm").open) return; // the modal dialog handles its own keys
    const inSelect = e.target instanceof HTMLSelectElement;
    if ((e.altKey && e.key === "ArrowUp") || (e.key === "Backspace" && !inSelect)) {
      e.preventDefault();
      goUp();
    } else if (e.key === "F5" || (e.ctrlKey && e.key === "r")) {
      e.preventDefault(); // never reload the page; rescan instead
      if (!state.scanning && !$("rescan").disabled) startScan(state.location);
    } else if (e.key === "Escape" && state.scanning) {
      call("cancel_scan");
    }
  });
}

async function start() {
  const prefs = await call("get_prefs");
  if (prefs.ok) state.theme = prefs.theme;
  applyTheme();
  await loadVolumes();
  call("set_visible", !document.hidden);
  render();
  startScan(defaultLocation(), true);
}

bindControls();
applyTheme();
render();
if (apiReady()) start();
else window.addEventListener("pywebviewready", start, { once: true });
