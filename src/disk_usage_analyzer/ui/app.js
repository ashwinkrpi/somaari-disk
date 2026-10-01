// UI shell. Talks only to window.pywebview.api; formats sizes and dates. No OS access.

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
  error: null,
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

// ---- api ------------------------------------------------------------------

function api() {
  return window.pywebview && window.pywebview.api;
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

function renderStatus() {
  const box = document.querySelector(".status");
  let title = "";
  let detail = "";
  box.dataset.kind = state.error ? "error" : "";
  if (state.error) {
    title = state.error;
  } else if (state.scanning) {
    const p = state.progress;
    title = "Scanning...";
    detail = p
      ? `${count(p.files, "file", "files")} · ${count(p.dirs, "folder", "folders")} · ${formatSize(p.size)}`
      : "";
  } else if (state.folder) {
    const n = state.folder.node;
    title = `${n.name}: ${formatSize(n.size)}`;
    detail = `${count(n.child_count, "item", "items")} · ${formatSize(n.size_on_disk)} on disk`;
  } else if (!api()) {
    title = "Open this page from the Disk Usage app.";
  } else {
    title = "Choose a location to scan.";
  }
  $("status-title").textContent = title;
  $("status-detail").textContent = detail;
}

function render() {
  renderTop();
  renderBreadcrumb();
  renderStatus();
}

// ---- actions --------------------------------------------------------------

async function loadVolumes() {
  const res = await call("list_volumes");
  if (!res.ok) return;
  state.volumes = res.volumes;
  state.home = res.home;
}

function defaultLocation() {
  if (state.home) return state.home.id;
  const sys = state.volumes.find((v) => v.is_system);
  return (sys || state.volumes[0] || {}).id ?? null;
}

async function startScan(location) {
  if (!location) return;
  state.location = location;
  state.error = null;
  state.folder = null;
  state.progress = null;
  state.scanning = true;
  render();
  const res = await call("scan", location);
  if (!res.ok) {
    state.scanning = false;
    state.error = res.error.message;
  } else {
    state.scanId = Math.max(state.scanId, res.scan_id);
  }
  render();
}

async function openFolder(segments) {
  const res = await call("get_children", segments, "size", 100);
  if (!res.ok) {
    state.error = res.error.message;
  } else {
    state.folder = res;
    state.error = null;
  }
  render();
}

function goUp() {
  if (state.folder && !state.folder.is_root) openFolder(state.folder.node.segments.slice(0, -1));
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
    renderStatus();
  },
  scan_done(p) {
    if (!isCurrent(p)) return;
    state.scanning = false;
    if (!p.ok) {
      state.error = p.error.message;
      render();
      return;
    }
    openFolder([]).then(() => {
      if (p.volume_lost) state.error = "The drive was disconnected during the scan.";
      render();
    });
  },
  volumes_changed(p) {
    state.volumes = p.volumes;
    render();
  },
  error(p) {
    if (p.scan_id === state.scanId) state.scanning = false;
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
  $("location").addEventListener("change", (e) => startScan(e.target.value));
  $("up").addEventListener("click", goUp);
  $("rescan").addEventListener("click", rescanOrStop);
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
    if (e.altKey && e.key === "ArrowUp") {
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
  startScan(defaultLocation());
}

bindControls();
applyTheme();
render();
if (api()) start();
else window.addEventListener("pywebviewready", start, { once: true });
