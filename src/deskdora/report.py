"""Run report export (v3.3): any History run as one self-contained
HTML file — goal, planner/model, source, every step with its
screenshot, the result, and timings.  No external references, so
it views offline forever.  PDF is the browser's print path
(Print → Save as PDF); there is deliberately no server-side PDF
renderer."""
from __future__ import annotations

import base64
import html
import os
import time

from .agent import describe_step
from .audit import read_log
from .planners import PROVIDER_REGISTRY


def _planner_display(mode):
    if not mode:
        return "—"
    if mode == "builtin":
        return "Built-in"
    if mode.startswith("ollama:"):
        return "Ollama · " + mode.split(":", 1)[1]
    if ":" in mode:
        pid, model = mode.split(":", 1)
        label = PROVIDER_REGISTRY.get(pid, {}).get("label", pid)
        return f"{label} · {model}"
    return PROVIDER_REGISTRY.get(mode, {}).get("label", mode)


def _img_tag(run_dir, filename):
    if not filename:
        return ""
    path = os.path.join(run_dir, os.path.basename(filename))
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode()
    except Exception:
        return ""
    return (f'<img alt="step screenshot" '
            f'src="data:image/png;base64,{data}">')


def _fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def render_report(run_dir):
    """The full HTML report for one run directory."""
    events = read_log(os.path.join(run_dir, "log.jsonl"))
    start = next((e for e in events if e["kind"] == "run_start"), {})
    end = next((e for e in reversed(events)
                if e["kind"] == "run_end"), {})
    plan_event = next((e for e in events if e["kind"] == "plan"), {})
    plan = plan_event.get("steps") or []
    results = {e["index"]: e for e in events
               if e["kind"] == "step_result"}
    starts = {e["index"]: e for e in events
              if e["kind"] == "step_start"}
    file_ops = [e for e in events if e["kind"] == "file_op"]
    undos = [e for e in events if e["kind"] == "undo"]
    run_id = os.path.basename(run_dir.rstrip("/"))
    t0 = start.get("ts") or (events[0]["ts"] if events else 0)
    t1 = end.get("ts") or (events[-1]["ts"] if events else t0)
    esc = html.escape

    rows = []
    for i, step in enumerate(plan):
        res = results.get(i)
        st = starts.get(i)
        if res is None:
            status = "not run"
            detail = ""
            dur = ""
        else:
            if res.get("skipped"):
                status = "skipped by user"
            elif res.get("ok"):
                status = "recovered" if res.get("recovered") else "ok"
            else:
                status = "FAILED"
            detail = res.get("detail") or res.get("error") or ""
            dur = (f"{res['ts'] - st['ts']:.1f}s"
                   if st and res.get("ts") and st.get("ts") else "")
        rows.append(
            f"<tr><td>{i + 1}</td><td>{esc(describe_step(step))}</td>"
            f"<td class='st-{esc(status.split()[0])}'>{esc(status)}"
            f"</td><td>{esc(detail)}</td><td>{esc(dur)}</td>"
            f"<td class='shot'>{_img_tag(run_dir, (res or {}).get('screenshot'))}</td></tr>")

    ops_html = ""
    if file_ops:
        items = "".join(
            f"<li>{esc(op.get('op', 'move'))}: {esc(op.get('src') or '')}"
            f" → {esc(op.get('dst') or '')}</li>" for op in file_ops)
        ops_html = f"<h2>File changes</h2><ul>{items}</ul>"
    undo_html = ""
    if undos:
        last = undos[-1]
        undo_html = (f"<p>Undo was run on this record: "
                     f"{len(last.get('undone') or [])} reversed, "
                     f"{len(last.get('conflicts') or [])} conflicts, "
                     f"{len(last.get('missing') or [])} missing.</p>")

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>DeskDora run report — {esc(run_id)}</title>
<style>
body{{font-family:Georgia,serif;background:#0a0a0a;color:#e8e8e8;
margin:40px auto;max-width:1000px;padding:0 20px;line-height:1.5}}
h1{{font-family:Helvetica,Arial,sans-serif;letter-spacing:.02em}}
h2{{font-family:Helvetica,Arial,sans-serif;margin-top:32px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
td,th{{border-bottom:1px solid #333;padding:6px 8px;vertical-align:top;
text-align:left}}
.meta{{color:#9a9a9a}}
.st-ok{{color:#b7d94c}} .st-FAILED{{color:#ff7a7a}}
.st-skipped{{color:#e8c96a}} .st-recovered{{color:#7ac7ff}}
.shot img{{max-width:300px;border:1px solid #333}}
button{{background:#b7d94c;border:0;padding:8px 16px;font-weight:bold;
cursor:pointer}}
@media print{{button{{display:none}} body{{background:#fff;color:#000}}
td,th{{border-color:#bbb}}}}
</style></head><body>
<button onclick="window.print()">Print / Save as PDF</button>
<h1>DeskDora run report</h1>
<p class="meta">{esc(run_id)}</p>
<table>
<tr><th>Goal</th><td>{esc(start.get('goal', ''))}</td></tr>
<tr><th>Planner</th><td>{esc(_planner_display(start.get('planner_mode', '')))}</td></tr>
<tr><th>Started via</th><td>{esc(start.get('source', 'ui'))}</td></tr>
<tr><th>Started</th><td>{esc(_fmt_ts(t0)) if t0 else '—'}</td></tr>
<tr><th>Duration</th><td>{(t1 - t0):.1f}s</td></tr>
<tr><th>Result</th><td>{esc(end.get('status', 'unknown'))}</td></tr>
<tr><th>Answer / reason</th><td>{esc(end.get('answer') or end.get('reason') or '')}</td></tr>
<tr><th>Steps executed</th><td>{esc(str(end.get('steps_executed', '')))}</td></tr>
</table>
<h2>Steps</h2>
<table>
<tr><th>#</th><th>Step</th><th>Status</th><th>Detail</th><th>Time</th><th>Screenshot</th></tr>
{''.join(rows)}
</table>
{ops_html}
{undo_html}
<p class="meta">Generated by DeskDora from the run's local audit
log. Self-contained: viewable offline.</p>
</body></html>"""
