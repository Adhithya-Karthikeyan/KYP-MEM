# Plan: kyp-mem UI Rewrite

## Overview

Full rewrite of `kyp_mem/static/index.html` (~2967 lines) to match the design handoff in `design_handoff_kyp_mem_ui/`. The existing vanilla JS + API data layer stays; the entire CSS, HTML structure, and rendering logic gets rebuilt.

**Stack**: Vanilla JS (no React), marked.js (markdown), D3.js (graph force layout for full graph)
**Target file**: `kyp_mem/static/index.html` (single-file rewrite)
**Backend**: `kyp_mem/ui.py` — untouched, all API endpoints preserved
**Design reference**: `design_handoff_kyp_mem_ui/` (README.md, kyp-mem.html, app.jsx, views.jsx)
**Do NOT port**: `data.js` (mock data), `tweaks-panel.jsx` (sandbox-only)

### User Requirements
- Resizable sidebar (drag handle) — carry over from existing UI
- Match design tokens and behavior exactly

---

## Phase 0: Reference — API Endpoints & State

These are the live API endpoints the rewritten UI must call (all from `kyp_mem/ui.py`):

| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/tree` | GET | `{type, name, path, children[], tags[]}` nested tree |
| `/api/stats` | GET | `{notes, folders, tags, links}` |
| `/api/note/{path}` | GET | `{path, title, content, tags[], properties{}, created, updated, links[], backlinks[{path,title,context}], unlinked[{path,title,context}], related[{path,score,title}]}` |
| `/api/search` | GET `?q=&tag=` | `[{path, score, snippet, title}]` |
| `/api/tags` | GET | Tag structure from vault |
| `/api/sessions` | GET `?project=` | `{project: [{path, title, tags[], created, updated, summary}]}` |
| `/api/sessions/search` | GET `?q=&project=` | `[{path, title, distance, snippet}]` |
| `/api/sessions/create` | POST `{project, summary}` | `{ok, path}` |
| `/api/projects` | GET | `[{name, session_count}]` |
| `/api/projects/create` | POST `{name, overview}` | `{ok, path}` |
| `/api/note/{path}` | POST `{content, tags[], properties{}}` | `{ok, path}` |
| `/api/note/{path}` | DELETE | `{ok}` |
| `/api/reload` | POST | `{ok, stats}` |

**Global state to preserve**:
- `currentPath`, `currentNote`, `treeData`, `allNotes{}`, `activeTagFilters` (Set)
- `graphVisible`, `isGraphMaximized`, `graphHeight`
- `editingPath`, `editingNote`
- `qsSelectedIndex`, `lastKnownStats`

**New state for design**:
- `view`: "note" | "session" | "graph"
- `activeSession`: string | null
- `sidebarOpen`: boolean (localStorage)
- `railOpen`: boolean (localStorage)
- `density`: "dense" | "regular" | "comfy" (localStorage)

**localStorage keys** (existing + new):
- `kyp-sidebar-w` (existing, keep for resize)
- `kyp-graph-h` (existing, keep for graph resize in rail)
- `kyp-sidebar-open` (new)
- `kyp-rail-open` (new)
- `kyp-density` (new)

---

## Phase 1: CSS Foundation & Layout Shell

**Goal**: Replace all CSS and HTML structure. Create the empty shell with topbar, main area, sidebar slot, content slot, and status bar. Nothing interactive yet.

### Tasks

1. **Replace the entire `<head>` section**:
   - Remove Inter font, keep only JetBrains Mono (weights 400, 500, 700)
   - Font URL: `https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap`
   - Keep `marked.min.js` and `d3.min.js` CDN links
   - Remove old `<style>` block entirely

2. **Write new `:root` CSS custom properties** — copy exact OKLCH values from design:
   ```css
   :root {
     --bg: oklch(0.165 0.008 60);
     --bg-2: oklch(0.195 0.009 60);
     --panel: oklch(0.215 0.010 60);
     --panel-2: oklch(0.245 0.010 60);
     --line: oklch(0.305 0.012 55);
     --line-2: oklch(0.385 0.014 55);
     --fg: oklch(0.945 0.012 75);
     --muted: oklch(0.68 0.012 65);
     --dim: oklch(0.50 0.010 60);
     --accent: oklch(0.78 0.13 45);
     --accent-dim: oklch(0.55 0.10 45);
     --warn: oklch(0.82 0.13 80);
     --pin: oklch(0.78 0.13 30);
     --link: oklch(0.78 0.10 220);
     --r-sm: 3px; --r: 5px; --r-lg: 8px;
     --pad-y: 14px; --pad-x: 18px; --row-y: 10px; --gap: 14px;
     --fz: 13px; --fz-sm: 11.5px; --fz-xs: 10.5px; --fz-lg: 15px; --fz-xl: 22px;
   }
   ```

3. **Add density variants**:
   ```css
   html[data-density="dense"] { --pad-y:10px; --pad-x:14px; --row-y:6px; --gap:10px; --fz:12px; --fz-sm:11px; --fz-xs:10px; --fz-lg:14px; --fz-xl:20px; }
   html[data-density="comfy"] { --pad-y:18px; --pad-x:22px; --row-y:14px; --gap:18px; --fz:14px; --fz-sm:12px; --fz-xs:11px; --fz-lg:16px; --fz-xl:24px; }
   ```

4. **Base styles**: body (font, background, overflow hidden, font-feature-settings), selection highlight, scrollbar (10px, thumb with 2px transparent border), keyframe animations (kypBlink, kypCaret), utility classes (.acc, .mut, .dim, .tab-nums, .hairline, .lnk)

5. **Layout HTML shell**:
   ```html
   <div id="app" style="display:grid; grid-template-rows:44px 1fr 24px; height:100vh;">
     <header id="topbar">...</header>
     <main id="main" style="display:grid; grid-template-columns:236px 1fr; min-height:0; border-top:1px solid var(--line);">
       <aside id="sidebar">...</aside>
       <div id="resize-handle-sidebar" class="resize-handle"></div>
       <section id="content-area">...</section>
     </main>
     <footer id="statusbar">...</footer>
   </div>
   ```
   - Main grid columns: `var(--sidebar-w, 236px) auto minmax(0, 1fr)` (auto for the resize handle)
   - Sidebar toggle: when hidden, set `grid-template-columns: 0 0 minmax(0,1fr)` and hide sidebar

6. **Resize handle CSS** (from existing UI, adapted):
   - 1px wide, cursor col-resize, hover shows accent color
   - Drag behavior preserved from existing `initResize()` pattern
   - Min 180px, max 400px, save to `kyp-sidebar-w` localStorage

### Verification
- Open `python3 -m kyp_mem.cli ui` in browser
- Page loads with correct background color (warm dark, not blue-black)
- JetBrains Mono renders (check font in DevTools)
- Grid layout visible: 44px topbar area, flexible main, 24px statusbar
- No JavaScript errors in console

---

## Phase 2: TopBar & StatusBar

**Goal**: Build the fixed top bar and bottom status bar with all their elements. Wire up sidebar/rail toggle buttons (visual toggle only, no content yet).

### Tasks

1. **TopBar left cluster** (flex, gap 14, aligned center):
   - Sidebar toggle button: 22×22, inline SVG (rect + vertical line at x=5)
   - Logo: 8×8 coral square with glow + "KYP·MEM" (weight 700, letter-spacing 0.08em, color --accent, size --fz-lg)
   - Tagline: "know your project" (--dim, --fz-sm)
   - Dot separator "·" (--dim)
   - Breadcrumb: `<project> / <note>` — last part in --fg, earlier in --muted, separators in --dim

2. **TopBar right cluster** (flex, gap 8):
   - View switcher: 3-way segmented control ("note" / "session" / "graph")
     - Container: inline-flex, border 1px --line, radius --r-sm, padding 2, bg --panel
     - Active button: color --accent, bg color-mix(in oklch, var(--accent) 14%, var(--panel))
     - Inactive: color --muted, bg transparent
   - GhostBtn: "+ project"
   - Search bar: 220px, leading coral "$", input placeholder "search vault", animated caret (6×12 block, kypCaret animation), "⌘K" hint chip
   - Rail toggle button: 26×24, inline SVG (rect + vertical line at x=9), border 1px --line, bg --panel

3. **StatusBar** (24px, flex between, --fz-xs, --dim):
   - Left: notes count, folder count, "·", store path, sync status (green dot + "sync ok")
   - Right: index status, "·", live clock (updates every 30s), "·", version
   - Wire live clock with setInterval(30000)
   - Wire note/folder counts from `/api/stats` (populated in init)

4. **Wire toggle buttons**:
   - Sidebar toggle: flip `sidebarOpen`, update main grid columns, save to localStorage
   - Rail toggle: flip `railOpen`, save to localStorage (rail content comes in Phase 4)
   - View switcher: update `view` state variable, update active button styling

### Verification
- TopBar renders with logo, tagline, breadcrumb, view switcher, search bar, toggles
- Status bar shows at bottom with clock updating
- Sidebar toggle hides/shows sidebar column
- View switcher buttons highlight on click
- Search bar has blinking coral caret animation

---

## Phase 3: Sidebar

**Goal**: Build the three sidebar sections (Sessions, Projects, Tags) with live data from API.

### Tasks

1. **Sidebar container**:
   - background: var(--bg), flex column, overflow hidden
   - Scroll region: overflow auto, padding "12px 4px 12px 12px", flex column, gap 16

2. **SideSection helper function** `renderSideSection(title, count, options)`:
   - Header: uppercase --fz-xs, letter-spacing .08em, --dim
   - Colored dot (6×6): --accent for sessions/projects, --muted (opacity 0.4) for tags
   - Optional count badge (--dim tab-nums)
   - Optional "+ new" button (--dim, hover shows --line border and --fg color)
   - Collapsible: chevron "▾" rotates -90° when collapsed, transition 0.15s

3. **Sessions section**:
   - Semantic search input: placeholder "⌕ semantic search session…", bg --panel, border --line
   - Project folder header: "▾ ≡ kyp-mem · {count}" using TreeFolderHeader pattern
   - Session rows (indented 16px): "● MMM DD, HH:MM AM/PM"
     - Live sessions: dot + time in --accent
     - Finished sessions: dot in --dim, time in --muted
     - Active row: 2px left border --accent + 10% accent bg tint
   - Wire to `/api/sessions` data
   - Wire semantic search to `/api/sessions/search` (250ms debounce)

4. **Projects section**:
   - ProjectGroup: collapsible "▾ ≡ {name}" header
   - Note rows (indented 16px): "◇ {name}"
     - Active: left border --accent, label flips to --accent
   - Wire to `/api/tree` data (filter out Sessions folders)
   - "+ new" button → open project create modal

5. **Tags section** (collapsible):
   - Wrap-flex of TagChip components
   - TagChip: inline-flex, "#" prefix (--dim), name, optional count
     - Default: border --line, bg --panel, color --muted
     - Hover: border --line-2, color --fg
     - Active: border --accent-dim, bg with 12% accent mix, color --accent
   - Wire to tag data from allNotes (collectAllTags pattern)
   - Click toggles tag filter

6. **Resize handle** between sidebar and content:
   - 1px visual handle, expands hit area with ::before pseudo-element
   - Drag behavior: min 180px, max 400px, save to localStorage `kyp-sidebar-w`
   - Accent color on hover/drag

### Verification
- Sessions list populates from API with correct date formatting
- Session semantic search returns results and clicking loads note
- Projects tree shows with collapsible folders
- Tags wrap and highlight on click
- Sidebar resizes via drag handle
- Sidebar toggle button (topbar) hides/shows sidebar

---

## Phase 4: Note View & Right Rail

**Goal**: Build the default note view with markdown rendering and the right rail (local graph, outline, backlinks).

### Tasks

1. **Note view container** (when `view === "note"`):
   - Two-column grid: `minmax(0,1fr) 300px` (rail visible) or `minmax(0,1fr)` (rail hidden)
   - gap: var(--gap), height 100%, minHeight 0

2. **Article (left column, scrollable)**:
   - **Command prompt strip**: `$ kyp_read {path}` left, `↳ ok · {words} words · {reading}` right
     - --fz-xs, --dim, coral "$", tabular-nums
     - Word count: `note.content.split(/\s+/).length`
     - Reading time: `Math.ceil(wordCount / 200) + ' min'`
   - **Tag strip + meta + actions**: TagChips wrapping, created/updated dates, "↗ share" and "⌥ edit" GhostBtns
   - **Title (h1)**: calc(var(--fz-xl) + 8px) = 30px, weight 500, letter-spacing -0.01em
     - Blinking coral cursor: 0.5em × 0.85em block, kypBlink animation
   - **Sub-meta**: word count + reading time in --dim/--muted
   - **Body**: Parse markdown with `marked.parse()`, apply styling:
     - h2: 19px, weight 500, prefixed by dim "##"
     - h3: 15px, weight 500, color --accent
     - p: line-height 1.65, color --muted
     - code: bg accent@14% on --bg, color --accent, border accent@28%
     - Links, tables, blockquotes, lists — restyle to match design tokens
   - Wikilinks: same `[[link]]` → click handler pattern as existing

3. **Right rail (aside, scrollable, conditionally visible)**:
   - Controlled by `railOpen` state + topbar toggle
   - **Rail card helper** `createRailCard(title, content, options)`:
     - Header: uppercase --fz-xs, letter-spacing .08em, --dim, optional right icons
     - Body: border 1px --line, bg --panel, radius --r, padding 10px 12px
     - Collapsible variant with rotating chevron

   - **Local Graph rail card**:
     - SVG 240px tall, viewBox 0..100
     - Build nodes from note.links + note.backlinks + note.related (same logic as existing renderGraph)
     - Run D3 force simulation, map to 0..100 coordinate space
     - Render with design's node/edge styling:
       - Center node: fill --accent, halo circle r*1.9 at opacity 0.18
       - Note nodes: fill --panel-2, stroke --accent (0.5)
       - Session nodes: fill --bg, stroke --muted (0.35), dashed edges
     - Hover: highlight node + connected edges
     - Expand button (⤢) → switch to graph view
     - +/- buttons for zoom

   - **Outline rail card** (collapsible):
     - Extract headings from note.content (h1-h3)
     - Flat list, indented by level (level-1: --fg weight 500, deeper: --muted weight 400)
     - Click scrolls to heading
     - Hover: bg --panel-2

   - **Backlinks rail card** (collapsible):
     - "← {note title}" rows, link-styled
     - Click loads note

4. **Wire `loadNote(path)` function**:
   - Fetch `/api/note/{path}`
   - If `view === "note"`: render note view + right rail
   - If `view === "session"`: render session view (Phase 5)
   - Update breadcrumb, sidebar active states
   - Save to `currentPath`, `currentNote`

### Verification
- Click a note in sidebar → note view renders with command prompt, tags, title with cursor, markdown body
- Right rail shows local graph, outline, backlinks
- Rail toggle button hides/shows right rail
- Wikilinks in content are clickable and navigate
- Outline items scroll to headings
- Backlinks load the linked note
- Edit button opens editor modal

---

## Phase 5: Session View

**Goal**: Build the dedicated session view with stat grid and timeline.

### Tasks

1. **Session view container** (when `view === "session"`):
   - Single-column scrollable article (no right rail)
   - Full height, overflow auto

2. **Status strip**:
   - Live/done pill: dot (6×6) + label, accent for live, muted for done
     - Live: border --accent-dim, bg accent@14% on --panel, color --accent
     - Done: border --line, bg --panel, color --muted
   - TagChips: "session", "auto-captured"
   - Date/time in --dim
   - "⌥ resume" GhostBtn on right

3. **Title**: `Session {id} · {summary}`
   - h1, calc(var(--fz-xl) + 6px), weight 500, letter-spacing -0.01em
   - Extract session ID from path (e.g., "2026-05-13_102203")
   - Extract summary from note content (after "## Summary")

4. **Sub-meta**: tokens and captured count
   - Token count: derive from content length (rough estimate) or show word count
   - Captured count: count sections (INVESTIGATED, LEARNED, COMPLETED items)

5. **Stat grid** (4 columns):
   - Each card: border 1px --line, bg --panel, radius --r, padding 10px 12px
   - Label: uppercase --fz-xs, letter-spacing .06em, --dim
   - Value: --fz-lg, weight 500, tabular-nums
   - Stats: duration, messages, captured (--accent color), tokens
   - Derive values from session note content where possible

6. **Timeline**:
   - h2 "## Timeline" with dim "##" prefix
   - Parse session content sections (INVESTIGATED, LEARNED, COMPLETED, PROMPTS) into timeline rows
   - TLRow: 3-column grid (44px time | 56px badge | 1fr text)
     - Time: --dim --fz-xs tabular-nums
     - Badge: bordered pill with kind color (MSG=--muted, MEM=--accent, DEC=--warn, TOOL=--link)
     - Text: --fz-sm, --muted
   - Vertical guide: 1px --line connecting badges (position absolute, left 72)

7. **Wire session opening**:
   - Clicking a session in sidebar → set `view = "session"`, set `activeSession`, call `loadNote(path)`
   - `loadNote` detects session path (contains "/Sessions/") and renders session view instead of note view
   - Update view switcher active state
   - Update breadcrumb to "sessions / {session-id}"

### Verification
- Click a session in sidebar → session view renders
- Status pill shows live/done correctly
- Stat grid displays 4 cards
- Timeline renders with colored badges and vertical guide
- View switcher reflects "session" as active
- Can switch back to note view via switcher

---

## Phase 6: Graph View

**Goal**: Build the full-screen graph view with interactive node/edge rendering.

### Tasks

1. **Graph view container** (when `view === "graph"`):
   - Two-column grid: `minmax(0,1fr) 300px`, gap var(--gap), height 100%

2. **Graph canvas section** (left):
   - Container: border 1px --line, bg --panel, radius --r, overflow hidden, flex column
   - **Header bar**: bg --bg-2, border-bottom 1px --line, padding 10px 14px
     - Left: "▦ vault graph · {n}n · {e}e" (accent ▦, weight 500 "vault graph", dim stats)
     - Right: ToolChips ("force" active, "radial", "time") + GhostBtn "✕" close
   - **SVG canvas**: viewBox 0..100, xMidYMid meet, position absolute inset 0
     - Background: 3×3 dot pattern (circle r=0.12 in --line)
     - Build full vault graph from allNotes data (all notes + their connections)
     - Run D3 force simulation, normalize positions to 0..100
     - **Edges**: stroke rules per design (focused=--accent, default=--line-2, session edges dashed)
     - **Nodes**: two kinds (note: --panel-2/--accent stroke, session: --bg/--muted stroke)
       - Focused: fill --accent, halo at r*2, opacity 0.22
       - Dimmed (non-neighbor when something focused): opacity 0.32
     - **Labels**: monospace 1.7-1.9 font-size, below node (y + r + 2.6)
   - **Legend overlay** (bottom-left, glass): notes/sessions/link/session-ref
     - bg: color-mix(in oklch, var(--bg) 85%, transparent), backdropFilter blur(8px)

3. **Graph interactions**:
   - Hover node → highlight, dim non-neighbors, show labels for hovered + neighbors
   - Click node → set as selected (sticky focus), update right rail
   - Hover clears on mouseleave

4. **Right rail** (graph view):
   - **Focused card**: colored dot + label, "degree {n} · kind {kind}"
   - **Connections card**: list of neighbor rows: "└─ {label}" with "{kind}" on right
     - Click selects that node
     - Hover: bg --panel-2
     - Text overflow: ellipsis

5. **Wire graph view**:
   - View switcher "graph" → show graph view
   - Local graph "⤢" button → switch to graph view
   - Graph "✕" close button → return to note view
   - Build graph data from allNotes: all notes are nodes, links/backlinks are edges, sessions are special nodes

### Verification
- Click "graph" in view switcher → full-screen graph renders
- Nodes and edges display with correct colors
- Dot pattern background visible
- Hover a node → non-neighbors dim, edges highlight
- Click node → right rail updates with focused node info and connections
- Click a connection → graph focuses on that node
- "✕" button returns to note view
- Legend overlay visible at bottom-left

---

## Phase 7: Interactions, Modals & Polish

**Goal**: Wire up all remaining interactions, modals, keyboard shortcuts, and polish.

### Tasks

1. **Quick Switcher** (⌘O):
   - Overlay: fixed inset, bg with blur, center-top positioned
   - Input: full-width, monospace, placeholder "Jump to note..."
   - Results: filter allNotes by title/path, max 15, arrow key navigation
   - Enter/click loads note
   - Escape closes
   - Same logic as existing implementation

2. **Search** (⌘K):
   - Focus search input in topbar
   - Dropdown results: fetch `/api/search?q={query}` (200ms debounce)
   - Results: title, path, snippet
   - Click loads note, closes dropdown
   - Click outside closes

3. **Edit Modal**:
   - Overlay with blur backdrop
   - Modal: path header, textarea, ESC/SAVE buttons
   - Save: POST `/api/note/{path}` with content
   - ⌘S shortcut to save
   - Wire "⌥ edit" GhostBtn in note view

4. **Session Create Modal**:
   - Project input (with datalist from allNotes projects)
   - Summary textarea
   - POST `/api/sessions/create`, refreshTree, loadNote
   - Wire "+ new" button in sessions sidebar section

5. **Project Create Modal**:
   - Project name input, overview textarea
   - POST `/api/projects/create`, refreshTree, loadNote
   - Wire "+ project" GhostBtn in topbar and sidebar

6. **Tag Filtering**:
   - Click TagChip in sidebar → toggle in activeTagFilters
   - Apply filter: show/hide tree items by tag match
   - Active tags shown with × to remove
   - "clear" button to reset

7. **Keyboard shortcuts**:
   - ⌘K → focus search
   - ⌘O → open quick switcher
   - Escape → close any open modal/overlay
   - ⌘S → save (when editor open)

8. **Auto-refresh polling**:
   - `pollForChanges()` every 3000ms
   - Fetch `/api/stats`, compare with lastKnownStats
   - If changed: refreshTree + reload current note

9. **localStorage persistence**:
   - `kyp-sidebar-w`: sidebar width (resize)
   - `kyp-sidebar-open`: sidebar visibility
   - `kyp-rail-open`: right rail visibility
   - `kyp-density`: density preference
   - `kyp-graph-h`: graph height in rail

10. **Init sequence**:
    ```javascript
    async function init() {
      // Restore localStorage preferences
      // Parallel fetch: /api/tree, /api/sessions, /api/stats
      // Build allNotes map
      // Enrich allNotes with full note metadata (parallel)
      // Render sidebar (sessions, projects, tags)
      // Render status bar stats
      // Start polling interval
      // Apply density from localStorage
    }
    ```

### Verification
- ⌘O opens quick switcher, arrow keys navigate, Enter loads note
- ⌘K focuses search, results appear, click loads note
- Edit button opens editor, save persists, content updates
- Session/project create modals work end-to-end
- Tag filtering shows/hides notes correctly
- Page auto-refreshes when vault changes
- Sidebar open/close state persists across reloads
- Rail open/close state persists across reloads
- Density preference persists

---

## Anti-Pattern Guards

1. **Do NOT use React** — the design prototype uses React/Babel but our app is vanilla JS
2. **Do NOT import data.js** — use live API data, not mock data
3. **Do NOT port tweaks-panel.jsx** — lock in default values (coral accent, regular density)
4. **Do NOT change ui.py** — all API endpoints stay exactly as they are
5. **Do NOT use Inter font** — JetBrains Mono only (400, 500, 700)
6. **Do NOT use hex colors for tokens** — use OKLCH values from design spec
7. **Do NOT drop existing features** — search, quick-switch, edit, session create, project create, tag filter, graph, resize, polling must all be preserved
8. **Do NOT use inline React-style state** — use simple global variables and DOM manipulation
9. **Keep marked.js** for markdown rendering — the design uses structured blocks but real notes are markdown
10. **Keep D3.js** for force layout in full graph — the design has hand-laid coordinates but we need dynamic layout

---

## Execution Notes

- Each phase builds on the previous — they must be executed in order
- Phase 1 produces a mostly-empty shell; Phases 2-6 fill in the views; Phase 7 wires everything together
- The file is a single `index.html` — all CSS, HTML, and JS in one file
- Test after each phase by running `python3 -m kyp_mem.cli ui` and checking in browser
- The design files in `design_handoff_kyp_mem_ui/` are the pixel-level reference — compare side by side
