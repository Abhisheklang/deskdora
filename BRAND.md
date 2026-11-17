# DeskDora Brand System

The visual identity for DeskDora, rebuilt from scratch in November
2026 after two earlier attempts were rejected as basic. This version
starts from published, community-curated palettes (sources below),
not from colours invented at the keyboard, and it changes the
product's whole register: out goes the violet-and-amber "AI SaaS"
look; in comes a deep-sea control room.

**The idea: "Deep Current."** DeskDora is a calm, powerful presence
working beneath the surface of your desktop. The interface is deep
water at night — dark blue-green, quiet, controlled — and the
brand colour is the current running through it: a seafoam teal
that means *the agent is present and working for you*. Nothing in
the UI shouts. State changes are carried by small, exact colour
signals, the way instruments on a bridge change: one light, one
meaning.

## Research sources

Palettes were gathered from the two sources the product owner
named — Coolors and ColorHunt — on 2026-11-17. Exact hexes below
were read from the pages listed; nothing is approximated.

| # | Palette | Hexes | Source |
|---|---|---|---|
| A | Coolors — "Deep Blue Sea" | `#0B132B` `#1C2541` `#3A506B` `#5BC0BE` `#6FFFE9` | Similar-palettes listing on coolors.co/palette/134074-13315c-0b2545-8da9c4-eef4ed |
| B | Coolors — the iconic five | `#EF476F` `#FFD166` `#06D6A0` `#118AB2` `#073B4C` | coolors.co/ef476f-ffd166-06d6a0-118ab2-073b4c (Coolors names: Bubblegum Pink, Golden Pollen, Emerald, Ocean Blue, Dark Teal) |
| C | Coolors — Yale/Oxford/Prussian | `#134074` `#13315C` `#0B2545` `#8DA9C4` `#EEF4ED` | coolors.co/palette/134074-13315c-0b2545-8da9c4-eef4ed |
| D | Coolors — "Deep Ocean Blue" | `#0A1128` `#001F54` `#034078` `#1282A2` `#FEFCFB` | Similar-palettes listing, same page as A |
| E | ColorHunt — teal/sage | `#557B83` `#39AEA9` `#A2D5AB` `#E5EFC1` | colorhunt.co/palette/557b8339aea9a2d5abe5efc1 |

Honest research notes: ColorHunt's own pages render their palette
grids with JavaScript and could not be text-fetched in this
environment; palette E was verified through its canonical
ColorHunt palette URL (the hexes are encoded in the URL itself).
Dribbble's tag pages were unreachable (bot wall, empty response);
the layout language below — sidebar app shell, generous radii,
soft layered depth, big confident type — reflects the current
dashboard idiom visible across Dribbble's web-design tag as known
to the designer, not a scrape.

**Why this fusion:** A supplies the structure (deep navy surfaces,
seafoam primary) — maritime, technical, calm. B supplies the
semantics: four of the most battle-tested signal colours on the
internet, each already carrying the right psychology (emerald =
go/done, golden = caution/attention, pink-red = stop/danger,
ocean blue = information). C contributes the powder-blue muted
text that keeps long reading comfortable on navy. E confirms the
teal register is where current palette taste sits. The result is
fresh against the rejected violet/amber identity (hue family moves
from ~265° violet to ~174° teal) while every colour remains
traceable to a published palette.

## Colour palette

### Deep Sea — neutrals (the water)

Tinted blue-green, never pure grey, never violet. Steps marked ◆
are palette-source colours; the rest are interpolated within the
same hue family to complete the scale.

| Token | Hex | Use |
|---|---|---|
| `abyss` | `#060C16` | App background |
| `deep-900` | `#0B132B` ◆A | Sidebar, sunken wells |
| `deep-850` | `#0E1930` | Panel gradient base |
| `deep-800` | `#14213D` | Cards, raised panels |
| `deep-750` | `#1C2541` ◆A | Hover surfaces, chips |
| `deep-700` | `#26355A` | Inset wells, active nav |
| `line` | `#21304F` | Default borders |
| `line-strong` | `#3A506B` ◆A | Emphasised borders, table rules |

### Current — primary brand colour

Seafoam teal: the colour of the agent's presence. It owns the
primary call-to-action, the logo ring, links, focus rings, active
navigation, and the running state. Teal sits between blue (trust,
competence) and green (go, safety) — precisely the two things a
user must feel about software that operates their computer.

| Token | Hex | Use |
|---|---|---|
| `teal-200` | `#A7E8E2` | Light accents, gradient start on dark |
| `teal-300` | `#8FE3DE` | Wordmark gradient end, highlights |
| `teal-400` | `#5BC0BE` ◆A | **Primary** — CTA, brand moments |
| `teal-500` | `#3AA7A3` | CTA hover/pressed |
| `ocean-500` | `#118AB2` ◆B | Gradient partner, links, info |
| `ocean-700` | `#073B4C` ◆B | Logo tile gradient end |
| `aqua-glow` | `#6FFFE9` ◆A | Glow only: running pulse dot, logo desk line |
| gradient | `135deg, #5BC0BE → #118AB2` | Logo tile, primary CTA, progress |
| on-teal text | `#04211F` | Text on teal surfaces |

### State colours — semantic, never decorative

| State | Colour | Hex | Source | Meaning |
|---|---|---|---|---|
| Idle | Powder | `#8DA9C4` | ◆C | Nothing is happening |
| Running | Current teal | `#5BC0BE` | ◆A | The agent is acting — watch the feed |
| Needs confirmation | Golden pollen | `#FFD166` | ◆B | Paused — the agent needs *you* |
| Success | Emerald | `#06D6A0` | ◆B | Verified done |
| Danger / kill | Bubblegum red | `#EF476F` | ◆B | Destructive or stopped — kill switch, deny, failures |
| Info | Ocean blue | `#118AB2` | ◆B | Neutral information, benchmark data |

Text colours: primary `#EDF5F2` (a breath away from C's Mint Cream
`#EEF4ED`), muted `#8DA9C4` ◆C, faint `#5C7694` (derived).

## Typography

- **Typeface: Inter**, bundled locally as woff2 (latin subset,
  weights 400/500/600/700/800) in `src/deskdora/static/fonts/` —
  the app never fetches fonts from the network. Inter is the
  typeface of the current dashboard generation: neutral, exact,
  with tabular figures for data.
- **Data: JetBrains Mono** (400/600 woff2, bundled the same way) —
  activity feed, timestamps, step JSON, benchmark numbers. Data
  should look engineered, not typed.
- **Scale:** 30 / 24 / 20 / 17 / 15 / 14 / 13 / 12 / 11 px.
  Display headings 700 weight, letter-spacing −0.03em. Eyebrow
  labels 11px, 600, uppercase, letter-spacing 0.14em, teal or
  muted. Body 14.5px, line-height 1.6.
- **Wordmark:** "Desk" in primary text, "Dora" in the teal
  gradient — the capable half of the name carries the current.

## Layout

A true app shell, not a stack of centred cards:

- **Sidebar (252px)** — brand lockup, primary navigation with an
  animated active indicator, and at its foot the agent status card
  and the kill switch: the two safety elements are always on
  screen, in the same place, in every view.
- **Content** — fluid, max 1240px, 28px gutters. Views are
  composed: the Run view pairs the command panel with a session
  column; data views use full-width tables and card grids.
- **Responsive:** sidebar collapses to an icon rail under 1080px
  and to a bottom bar under 720px; grids stack; nothing overflows.

## Space, radius, elevation

- **Spacing:** 4pt scale — 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40.
- **Radius:** 8 (chips, small controls), 12 (inputs, buttons),
  16 (cards), 20 (panels, modal), 999 (pills).
- **Elevation:** three steps only — flat panels with a 1px line,
  raised cards (`0 18px 40px rgba(2,6,14,.45)` + top-edge light),
  and floating (modal) with backdrop blur. Coloured glow is
  reserved: teal under the primary CTA and the running pulse;
  nothing else glows.
- **Texture:** a very subtle ambient gradient mesh (teal and ocean
  at 5–7% opacity) drifting slowly behind the shell, plus a faint
  top light line on panels. Tasteful, static-safe, GPU-cheap
  (transform/opacity only).

## Motion principles

1. Motion explains state: the running pulse, the progress sweep,
   the feed entry arriving. Decoration-only movement is limited
   to the ambient background drift.
2. Everything animates on transform/opacity, 160–320ms,
   `cubic-bezier(.22,.8,.24,1)`. View changes cross-fade/slide
   8px. The modal scales from .96 with a fade.
3. Loading is shown as shimmer skeletons in the shape of the
   coming content — never spinners alone, never layout jumps.
4. `prefers-reduced-motion` disables the animation layer
   entirely, including the ambient drift.

## The logo

Unchanged geometry — it was well drawn — recoloured into Deep
Current: a rounded tile in the ocean gradient (`#1282A2` ◆D →
`#073B4C` ◆B), a bold white cursor (the agent's hand), the target
ring around its tip now in seafoam `#5BC0BE`, and the desk line
beneath in the `#5BC0BE → #6FFFE9` gradient — the surface lit by
the current. At 48px the cursor and ring remain the only elements
that need to read, and both are bold.

Source: `src/deskdora/static/logo.svg`; PNG renders in
`packaging/icons/`.

## Rules

1. Teal means the agent: presence, action, running. If an element
   is teal and the agent isn't involved, it's wrong.
2. Gold appears only when the agent is waiting on the user.
   Emerald only for verified success. Red only for danger.
3. Gradients are brand moments only: logo, wordmark accent,
   primary CTA, progress. Never card backgrounds.
4. Neutrals are always the Deep Sea scale — no pure greys, no
   violet tint, no second neutral family.
5. The kill switch is always visible, always in the sidebar foot,
   always red. It is never styled down for aesthetics.
6. `prefers-reduced-motion` disables the animation layer entirely.
