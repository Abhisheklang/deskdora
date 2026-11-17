<p align="center"><img src="src/deskdora/static/logo.svg" width="120" alt="DeskDora logo"></p>

# DeskDora

**Local-first desktop agent for Linux (Ubuntu/GNOME): accessibility-tree grounding with vision fallback, Ollama-powered planning, a mandatory-confirmation safety layer, and a complete audit log of every action.**

DeskDora operates your desktop for you. You type a task in plain words —
*"Organise folder ~/Downloads by type"*, *"Fill the form: name=…, email=…,
city=… and submit"* — and DeskDora does the clicking and typing itself:
opening apps, pressing buttons, filling fields, reading pages back to you.

It is **local-first**: by default, planning runs on your machine (a
local model via Ollama, or a built-in deterministic planner that needs
no model at all), and nothing leaves the machine. Cloud planners are
**opt-in** (v3.1): if you add your own API key for a provider and
select it, your goal and a text summary of your screen go to that
provider — never otherwise, and never by default.

![DeskDora app window](docs/screenshots/ui-run.png)

## Why it works (and when it doesn't)

Most computer-use agents guess pixels from screenshots and misclick
constantly. DeskDora doesn't guess: it reads the desktop through the
operating system's **accessibility tree (AT-SPI)** — every window, button,
text field and menu as a numbered, labelled element — and the planner
picks an element, the same trick that made browser automation reliable.
A vision path (screenshot + OCR / vision model) exists as a fallback for
surfaces the tree can't describe.

DeskDora also **fails honestly**: if it cannot find an element, cannot
plan a goal, or a confirmation is denied, it stops and says why. It never
clicks randomly hoping something works.

## Safety is the product

An agent that can operate your whole PC can also delete your files. So:

- **Mandatory confirmation** before any action that deletes, sends,
  submits, or installs — in the web UI, a dialog blocks the run until
  *you* decide. This cannot be switched off.
- **Application allow-list** — restrict which apps the agent may launch.
- **Kill switch** — an always-visible button (and a stop file / signal)
  that halts the agent before its next step.
- **Complete audit log** — every run is recorded as structured JSONL
  with a screenshot per step, replayable in the UI. Nothing is invisible.
- **Sandbox mode** — run it inside a VM first (that's how we test it),
  let it earn your real machine later.

## Quick start (Ubuntu 22.04 / 24.04, GNOME, Xorg session)

**Easiest — the installable app (Ubuntu 24.04):**

```bash
sudo apt install ./deskdora_3.1.0_amd64.deb
```

Then launch **DeskDora** from your applications menu: it starts the local
service and opens the DeskDora application window (a native GTK window —
no browser involved). The package is self-contained — its Python
dependencies are bundled and installed into a private virtualenv, no
network needed beyond apt's own archives.
(Build it yourself with `packaging/build-deb.sh`.)

**From source:**

```bash
# System dependencies
sudo apt install python3-pyatspi python3-gi at-spi2-core xdotool scrot \
                 gir1.2-atspi-2.0 gir1.2-gtk-3.0 gir1.2-webkit2-4.1

# DeskDora itself
pip install deskdora        # or: pip install .  from a source checkout

# Optional: a local LLM planner
ollama pull qwen2.5:1.5b    # DeskDora works without it, too

# Run one task from the terminal
deskdora run "Organise folder ~/Downloads by type"

# Or start the web UI (local only, 127.0.0.1:8765)
deskdora serve
```

> **Session support:** the reference session is **Xorg/X11** ("Ubuntu on
> Xorg" on the login screen), where every feature is verified. A
> **Wayland driver** (v1.1) works through xdg-desktop-portal —
> AT-SPI perception, the Screenshot portal (with a ScreenCast/PipeWire
> fallback), and RemoteDesktop input where the compositor's portal
> backend implements it (GNOME/KDE do; the wlroots backend does not
> implement RemoteDesktop at all, and in a headless test session its
> screencopy format negotiation also blocks capture — the exact probe
> results ship in the release report). Set `driver` in the config to
> `linux-wayland` to use it.

## Planners

| Planner | What it is | When it's used |
|---|---|---|
| `ollama` | A local LLM (default `qwen2.5:1.5b`) writes a JSON step plan; every plan is validated against an action allow-list before anything runs | `deskdora run --planner ollama`, or auto mode when a server is reachable |
| `builtin` | A deterministic, rule-based planner with a small documented grammar (organise folders, settings toggles, form fills, article fact-finding, PDF→spreadsheet, open/click/type/press chains) | Offline, modest hardware, tests & benchmark |
| cloud providers | 20+ named providers (OpenAI, Google Gemini, Anthropic Claude, DeepSeek, Groq, Mistral, OpenRouter, xAI, Cohere, Azure OpenAI, LM Studio, …) over three wire protocols, using **your own API key** | Settings → AI Providers; select the provider as the planner |
| custom | Your own endpoint: a name, a base URL, a protocol (OpenAI-compatible / Anthropic / Gemini-style) and a model ID | Settings → AI Providers → Custom provider |

Cloud and custom planners are off by default and entirely optional —
they exist for machines without a local model. Keys are stored only in
the local config file, are masked everywhere in the UI and API, and
the active planner — local, cloud or custom — is badged in the UI and
recorded in the run log.

The active planner is **recorded in every run log and shown in the UI** —
no silent substitution. Free-form goals the built-in planner can't parse
are refused honestly rather than improvised.

## Features

- Native desktop app window (GTK/WebKit): give the agent a task and
  follow every step in the activity feed while it works
- Whole-desktop control: file manager, settings, browser, office apps,
  terminal — anything exposing an accessibility tree
- Hybrid grounding: accessibility tree first, vision fallback
- **Platform drivers** (v1.1+): Linux/X11 (reference, verified),
  Linux/Wayland via desktop portals, macOS (Accessibility + Quartz)
  and Windows (UI Automation + SendInput) — the macOS and Windows
  drivers are implemented and conformance-tested but **not yet run
  on real hardware**, and they say so in the UI
- Safety layer: confirmations, app allow-list, sandbox mode, kill
  switch, and a **focus guard** (v1.1) that refuses to type into the
  wrong window instead of following focus blindly
- **Per-category autonomy** (v3.0): files / apps / web / messages /
  money, each free-run, confirm-first, or never — enforced on every
  action, with a hard floor: money can never be set to free-run
- Audit log with before/after screenshots and step-by-step replay
- **Resident memory** (v2.0): places, apps and habits learned from
  successful runs, consulted by the planner ("Organise folder my
  messy by type"), inspectable and deletable in the Memory view
- **Voice** (v2.0): spoken progress updates via offline TTS (espeak),
  and voice input through a pluggable offline STT pipeline (Vosk /
  PocketSphinx)
- **Routines**: save a successful run and re-run it in one click, on
  a **cron schedule** or a **folder-change trigger** (v2.0) — stored
  locally, on your machine only
- **Proactive proposals** (v3.0): when a watched folder fills with
  loose files, DeskDora proposes an organise — and only executes it
  unasked if the files category is set to free-run
- **Phone companion** (v3.0): a mobile web companion served on your
  local network — status, confirmations, stop — with a PIN guard
  for any non-local control
- **API key** (v3.1): DeskDora can issue its own revocable API key
  (Settings → Access) for programmatic control of its HTTP API
- **MCP server** (v3.1): `deskdora-mcp` exposes DeskDora to MCP
  clients (run tasks, read status and logs, approve confirmations,
  stop) — through the same safety layer, with no bypass (below)
- **Benchmark**: 25 standard desktop tasks with end-state assertions;
  results published in [BENCHMARK.md](BENCHMARK.md), failures included

## Programmatic access: API key & MCP

**API key.** Settings → Access → *Generate key* shows a key once;
only its hash is kept. Present it as `X-Api-Key: <key>` (or an
`Authorization: Bearer <key>` header) to call the HTTP API from
scripts and other tools — start runs, read status and run logs,
answer confirmations. Revoking it takes effect immediately.

**MCP server.** `deskdora-mcp` is a stdio MCP server. It talks to the
running DeskDora service, so an MCP client gets exactly the powers
of the UI — and the same limits: a run it starts still pauses for
confirmation, still obeys the autonomy policy, and still dies on the
kill switch. To connect Claude Desktop (or any MCP client), add:

```json
{
  "mcpServers": {
    "deskdora": {
      "command": "deskdora-mcp",
      "env": { "DESKDORA_URL": "http://127.0.0.1:8765" }
    }
  }
}
```

Tools: `run_task`, `get_status`, `list_runs`, `get_run_log`,
`approve_confirmation`, `deny_confirmation`, `stop`.

## Honest limits

- The X11 driver is the verified reference. The Wayland driver's
  real-world level depends on the compositor's portal backend (see
  the session note above); the macOS and Windows drivers have never
  run on real hardware. GNOME is the reference desktop; other AT-SPI
  desktops (KDE, Cinnamon) are untested.
- Some widgets expose their rows poorly to the accessibility tree
  (certain icon views); tasks depending on those can fail — the agent
  reports the failure instead of working around it silently.
- The built-in planner understands a defined grammar, not arbitrary
  English, and its parsing is strict: a goal it cannot consume in
  full is refused ("I can't parse this goal") rather than partially
  executed. The Ollama planner handles free-form goals, but a small
  local model will plan imperfectly — plans are validated, and failed
  steps stop the run rather than cascade.
- Offline speech recognition is only as good as its small local
  models: in our reference test setup, Vosk and PocketSphinx transcribe a
  real human voice well and synthetic (espeak) speech poorly.
- Benchmark numbers come from a virtualised XFCE desktop standing in
  for GNOME (the reference test setup); see BENCHMARK.md for exactly what ran.
- The cloud provider integrations are verified against local mock
  servers implementing each provider's documented protocol; the
  per-release verification record ships with the release report.

## Development

```bash
python3 -m venv --system-site-packages .venv   # pyatspi is a system pkg
.venv/bin/pip install -e .[dev]
.venv/bin/pytest
deskdora bench          # full benchmark (needs a live X session)
```

## Licence

MIT — see [LICENSE](LICENSE). DeskDora is 100% original code.
