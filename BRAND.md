# DeskDora Brand System — Round 3: "Control Room"

The visual identity for DeskDora, rebuilt from scratch in November
2026 after two earlier identities ("Ink / Iris / Amber", then
"Deep Current") were rejected as basic. This version is not
invented at the keyboard and it is not a re-skin: it is built to a
brief derived from **live visual research** — award-winning work
opened and studied in a real browser on 2026-11-17 (sources
below) — and it changes the product's register completely: out
goes the soft, shadowed, seafoam "AI product" look; in comes a
**near-black instrument panel** run by typography, hairlines, and
a single acid accent.

**The idea: a control room, not a dashboard.** DeskDora operates
your whole computer. Its interface should feel like the panel of
a machine that does serious work: black surfaces, hairline
divisions, huge exact numerals, one lime signal colour, and a
human voice — set in serif — that narrates what the agent is
doing and asks before it does anything irreversible.

## Research sources (viewed live, 2026-11-17)

| Reference | What was taken from it |
|---|---|
| **Sweet Tape** — Awwwards Site of the Day | Acid lime `#B6FE00` as the single accent on near-black; huge condensed-grotesque headlines; a physical "pull" tab as an interface gesture |
| **Haulix** logistics dashboard — Dribbble | Near-black instrument panel; hairline dividers; **no drop shadows**; capacity bars ("used / total" fills); severity pills; palette `#040404` `#5C5C5B` `#F4F4F4` `#9E9F9F` `#CEDE62` |
| **Nixtio** dashboard — Dribbble | Solid black icon rail; oversized headline with lime pills set inline; capsule-segment progress; fat capsule-bar charts; palette `#060807` `#DAF64C` `#A4AF45` |
| **Grids** by Obys | Three colours only (`#000` `#FFF` `#ECD06F` mustard); the visible grid as a design element; numbered sections; a "00→100" preparing loader |
| **Perplexity Computer** | A serif display face for the agent's own voice; restraint everywhere else |
| **Cyera** | Agent work presented as a mission narrative, not a log dump |

## Colour palette — Option A: "Control-room lime"

One accent, sparingly. Everything else is black, grey, or white.

| Token | Hex | Use |
|---|---|---|
| `void` | `#040404` | App background |
| `rail` | `#060807` | Icon rail, shell chrome (Nixtio) |
| `panel` | `#0A0B0A` | Instrument panels — one step off the blacks |
| `panel-2` | `#101110` | Raised rows, inputs, hover |
| `hairline` | `#5C5C5B` @ 28% | All dividers, borders, grid rules (Haulix) |
| `hairline-soft` | `#5C5C5B` @ 14% | Secondary rules, timeline spine |
| `text` | `#F4F4F4` | Primary text |
| `text-2` | `#9E9F9F` | Secondary text, labels |
| `text-3` | `#5C5C5B` | Faint text, disabled, placeholders |
| **`lime`** | **`#CEDE62`** | **THE accent** (Haulix) — a tile, a pill, a progress fill, a dot. Never a wash, never a background field |
| `lime-live` | `#DAF64C` | The single most-live element only (Nixtio): the executing step |
| `lime-dim` | `#A4AF45` | Lime at rest: completed segments, quiet brand marks |
| **`mustard`** | **`#ECD06F`** | **Reserved**: the kill switch / irreversible danger ONLY (Grids) |
| `ok` | `#06D6A0` | Success — pills only |
| `bad` | `#EF476F` | Failure / critical — pills only |

Rules that follow from the palette: lime never tints a large
surface; mustard appears nowhere except the stop gesture and
irreversible-action warnings; status colours are sampled
sparingly, as pills, never as panel fills.

## Light palette — v3.5: "Control Room, daylight"

The same system on paper, added in v3.5 (user order, 2026-11-19).
Light is the **default theme**: a fresh install — and any existing
install, whose config predates the `theme` key — opens in light
mode, because light reads better in screen recordings. Dark
remains the palette above, byte-for-byte; the user switches in
Settings → Appearance, the change applies immediately (no
restart), persists in the config, and reaches every surface: the
main window, the companion page, and the floating window.

One structural rule makes the two themes possible without
touching a single layout rule: **the accent exists in two forms.**
As a *fill* it is always the bright brand value, always paired
with `#040404` ink (`#CEDE62` fill + ink measures 13.9:1). As
*text, icon or border* on a surface it uses the parallel `-ink`
tokens — identical to the fills in dark mode, deepened in light
mode, because bright `#CEDE62` as text on white measures 1.5:1
and is forbidden there.

| Token | Hex | Use |
|---|---|---|
| `paper` | `#F2F2EC` | App background (the light `void`) |
| `rail-light` | `#E7E7DD` | Icon rail, shell chrome |
| `panel-light` | `#FFFFFF` | Instrument panels |
| `panel-2-light` | `#F5F5EF` | Raised rows, inputs |
| `panel-3-light` | `#E3E3D8` | Tracks: progress, capacity, switch-off |
| `hairline-light` | `#5C5C5B` @ 38% | All dividers and borders |
| `hairline-soft-light` | `#5C5C5B` @ 20% | Secondary rules |
| `ink` | `#191A14` | Primary text |
| `ink-2` | `#54554C` | Secondary text, labels (7.6:1 on white) |
| `ink-3` | `#7E7E72` | Faint text, placeholders (parity with dark `text-3`) |
| `lime` (fill) | `#CEDE62` | Unchanged — tiles, pills, fills, with `#040404` ink |
| `lime-live` (fill) | `#DAF64C` | Unchanged — the executing step |
| **`lime-ink`** | **`#5C6B1C`** | **Lime as text/icon/border** (5.9:1 on white, ≥4.7:1 on every light surface) |
| `lime-dim-light` | `#66731F` | Completed segments; lime as small text (5.2:1 on white) |
| `mustard` (fill) | `#ECD06F` | Unchanged — stop fills, with `#040404` ink |
| **`mustard-ink`** | **`#7D6318`** | **Mustard as text/border**: pull-tab, blocked pills (5.7:1 on white) |
| `ok-light` | `#067A5C` | Success as text/border (5.3:1 on white) |
| `bad-light` | `#C2254F` | Failure as text/border (5.7:1 on white) |
| `overlay-card-light` | `#FBFBF7` | Floating window card, with a `#66731F` border and ink text |

The **edge light does not follow the theme**: it paints lime /
amber on the desktop itself, not on an app surface, and its job
is to be seen against arbitrary desktop content in either mode.
The kill switch keeps its meaning in both themes — mustard is
still danger, only, and in light mode it speaks through
`mustard-ink` whenever it is text or an outline rather than a
fill.

## Typography

Three voices, each with one job. All bundled locally as woff2 —
the app never fetches fonts from the network.

- **Display: Oswald** (variable, 400–700) — a condensed grotesque.
  Headlines, the current task rendered at display size, KPI
  numerals. Tight tracking (−0.02…−0.04em), uppercase for
  headlines. Type is the hero: hierarchy comes from size and
  weight, not colour.
- **Serif: Newsreader** (variable + italic) — the agent's voice,
  and nothing else: the confirmation question, and the one-line
  agent summary on the Run screen. When DeskDora "speaks", it
  speaks in serif.
- **UI/body: Inter** (400–800) — controls, prose, tables.
- **Mono: JetBrains Mono** (400/600) — timestamps, step IDs,
  paths, section numbers, labels. 11–12px, uppercase,
  letter-spacing 0.14em.

Scale anchors: run-task headline 56–72px (clamped to fit),
view headlines 40–56px, KPI numerals 48–64px, modal question
28–36px serif, body 14.5px.

## Layout

- **76px solid black icon rail** (`#060807`): logo tile at top;
  Run / Action log / Routines / Benchmark / Settings as icons
  with mono micro-labels; agent-state dot and the kill indicator
  pinned at the bottom. No wide labelled sidebar.
- **Slim top strip**: the current run's name in large Oswald, a
  live clock (mono), and the agent state as ONE pill —
  `IDLE` / `RUNNING` / `AWAITING CONFIRMATION`.
- **Visible 12-column grid**, 24–32px gutters, hairline dividers;
  sections numbered `01`–`04` in small mono. The grid is a design
  element, not an accident.
- **Run screen = one composition.** Left 7 columns: the current
  task as an oversized headline (the goal text itself), the
  current step carried in a lime pill inside the headline area,
  the big Run control, one segmented capsule progress row (steps
  done vs remaining). Right 5 columns: ONE dark instrument panel —
  action being performed now, target app/window, a capacity bar
  ("Steps 14 / 20"), next actions as a numbered list. Idle, the
  same composition is the "give a task" state: giant headline
  prompt, input integrated into the composition (no form card),
  quick-start chips as pills.
- **The kill switch breaks the grid — exactly once.** A fixed,
  full-height pull-tab on the right edge: "PULL TO STOP",
  mustard-outlined. A physical gesture (Sweet Tape's pull tab),
  not a corner button. Clickable across its whole area and
  keyboard-operable.
- **Activity / Action log**: one timeline — hairline spine, one
  line per event: mono timestamp, status pill, plain-language
  action, result. No card per event. The currently-executing row
  is the only lime-filled row in the product.
- **Benchmark**: one wide statistics panel — fat capsule bars per
  task (fill = verdict) and a KPI strip of huge numerals with
  mono labels, every number carrying its denominator
  ("20/25 TASKS · 5/5 GATE").
- **Routines**: pill list, not cards. **Settings**: quiet
  two-column hairline rows.
- **Confirmation modal**: a centred black panel; the question in
  Newsreader serif at 28–36px stating the exact action ("Allow
  DeskDora to submit this form?"); the step in mono beneath;
  full-width Confirm (lime) / Cancel (hairline ghost).

## Motion

- **Full-field lime wipe** on view transitions — a lime plane
  sweeps, the view changes beneath it. The one theatrical gesture,
  used once per navigation.
- **"Preparing… 00→100"** run-start loader moment (Grids homage) —
  fast, under a second, skippable by reality: it yields the moment
  the plan actually arrives.
- Capsule bars and progress segments grow from zero on view entry.
- Timeline rows ease in (opacity/translate, ≤240ms).
- Nothing bounces. Everything is transform/opacity, GPU-cheap.
  `prefers-reduced-motion` disables the motion layer entirely,
  including the wipe and the loader.

## The logo

The cursor-and-ring geometry, recoloured to the system and
flattened: a single lime tile (`#CEDE62` — lime used as a tile is
the accent's sanctioned form), the cursor, target ring, and desk
line all in solid `#040404` black. No gradients, no shadows, no
glow. Source: `src/deskdora/static/logo.svg`; PNG renders in
`packaging/icons/`.

## The 5 rules

1. **One accent, sparingly.** Lime is a tile, a pill, a fill, or a
   dot — never a wash. Mustard is danger, only.
2. **No drop shadows.** Hairlines, the solid rail, and scale do
   the work shadows used to do.
3. **Instruments, not cards.** Every number has a denominator
   and a fill; every panel reads like a gauge.
4. **Type is the hero.** The grid is visible — and broken exactly
   once, by the pull-tab.
5. **The agent has a narrative and a serif voice.** It says what
   it is doing in plain language, and it asks — in serif — before
   anything irreversible.

## Addendum — v2.0 / v3.0 surfaces (same system, no new language)

The Control Room system above governs every surface added after the
round-3 redesign; nothing below introduces a new colour, face, or
rule.

- **Memory view** (rail: database glyph, label MEMORY): places as
  numbered hairline rows (`P—01`…), the path in JetBrains Mono, the
  source (`USER` / `LEARNED`) as a mono meta line; apps and habits in
  the same row system; forgetting is a ghost pill per row and one
  ghost "clear all" — destructive styling stays the routine-delete
  pattern (hairline, red on hover), never mustard.
- **Proposals** live on the Run screen as section `04 · PROACTIVE`:
  the same row system, the goal as an Oswald row title, `ORGANISE IT`
  as the one lime pill; auto-executed proposals are badged `AUTO`.
- **Autonomy settings** are ordinary Settings hairline rows: a mono
  category label, a pill select, a grey hint. Money's select simply
  has no free-run option — the floor is visible by absence.
- **Companion** (`/companion.html`) is the Control Room at phone
  width: same tokens, same pills, one card per concern, the stop
  control mustard-outlined. It is a *client of the same service*,
  not a second design.
- **First-run setup wizard** (v3.6): a full-window takeover, not
  a modal — the numbered section head counts the steps
  (`01 WELCOME` … `05 ALL SET`), each step is one Oswald headline
  and one hairline panel, and the footer carries a five-segment
  progress rule (segments fill lime as steps complete), a ghost
  SKIP SETUP, and one lime primary. No new tokens: the wizard is
  built entirely from the active theme's variables, so it is
  correct in both palettes and re-skins live on the Appearance
  step. Errors are inline in the step that caused them (hairline
  red box), never a separate page.
- **Planner error page** (v3.7): the third failure surface, for
  the one case the other two don't cover — pre-run validation
  finds a *definitive* planner setup problem (unknown provider
  or model, model not installed, API key missing or rejected),
  so the run cannot start at all. Same full-window takeover
  pattern as the wizard: numbered section head (`!! PLANNER
  PROBLEM`), one Oswald headline naming the problem in plain
  words, a hairline panel carrying the configured planner, the
  problem class in `bad` red, and the fixes on the page (model
  picker, model download with the wizard's progress bar, a route
  into provider settings), one lime primary (CHECK AGAIN). No
  new tokens — correct in both palettes. The three surfaces
  stay distinct by job: unreachable → the v3.3 connection modal;
  definitive misconfiguration → this page; a wizard step's own
  error → inline in the step.
- **Positioning line** (v3.7.2): the product line every surface
  carries — *local-first by default, with optional online paths*.
  By default nothing leaves the machine (Ollama or the built-in
  planner); the online paths are opt-in only — cloud planners
  with the user's own API key (which receive the goal and a text
  summary of the screen, as Settings discloses), and the phone
  companion over the local network behind its pairing PIN, with
  no cloud relay. Copy must never call DeskDora offline-only or
  imply an "online version": it is one app, local by default.
