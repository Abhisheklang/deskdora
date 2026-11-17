"""Undo for file actions (v3.3): the journal restores a run's
moves exactly; conflicts and missing files are reported, never
silently overwritten."""
import hashlib
import json
import os

from deskdora import filejournal


def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def make_run(tmp_path, moves):
    """Fabricate a run dir whose log journals `moves` — a list of
    (src, dst) with the files already sitting at dst, as a real
    organise run would leave them."""
    run_dir = tmp_path / "run-x"
    run_dir.mkdir()
    events = [{"kind": "run_start", "goal": "organise", "ts": 1.0}]
    for src, dst in moves:
        events.append({"kind": "file_op", "op": "move", "src": src,
                       "dst": dst, "sha256": _sha(dst), "ts": 2.0})
    events.append({"kind": "run_end", "status": "success", "ts": 3.0})
    (run_dir / "log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events))
    return str(run_dir)


def test_undo_restores_exact_state(tmp_path):
    messy = tmp_path / "messy"
    a_src, b_src = str(messy / "a.txt"), str(messy / "b.txt")
    a_dst = str(messy / "Text" / "a.txt")
    b_dst = str(messy / "Text" / "b.txt")
    _write(a_dst, b"alpha")
    _write(b_dst, b"beta")
    run_dir = make_run(tmp_path, [(a_src, a_dst), (b_src, b_dst)])
    report = filejournal.undo_run(run_dir)
    assert report["total"] == 2
    assert len(report["undone"]) == 2
    assert report["conflicts"] == [] and report["missing"] == []
    assert open(a_src, "rb").read() == b"alpha"
    assert open(b_src, "rb").read() == b"beta"
    assert not os.path.exists(a_dst)


def test_undo_reports_reoccupied_source(tmp_path):
    messy = tmp_path / "messy"
    src, dst = str(messy / "a.txt"), str(messy / "Text" / "a.txt")
    _write(dst, b"moved")
    _write(src, b"someone else's new file")
    run_dir = make_run(tmp_path, [(src, dst)])
    report = filejournal.undo_run(run_dir)
    assert report["undone"] == []
    assert len(report["conflicts"]) == 1
    # nothing was overwritten, either way
    assert open(src, "rb").read() == b"someone else's new file"
    assert open(dst, "rb").read() == b"moved"


def test_undo_reports_changed_file(tmp_path):
    messy = tmp_path / "messy"
    src, dst = str(messy / "a.txt"), str(messy / "Text" / "a.txt")
    _write(dst, b"original")
    run_dir = make_run(tmp_path, [(src, dst)])
    _write(dst, b"edited after the run")
    report = filejournal.undo_run(run_dir)
    assert report["undone"] == []
    assert "changed since the run" in report["conflicts"][0]["reason"]


def test_undo_reports_missing_file(tmp_path):
    messy = tmp_path / "messy"
    src, dst = str(messy / "a.txt"), str(messy / "Text" / "a.txt")
    _write(dst, b"gone soon")
    run_dir = make_run(tmp_path, [(src, dst)])
    os.unlink(dst)
    report = filejournal.undo_run(run_dir)
    assert report["missing"] and report["undone"] == []


def test_journal_reads_only_file_ops(tmp_path):
    run_dir = make_run(tmp_path, [])
    assert filejournal.journal_for_run(run_dir) == []
