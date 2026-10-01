# Disk Usage Analyzer: design tokens (extracted from the Claude canvas)

Boards in `boards/` are the visual source of truth (1280x840 reference size).
They are HTML mockups with static sample data. Open them in a browser to compare against the app.
Fonts: IBM Plex Sans (400/500/600) for UI, IBM Plex Mono (500) for paths and sizes. Bundle locally; no CDN at runtime.

## Layout (top to bottom)
1. Top panel (dark, 88px + 44px breadcrumb row): app title "Disk Usage", Location select (min-width 420px, 44px high, mono 15px), Up button (44x44), Rescan button (right aligned, 44px high).
2. Breadcrumb row: mono 13px, current segment white/600, ancestors muted.
3. Usage bar strip: status label + icon (left), "X used of Y · Z free · N%" (right), 10px progress bar with tick marks at 50% and 75%.
4. Body: 16px padding and gap. Left panel (flex 2) "Largest items": header 64px, rows 56px with name, inline bar (16px high), size, percent. Right panel (flex 1): detail for selection.
   - Folder selected: header, path, "size · N items · % of parent", child list sorted by size.
   - File selected: "File details": name, type, size, % of folder and % of root, Full path, Size on disk, Modified, Created, Owner, Permissions, buttons "Show in folder" and "Move to Trash" (destructive color).
5. Last row in the list is always "Other (N items)" or "N more items" to roll up the long tail.

## Light palette
page bg #EEF0F3 | top panel #16202C | text #16202C | muted text #4A5565 | border #D5D9E0 | divider #E4E7EC
card #FFFFFF | selected row bg #E8F0FA, selection ring #1F4E8C | bar fill #CFDBEC | subtle #F6F7F9
control border #8A94A3 | top-panel muted #C5CCD6 | destructive #B3261E

## Dark palette
page bg #0F141B | top panel #0A0E13 | text #E6EAF0 | card/strip #1A222D | detail header #212B38
border/divider #2C3644 | selected row bg #1E3A5F, ring #4C8DDB | bar fill #2A4568 | control bg #1F2937, control border #566274
link/accent #8CC1F0 | muted #9AA6B5 / #8A94A3 | track #2C3644

## Usage bar states (thresholds are on percent used)
| Range | Label | Light fill / text | Dark fill / text |
|---|---|---|---|
| < 50% | Plenty of space | #2E7D32 / #1B5E20 | #43A047 / #6FCF7A |
| 50% to <75% | Filling up | #F2C14E / #7A5B00 | derive a dark-mode equivalent |
| 75% to <100% | Running low | #E07B1F / #9A4A00 | derive a dark-mode equivalent |
| 100% | Disk full | #C62828 / #B3261E | derive a dark-mode equivalent |
Never rely on color alone: each state has its own icon and text label.

## Interaction and accessibility notes
- Touch-friendly: 44px minimum hit targets, 56px rows.
- Rows are buttons with aria-pressed; the progress bar has role=progressbar with aria-valuenow.
- Light and dark must both ship; follow the OS preference with a manual override.
