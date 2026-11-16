"""Gate & benchmark task definitions.

Each task is data plus a setup and an assertion.  The assertion checks
*externally observable end state* (files on disk, a settings read-back, a
saved submission, an extracted answer) — never the agent's own claim.
This module is shared by the v0.1 gate runner and the v1.0 benchmark.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures"
WORK_ROOT = Path(os.environ.get(
    "DESKDORA_TASK_WORK", os.path.expanduser("~/deskdora-tasks")))

SITE_PORT = 8901
SITE_URL = f"http://127.0.0.1:{SITE_PORT}/index.html"


@dataclass
class Ctx:
    task_id: str
    data: dict = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        d = WORK_ROOT / self.task_id
        d.mkdir(parents=True, exist_ok=True)
        return d


@dataclass
class Task:
    id: str
    title: str
    goal: str
    setup: object      # callable(Ctx) -> None
    assertion: object  # callable(Ctx, run_result) -> (bool, str)
    known_apps: object = None  # callable(Ctx) -> dict


def browser_command():
    import glob
    import shutil as _sh
    # Prefer a stock Chrome-for-Testing/Chromium build (the Playwright
    # cache ships one).  The sandbox's /opt/meta-chromium build refuses
    # standalone startup (it requires its own harness policy), so it is
    # deliberately not used.
    for pattern in (os.path.expanduser(
            "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome"),
            os.path.expanduser(
                "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")):
        hits = sorted(glob.glob(pattern))
        if hits:
            return (f"{hits[-1]} --force-renderer-accessibility "
                    f"--no-first-run --no-sandbox "
                    f"--user-data-dir=/tmp/deskdora-chrome")
    for cand in ("chromium", "chromium-browser", "google-chrome"):
        found = _sh.which(cand)
        if found:
            return (f"{found} --force-renderer-accessibility --no-first-run "
                    f"--no-sandbox")
    return "chromium --force-renderer-accessibility --no-first-run --no-sandbox"


def base_known_apps(ctx=None):
    return {
        "browser": browser_command(),
        "site_url": SITE_URL,
        "form": f"python3 {FIXTURES}/form_app.py",
    }


# --------------------------------------------------------------------------
# Task 1: organise a messy folder
# --------------------------------------------------------------------------

MESSY_FILES = {
    "photo.png": "Images", "pic.jpg": "Images",
    "notes.txt": "Documents", "report.pdf": "Documents", "readme.md": "Documents",
    "song.mp3": "Audio", "clip.mp4": "Video", "backup.zip": "Archives",
}


def _organise_setup(ctx):
    messy = ctx.dir / "messy"
    if messy.exists():
        shutil.rmtree(messy)
    messy.mkdir(parents=True)
    for name in MESSY_FILES:
        (messy / name).write_text(f"seed {name}\n")
    ctx.data["messy"] = str(messy)


def _organise_assert(ctx, _run):
    messy = Path(ctx.data["messy"])
    problems = []
    for name, folder in MESSY_FILES.items():
        if not (messy / folder / name).exists():
            problems.append(f"{name} not in {folder}/")
        if (messy / name).exists():
            problems.append(f"{name} still at top level")
    if problems:
        return False, "; ".join(problems)
    return True, "all 8 files in correct subfolders, none left loose"


# --------------------------------------------------------------------------
# Task 2: change a system setting through the Settings GUI
# (enable event sounds in xfce4-appearance-settings; read back via xfconf)
# --------------------------------------------------------------------------

SOUNDS_PROP = "/Net/EnableEventSounds"


def _sounds_get():
    res = subprocess.run(["xfconf-query", "-c", "xsettings", "-p", SOUNDS_PROP],
                         capture_output=True, text=True)
    return res.stdout.strip()


def _sounds_setup(ctx):
    subprocess.run(["xfconf-query", "-c", "xsettings", "-p", SOUNDS_PROP,
                    "--create", "-t", "bool", "-s", "false"],
                   capture_output=True, text=True)
    if _sounds_get() != "false":
        raise RuntimeError(f"could not initialise {SOUNDS_PROP} to false "
                           f"(reads {_sounds_get()!r})")


def _sounds_assert(ctx, _run):
    now = _sounds_get()
    if now == "true":
        return True, f"xfconf xsettings {SOUNDS_PROP} == true"
    return False, f"{SOUNDS_PROP} is {now!r}, expected 'true'"


# --------------------------------------------------------------------------
# Task 3: fill and submit the form fixture
# --------------------------------------------------------------------------

FORM_VALUES = {"name": "Ada Lovelace", "email": "ada@example.org",
               "city": "Pune"}


def _form_setup(ctx):
    result = ctx.dir / "result.json"
    if result.exists():
        result.unlink()
    ctx.data["result"] = str(result)
    ctx.data["apps"] = {
        "form": f"python3 {FIXTURES}/form_app.py {result}"}


def _form_assert(ctx, _run):
    path = Path(ctx.data["result"])
    if not path.exists():
        return False, "submission file was never written"
    got = json.loads(path.read_text())
    if got == FORM_VALUES:
        return True, f"submission matches: {got}"
    return False, f"submission {got} != expected {FORM_VALUES}"


# --------------------------------------------------------------------------
# Task 4: web fact extraction on the local test site
# --------------------------------------------------------------------------

def _web_assert(ctx, run):
    capital = (run.get("answers") or {}).get("capital", "")
    if capital == "Velport":
        return True, "extracted capital == 'Velport'"
    return False, f"extracted capital == {capital!r}, expected 'Velport'"


# --------------------------------------------------------------------------
# Task 5: spreadsheet from a PDF
# --------------------------------------------------------------------------

PDF_ROWS = [("Item", "Qty", "Price"), ("Apples", "12", "30"),
            ("Bread", "5", "25"), ("Milk", "8", "42"),
            ("Eggs", "30", "6"), ("Rice", "10", "55")]
SHEET_NAME = "deskdora-report.gnumeric"


def _sheet_setup(ctx):
    pdf = ctx.dir / "invoice.pdf"
    subprocess.run(["python3", str(FIXTURES / "make_pdf.py"), str(pdf)],
                   check=True, capture_output=True, text=True)
    for base in (ctx.dir, Path.home()):
        old = base / SHEET_NAME
        if old.exists():
            old.unlink()
    ctx.data["pdf"] = str(pdf)


def _sheet_assert(ctx, _run):
    found = None
    for base in (ctx.dir, Path.home(), Path.cwd()):
        cand = base / SHEET_NAME
        if cand.exists():
            found = cand
            break
    if not found:
        return False, f"{SHEET_NAME} not found in task dir, home, or cwd"
    out_csv = ctx.dir / "check.csv"
    res = subprocess.run(["ssconvert", str(found), str(out_csv)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        return False, f"ssconvert failed: {res.stderr.strip()}"
    text = out_csv.read_text()
    missing = [f"{a},{b},{c}" for a, b, c in PDF_ROWS
               if f"{a},{b},{c}" not in text]
    if missing:
        return False, f"sheet missing rows {missing}; content: {text!r}"
    return True, f"sheet at {found} contains all {len(PDF_ROWS)} expected rows"


# --------------------------------------------------------------------------

def gate_tasks():
    return [
        Task(id="organise-folder", title="Organise a messy folder by type",
             goal="", setup=_organise_setup, assertion=_organise_assert),
        Task(id="settings-sounds",
             title="Change a system setting in the Settings GUI",
             goal="Enable event sounds in Appearance settings",
             setup=_sounds_setup, assertion=_sounds_assert),
        Task(id="fill-form", title="Fill and submit a form",
             goal=("Fill the form: name=Ada Lovelace, "
                   "email=ada@example.org, city=Pune and submit"),
             setup=_form_setup, assertion=_form_assert),
        Task(id="web-fact", title="Find a fact on the local test site",
             goal="Open the article about Zorania and report its capital",
             setup=lambda ctx: None, assertion=_web_assert),
        Task(id="pdf-to-sheet", title="Build a spreadsheet from a PDF",
             goal="", setup=_sheet_setup, assertion=_sheet_assert),
    ]


def prepare_gate_goals(tasks):
    """Fill in goals that depend on setup-time values.  Call after setup."""
    for task in tasks:
        ctx = task_ctx(task)
        if task.id == "organise-folder":
            task.goal = f"Organise folder {ctx.data['messy']} by type"
        elif task.id == "pdf-to-sheet":
            task.goal = (f"Create a spreadsheet from {ctx.data['pdf']} "
                         f"saved as {SHEET_NAME}")
    return tasks


_CTX_CACHE = {}


def task_ctx(task):
    if task.id not in _CTX_CACHE:
        _CTX_CACHE[task.id] = Ctx(task.id)
    return _CTX_CACHE[task.id]
