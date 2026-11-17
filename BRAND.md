# DeskDora Brand System

The visual identity for DeskDora, treated the way a product brand
treats it: a small set of colours with jobs, rules for where each one
may appear, and a written rationale — so the UI, the icon, and future
surfaces all feel like one product.

**The idea: "Night desk, warm intent."** DeskDora works on your
machine, often unattended, always under your control. The interface is
a calm, deep, indigo-tinted dark — the desk at night. Against that
calm, one warm colour — amber — always means the same thing:
**DeskDora is about to act, or needs you.** Amber is the colour of the
target ring in the logo and of the highlight box in the live view: the
exact spot the agent is about to touch. No other element may use
amber decoratively.

## Colour palette

### Ink — tinted neutrals (the material)

Neutrals are never pure grey. They are tinted with the primary hue
(indigo), the way Linear and Stripe tint theirs, so every surface
feels like the same material under the same light. Deep indigo reads
as calm, focused, and trustworthy — the correct emotional register
for software you allow to operate your computer.

| Token | Hex | Use |
|---|---|---|
| `ink-950` | `#07070E` | App background |
| `ink-900` | `#0C0C17` | Header, tab bar, sunken areas |
| `ink-850` | `#11111F` | Card base |
| `ink-800` | `#17172A` | Raised panels, inputs |
| `ink-700` | `#202036` | Hover states, inset wells |
| `ink-line` | `#23233E` | Default borders |
| `ink-line-strong` | `#303052` | Emphasised borders, dividers |

### Iris — primary brand colour

Violet-indigo is the colour of intelligence and trust in product
design (Stripe's indigo, Linear's violet): technical without being
cold, calm without being dull. Iris owns the brand mark, the
wordmark, links, focus rings, and informational accents. It is the
colour of DeskDora *thinking*.

| Token | Hex | Use |
|---|---|---|
| `iris-300` | `#B9AFFF` | Wordmark gradient end, light accents |
| `iris-400` | `#9D8CFF` | Links, secondary highlights |
| `iris-500` | `#7C66FF` | Primary iris |
| `iris-600` | `#6348E8` | Gradient end, pressed states |
| gradient | `135deg, #8B74FF → #5A54D1` | Logo tile, brand moments |

### Amber — the colour of intent (accent / action)

Amber and orange sit opposite indigo on the wheel and carry the
opposite psychology: energy, warmth, optimism, attention. Used
sparingly — research on accent discipline (and every strong brand
from Spotify to Figma) says an accent only works if it is rare.
Amber is reserved, without exception, for **action and attention**:

- the primary call-to-action ("Run task", "Run now", "Save"),
- the live-view target highlight and its label,
- the LIVE / running indicators and the run-progress bar,
- the confirmation state ("DeskDora needs you"),
- the target ring in the logo.

| Token | Hex | Use |
|---|---|---|
| `amber-300` | `#FFCF6B` | Gradient start, light accents |
| `amber-400` | `#FFB020` | The accent itself; highlight; ring |
| `amber-500` | `#FF9416` | Gradient mid |
| gradient | `135deg, #FFB020 → #FF7A29` | Primary buttons |
| on-amber text | `#221503` | Text on amber surfaces |

### State colours

State colours are semantic, never decorative. Each maps to exactly
one meaning everywhere in the product:

| State | Colour | Hex | Meaning |
|---|---|---|---|
| Idle | Ink / muted text | `#8F8FAE` | Nothing is happening |
| Running | **Amber** | `#FFB020` | The agent is acting — watch the highlight |
| Needs confirmation | **Amber**, stronger | `#FFB020` | The agent is paused and needs *you* |
| Success | Emerald | `#2FD48C` | Verified done |
| Danger / kill | Rose | `#FF5C6C` | Destructive or stopped — kill switch, deny, failures |
| Info | Sky | `#4CC3FF` | Neutral information, benchmark data |

Emerald is green's psychology — safety, "go", completion — kept at a
minty brightness so it sits comfortably on the ink neutrals. Rose is
a warm red, alarming without being garish; it is used *only* for the
kill switch, deny actions, and failure states, so it never loses its
meaning. Amber deliberately covers both "running" and "needs
confirmation": in both cases the correct user behaviour is the same —
**pay attention to what the agent is doing.**

## Typography

- **UI typeface:** Inter if available, otherwise the system stack
  (`system-ui, "Segoe UI", Roboto`). One family everywhere; personality
  comes from weight and spacing, not novelty fonts.
- **Display / headings:** 700–800 weight, letter-spacing −0.02em.
- **Body:** 15px, line-height 1.55.
- **Small / metadata:** 12.5px, muted text colour.
- **Data (activity feed, logs):** the system monospace stack — data
  should look like data.
- **Wordmark:** "Desk" in white, "Dora" in the amber gradient — the
  friendly half of the name carries the warm colour.

## Space, radius, elevation

- **Spacing:** 4pt base scale — 4 / 8 / 12 / 16 / 24 / 32. Cards pad
  at 20–24; sections separate at 16.
- **Radius:** 10 (inputs, small buttons), 14 (buttons, tiles),
  18 (cards), 24 (modal), 999 (pills, tabs).
- **Elevation:** shadows are tinted with ink, never pure black at full
  strength; the only coloured glows are iris under the logo/brand
  moments and amber under the primary CTA — glow is a brand signal,
  not decoration.

## The logo

A rounded tile in the iris gradient (the calm, intelligent base),
holding a bold white cursor — the agent's hand. Around the cursor's
tip, the **amber target ring**: the exact spot DeskDora is about to
touch. Beneath, the desk line is drawn in the amber gradient — the
surface the agent works on, lit by intent. The mark must survive at
48px (menu icon): at small sizes the cursor and ring are the only
elements that need to read, so both are drawn bold, with no fine
detail anywhere in the mark.

Source: `src/deskdora/static/logo.svg`; PNG renders for packaging in
`packaging/icons/`.

## Rules

1. Amber means action/attention. If an element is amber and the user
   doesn't need to act or attend, it's wrong.
2. Rose means danger. Nothing else is rose.
3. Gradients are brand moments only: logo, wordmark accent, primary
   CTA, progress. Never as card backgrounds or decoration.
4. Neutrals are always the indigo-tinted Ink scale — no pure greys,
   no second neutral family.
5. Animation is purposeful: things that move are alive (running,
   progress, the highlight). Nothing moves to decorate.
6. `prefers-reduced-motion` disables the animation layer entirely.
