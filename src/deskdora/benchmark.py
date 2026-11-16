"""The DeskDora benchmark: ~25 standard desktop tasks, defined as data
(setup + goal + end-state assertion), executed in the desktop rig by the
same runner discipline as the gate.  Results are published exactly as
measured — passes, failures, and the reasons — in BENCHMARK.md.

Run with:  deskdora bench [--planner builtin] [--limit N]
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from . import tasks as _t
from .cli import build_agent
from .tasks import FIXTURES, Ctx, Task

EVIDENCE = os.path.expanduser("~/workspace/deskdora-deliverables/evidence/benchmark")
APPS_TO_CLOSE = ["thunar", "gnumeric", "xfce4-appearance-settings",
                 "mousepad", "form_app.py", "xfce4-terminal"]


# --------------------------------------------------------------------------
# local setup/assert factories
# --------------------------------------------------------------------------

def _organise_variant(files_map):
    def setup(ctx):
        messy = ctx.dir / "messy"
        if messy.exists():
            import shutil
            shutil.rmtree(messy)
        messy.mkdir(parents=True)
        for name in files_map:
            (messy / name).write_text(f"seed {name}\n")
        ctx.data["messy"] = str(messy)
        ctx.data["map"] = files_map

    def assertion(ctx, _run):
        messy = Path(ctx.data["messy"])
        problems = []
        for name, folder in files_map.items():
            if not (messy / folder / name).exists():
                problems.append(f"{name} not in {folder}/")
            if (messy / name).exists():
                problems.append(f"{name} still loose")
        return (not problems), "; ".join(problems) or \
            f"all {len(files_map)} files organised"

    return setup, assertion


def _form_variant(values):
    def setup(ctx):
        result = ctx.dir / "result.json"
        if result.exists():
            result.unlink()
        ctx.data["result"] = str(result)
        ctx.data["apps"] = {
            "form": f"python3 {FIXTURES}/form_app.py {result}"}

    def assertion(ctx, _run):
        path = Path(ctx.data["result"])
        if not path.exists():
            return False, "submission file never written"
        got = json.loads(path.read_text())
        return got == values, f"submission {got} vs expected {values}"

    goal = ("Fill the form: " + ", ".join(f"{k}={v}" for k, v in values.items())
            + " and submit")
    return setup, assertion, goal


def _sounds_variant(prop, initial):
    def setup(ctx):
        subprocess.run(["xfconf-query", "-c", "xsettings", "-p", prop,
                        "--create", "-t", "bool", "-s", initial],
                       capture_output=True, text=True)
        ctx.data["prop"] = prop

    def assertion(ctx, _run):
        res = subprocess.run(["xfconf-query", "-c", "xsettings", "-p", prop],
                             capture_output=True, text=True)
        want = "false" if initial == "true" else "true"
        now = res.stdout.strip()
        return now == want, f"{prop} == {now!r} (expected {want!r})"

    return setup, assertion


def _web_variant(thing, expected):
    def assertion(ctx, run):
        got = (run.get("answers") or {}).get(thing, "")
        return got == expected, f"extracted {thing} == {got!r}, want {expected!r}"
    return assertion


def _sheet_variant(rows, save_name):
    def setup(ctx):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "make_pdf", str(FIXTURES / "make_pdf.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        pdf = ctx.dir / "data.pdf"
        mod.build_pdf(str(pdf), rows)
        for base in (ctx.dir, Path.home()):
            old = base / save_name
            if old.exists():
                old.unlink()
        ctx.data.update({"pdf": str(pdf), "save_name": save_name,
                         "rows": rows})

    def assertion(ctx, _run):
        found = None
        for base in (ctx.dir, Path.home(), Path.cwd()):
            cand = base / save_name
            if cand.exists():
                found = cand
                break
        if not found:
            return False, f"{save_name} not found"
        out_csv = ctx.dir / "check.csv"
        res = subprocess.run(["ssconvert", str(found), str(out_csv)],
                             capture_output=True, text=True)
        if res.returncode != 0:
            return False, f"ssconvert failed: {res.stderr.strip()}"
        text = out_csv.read_text()
        missing = [",".join(r) for r in rows if ",".join(r) not in text]
        return (not missing), (f"missing rows {missing}" if missing
                               else f"all {len(rows)} rows present")

    return setup, assertion


def _editor_assert(needle):
    def assertion(ctx, _run):
        from . import perception
        obs = perception.observe()
        joined = "\n".join(obs.texts.values())
        names = " ".join(e.name for e in obs.elements)
        ok = needle in joined or needle in names
        return ok, f"typed text {'found' if ok else 'NOT found'} in editor"
    return assertion


def _window_assert(fragment):
    def assertion(ctx, _run):
        from . import perception
        obs = perception.observe()
        ok = any(fragment.lower() in w.lower() for w in obs.windows)
        return ok, f"window containing {fragment!r}: {'open' if ok else 'absent'}"
    return assertion


def _file_assert(relpath, content):
    def assertion(ctx, _run):
        path = ctx.dir / relpath
        if not path.exists():
            return False, f"{relpath} was not created"
        got = path.read_text().strip()
        return got == content, f"{relpath} contains {got!r}"
    return assertion


# --------------------------------------------------------------------------
# the suite
# --------------------------------------------------------------------------

def benchmark_tasks():
    tasks = []
    add = tasks.append

    # The five gate tasks, re-run as benchmark entries.
    add(Task("b01-organise-basic", "Organise folder (8 files)",
             "", _t._organise_setup, _t._organise_assert))
    add(Task("b02-sounds-enable", "Enable event sounds in Settings",
             "Enable event sounds in Appearance settings",
             _t._sounds_setup, _t._sounds_assert))
    fs, fa = _t._form_setup, _t._form_assert
    add(Task("b03-form-basic", "Fill and submit form (Ada)",
             "Fill the form: name=Ada Lovelace, email=ada@example.org, "
             "city=Pune and submit", fs, fa))
    add(Task("b04-web-capital", "Web: Zorania capital",
             "Open the article about Zorania and report its capital",
             lambda ctx: None, _t._web_assert))
    add(Task("b05-pdf-sheet", "Spreadsheet from PDF (invoice)",
             "", _t._sheet_setup, _t._sheet_assert))

    # Organise variants.
    s, a = _organise_variant({"a.png": "Images", "b.jpg": "Images",
                              "c.gif": "Images", "d.webp": "Images",
                              "e.bmp": "Images"})
    add(Task("b06-organise-photos", "Organise folder (photos only)", "", s, a))
    many = {f"file{i:02d}.{ext}": folder for i, (ext, folder) in enumerate([
        ("png", "Images"), ("jpg", "Images"), ("txt", "Documents"),
        ("pdf", "Documents"), ("md", "Documents"), ("mp3", "Audio"),
        ("wav", "Audio"), ("mp4", "Video"), ("mkv", "Video"),
        ("zip", "Archives"), ("gz", "Archives"), ("txt", "Documents"),
        ("png", "Images"), ("mp3", "Audio"), ("pdf", "Documents"),
        ("zip", "Archives")], start=1)}
    s, a = _organise_variant(many)
    add(Task("b07-organise-many", "Organise folder (16 files)", "", s, a))

    # Form variants.
    s, a, g = _form_variant({"name": "Grace Hopper",
                             "email": "grace@example.net", "city": "New York"})
    add(Task("b08-form-grace", "Fill and submit form (Grace)", g, s, a))
    s, a, g = _form_variant({"name": "Alan Turing",
                             "email": "alan@example.org", "city": "London"})
    add(Task("b09-form-alan", "Fill and submit form (Alan)", g, s, a))

    # Web variants.
    add(Task("b10-web-belmora", "Web: Belmora capital",
             "Open the article about Belmora and report its capital",
             lambda ctx: None, _web_variant("capital", "San Corvo")))
    add(Task("b11-web-kestrel", "Web: Kestrel Isle capital",
             "Open the article about Kestrel Isle and report its capital",
             lambda ctx: None, _web_variant("capital", "Northhaven")))
    add(Task("b12-web-river", "Web: Zorania river",
             "Open the article about Zorania and report its river",
             lambda ctx: None, _web_variant("river", "Saffron River")))

    # Settings variants.
    s, a = _sounds_variant("/Net/EnableEventSounds", "true")
    add(Task("b13-sounds-disable", "Disable event sounds in Settings",
             "Disable event sounds in Appearance settings", s, a))
    s, a = _sounds_variant("/Net/EnableInputFeedbackSounds", "false")
    add(Task("b14-feedback-enable", "Enable input feedback sounds",
             "Enable input feedback sounds in Appearance settings", s, a))

    # Spreadsheet variant.
    rows = [("Item", "Qty", "Price"), ("Pens", "20", "15"),
            ("Books", "7", "120"), ("Bags", "3", "250")]
    s, a = _sheet_variant(rows, "deskdora-bench-sheet.gnumeric")
    add(Task("b15-sheet-stationery", "Spreadsheet from PDF (stationery)",
             "", s, a))

    # Editor tasks.
    add(Task("b16-editor-type-1", "Type text in the editor",
             "Open text editor then type DeskDora benchmark one",
             lambda ctx: None, _editor_assert("DeskDora benchmark one")))
    add(Task("b17-editor-type-2", "Type text in the editor (2)",
             "Open text editor then type The quick brown fox 12345",
             lambda ctx: None, _editor_assert("The quick brown fox 12345")))

    # Terminal tasks (goals filled after setup).
    add(Task("b18-terminal-echo", "Terminal: echo to a file", "",
             lambda ctx: None, _file_assert("out.txt", "deskdora-bench")))
    add(Task("b19-terminal-mkdir", "Terminal: mkdir and write", "",
             lambda ctx: None, _file_assert("made/ok.txt", "ok")))

    # Open-an-app tasks.
    def _app_assert(app_name):
        def assertion(ctx, _run):
            from . import perception
            obs = perception.observe()
            ok = any(e.app.lower() == app_name for e in obs.elements) or \
                any(app_name in w.lower() for w in obs.windows)
            return ok, f"app {app_name}: {'running' if ok else 'not found'}"
        return assertion

    add(Task("b20-open-terminal", "Open the terminal",
             "Open terminal", lambda ctx: None, _app_assert("xfce4-terminal")))
    add(Task("b21-open-spreadsheet", "Open the spreadsheet app",
             "Open spreadsheet", lambda ctx: None, _app_assert("gnumeric")))

    # Honest-failure probes: goals outside the built-in grammar / facts
    # that do not exist.  These are expected to fail; the benchmark
    # records what actually happens either way.
    add(Task("b22-unknown-goal", "Unsupported goal (defragment)",
             "Defragment the hard drive and optimise the system registry",
             lambda ctx: None,
             lambda ctx, run: (False, "no end state — unsupported goal")))
    add(Task("b23-web-missing", "Web: article that does not exist",
             "Open the article about Atlantis and report its capital",
             lambda ctx: None, _web_variant("capital", "—")))

    # Messy real-world case: a subfolder already exists before organising.
    def _pre_setup(ctx):
        messy = ctx.dir / "messy"
        if messy.exists():
            import shutil
            shutil.rmtree(messy)
        (messy / "Images").mkdir(parents=True)
        (messy / "Images" / "old.png").write_text("old\n")
        files = {"new.png": "Images", "doc.txt": "Documents",
                 "song.mp3": "Audio"}
        for name in files:
            (messy / name).write_text(f"seed {name}\n")
        ctx.data["messy"] = str(messy)
        ctx.data["map"] = files

    def _pre_assert(ctx, _run):
        messy = Path(ctx.data["messy"])
        problems = [f"{n} not in {f}/" for n, f in ctx.data["map"].items()
                    if not (messy / f / n).exists()]
        return (not problems), "; ".join(problems) or "organised despite pre-existing folder"

    add(Task("b24-organise-preexisting", "Organise with pre-existing subfolder",
             "", _pre_setup, _pre_assert))

    # Second sheet name variant.
    s, a = _sheet_variant(_t.PDF_ROWS, "deskdora-bench-invoice.gnumeric")
    add(Task("b25-sheet-renamed", "Spreadsheet from PDF (other filename)",
             "", s, a))
    return tasks


def goal_for(task, ctx):
    if task.goal:
        return task.goal
    if "organise" in task.id:
        return f"Organise folder {ctx.data['messy']} by type"
    if "pdf-sheet" in task.id or "sheet" in task.id:
        pdf = ctx.data.get("pdf") or _t.task_ctx(
            _t.gate_tasks()[4]).data.get("pdf")
        save = ctx.data.get("save_name", _t.SHEET_NAME)
        return f"Create a spreadsheet from {pdf} saved as {save}"
    if task.id == "b18-terminal-echo":
        return (f"Open terminal then type echo deskdora-bench > "
                f"{ctx.dir}/out.txt then press Return")
    if task.id == "b19-terminal-mkdir":
        return (f"Open terminal then type mkdir -p {ctx.dir}/made && "
                f"echo ok > {ctx.dir}/made/ok.txt then press Return")
    return task.goal


def _clean_desktop():
    for app in APPS_TO_CLOSE:
        subprocess.run(["pkill", "-f", app], capture_output=True)
    subprocess.run(["pkill", "-f", "chrome-linux64"], capture_output=True)
    time.sleep(1.0)


def _ensure_site():
    import urllib.request
    from .tasks import SITE_PORT, FIXTURES
    url = f"http://127.0.0.1:{SITE_PORT}/index.html"
    try:
        urllib.request.urlopen(url, timeout=2)
        return
    except Exception:
        pass
    subprocess.Popen(
        ["python3", "-m", "http.server", str(SITE_PORT),
         "--directory", str(FIXTURES / "site")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    time.sleep(1.5)


def run_benchmark(planner_mode="builtin", model="qwen2.5:1.5b", limit=None,
                  out_dir=None):
    out_dir = out_dir or EVIDENCE
    os.makedirs(out_dir, exist_ok=True)
    _ensure_site()
    tasks = benchmark_tasks()
    if limit:
        tasks = tasks[:limit]
    results_path = os.path.join(out_dir, "benchmark-results.json")
    results = []
    for task in tasks:
        ctx = Ctx(f"bench-{task.id}")
        print(f"\n=== {task.id}: {task.title} ===", flush=True)
        _clean_desktop()
        try:
            task.setup(ctx)
        except Exception as exc:
            results.append({"id": task.id, "title": task.title,
                            "verdict": "FAIL", "stage": "setup",
                            "detail": str(exc)})
            _save(results, results_path)
            continue
        goal = goal_for(task, ctx)
        known = dict(ctx.data.get("apps", {}))
        agent = build_agent(goal, planner_mode=planner_mode, model=model,
                            auto_approve=True,
                            runs_dir=os.path.join(out_dir, "runs"),
                            known_apps=known)
        os.chdir(ctx.dir)
        started = time.time()
        run = agent.run(goal)
        elapsed = round(time.time() - started, 1)
        try:
            ok, detail = task.assertion(ctx, run)
        except Exception as exc:
            ok, detail = False, f"assertion error: {exc}"
        verdict = "PASS" if (ok and run["status"] == "success") else "FAIL"
        print(f"{verdict} status={run['status']} steps={run.get('steps_executed', 0)} "
              f"({elapsed}s): {detail}", flush=True)
        results.append({
            "id": task.id, "title": task.title, "goal": goal,
            "verdict": verdict, "run_status": run["status"],
            "run_reason": run.get("reason", ""), "answers": run.get("answers"),
            "steps": run.get("steps_executed", 0), "seconds": elapsed,
            "assertion_detail": detail, "log_dir": agent.log.dir,
            "planner_mode": agent.planner_mode,
        })
        _save(results, results_path)
        _clean_desktop()
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\nBENCHMARK: {passed}/{len(results)} passed -> {results_path}")
    write_benchmark_md(results, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "BENCHMARK.md"))
    return 0 if passed else 1


def _save(results, path):
    with open(path, "w") as fh:
        json.dump(results, fh, indent=2, default=str)


def write_benchmark_md(results, path):
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    lines = [
        "# DeskDora benchmark results",
        "",
        "Standard desktop tasks executed by the agent in a virtualised",
        "Ubuntu/XFCE desktop (Xorg session), each verified by an external",
        "end-state assertion — never by the agent's own claim.",
        "",
        f"**Score: {passed}/{len(results)} tasks passed.**",
        "",
        "| # | Task | Verdict | Run status | Steps | Time | Evidence |",
        "|---|------|---------|------------|-------|------|----------|",
    ]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | {r['title']} | {r['verdict']} | {r.get('run_status', '—')} "
            f"| {r.get('steps', '—')} | {r.get('seconds', '—')}s "
            f"| {r.get('assertion_detail', '')} |")
    lines += [
        "",
        "Notes:",
        "- Planner: deterministic built-in planner (see README for the",
        "  supported goal grammar). Confirmation gates were exercised",
        "  with an explicitly disclosed auto-approve policy, as is",
        "  standard for unattended benchmark runs; interactive runs ask",
        "  a human instead.",
        "- Tasks b22/b23 are deliberate probes outside the supported",
        "  grammar / with a non-existent target; they are expected to",
        "  fail, and the agent is expected to fail honestly (stop and",
        "  report) rather than improvise.",
    ]
    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    return path
