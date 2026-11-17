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
