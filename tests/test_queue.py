"""Task queue (v3.2): multiple works, executed in order.

The queue is exercised against a fake runner — no desktop — that
records the order runs start in and finishes them on demand.  The
contract under test: strict FIFO execution, one run at a time,
pause/resume, reorder and removal of queued items, the kill switch
halting the queue, and persistence across a restart.
"""
import threading
import time

import pytest

from deskdora.queue import BusyError, TaskQueue


class FakeRunner:
    def __init__(self):
        self.started = []            # goals, in start order
        self._busy = False
        self._killed = False
        self._release = threading.Event()
        self._release.set()
        self.result = {"status": "success"}
        self.hold = False            # join() blocks until released

    def busy(self):
        return self._busy

    def start(self, goal, planner):
        if self._busy:
            raise BusyError("busy")
        self._busy = True
        self.started.append((goal, planner))
        if self.hold:
            self._release.clear()

    def join(self):
        self._release.wait(timeout=10)
        self._busy = False
        return dict(self.result)

    def kill_tripped(self):
        return self._killed

    def release(self):
        self._release.set()


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def queue(runner):
    q = TaskQueue(runner)
    q.start()
    yield q
    q.stop()


def test_fifo_order_and_one_at_a_time(queue, runner):
    runner.hold = True
    queue.add("first")
    queue.add("second", planner="ollama")
    queue.add("third")
    assert wait_for(lambda: len(runner.started) == 1)
    assert runner.started[0][0] == "first"
    # the second item must not start while the first is held
    time.sleep(0.3)
    assert len(runner.started) == 1
    runner.release()
    assert wait_for(lambda: len(runner.started) == 2)
    assert runner.started[1] == ("second", "ollama")
    runner.hold = False
    runner.release()
    assert wait_for(lambda: queue.snapshot()["queued"] == 0
                    and not queue.snapshot()["running_id"])
    statuses = [it["status"] for it in queue.snapshot()["items"]]
    assert statuses == ["success", "success", "success"]


def test_pause_blocks_and_resume_continues(queue, runner):
    queue.pause()
    queue.add("held task")
    time.sleep(0.4)
    assert runner.started == []
    queue.resume()
    assert wait_for(lambda: len(runner.started) == 1)


def test_move_reorders_queued_items(queue, runner):
    queue.pause()
    a = queue.add("alpha")
    b = queue.add("beta")
    c = queue.add("gamma")
    assert queue.move(c["id"], "up") is True
    goals = [it["goal"] for it in queue.snapshot()["items"]]
    assert goals == ["alpha", "gamma", "beta"]
    assert queue.move(a["id"], "up") is False     # already first
    assert queue.move(b["id"], "down") is False   # already last
    with pytest.raises(ValueError):
        queue.move(a["id"], "sideways")


def test_remove_and_clear(queue, runner):
    queue.pause()
    a = queue.add("one")
    queue.add("two")
    assert queue.remove(a["id"]) is True
    assert queue.remove(a["id"]) is False
    assert [it["goal"] for it in queue.snapshot()["items"]] == ["two"]
    assert queue.clear() == 1
    assert queue.snapshot()["items"] == []


def test_kill_halts_queue_until_resumed(queue, runner):
    runner.hold = True
    queue.add("doomed")
    queue.add("waiting")
    assert wait_for(lambda: len(runner.started) == 1)
    runner._killed = True
    runner.result = {"status": "killed"}
    runner.release()
    assert wait_for(lambda: queue.snapshot()["halted"] is True)
    time.sleep(0.4)
    assert len(runner.started) == 1          # second item never started
    runner._killed = False
    runner.result = {"status": "success"}
    runner.hold = False
    queue.resume()
    assert wait_for(lambda: len(runner.started) == 2)


def test_busy_desktop_defers_item(queue, runner):
    runner._busy = True                       # a manual run owns it
    queue.add("patient")
    time.sleep(0.4)
    assert runner.started == []
    runner._busy = False
    assert wait_for(lambda: len(runner.started) == 1)


def test_persistence_roundtrip(tmp_path, runner):
    path = str(tmp_path / "queue.json")
    q = TaskQueue(runner, path)
    q.pause()
    q.add("keep me", planner="builtin")
    q2 = TaskQueue(runner, path)
    snap = q2.snapshot()
    assert snap["paused"] is True
    assert [it["goal"] for it in snap["items"]] == ["keep me"]
    assert snap["items"][0]["planner"] == "builtin"


def test_running_item_comes_back_interrupted(tmp_path, runner):
    import json
    path = tmp_path / "queue.json"
    path.write_text(json.dumps({"paused": False, "items": [{
        "id": "abc", "goal": "was running", "planner": "",
        "status": "running", "queued_at": 1.0, "started_at": 2.0,
        "finished_at": None, "result_status": None}]}))
    q = TaskQueue(runner, str(path))
    assert q.snapshot()["items"][0]["status"] == "interrupted"


def test_empty_goal_rejected(queue):
    with pytest.raises(ValueError):
        queue.add("   ")
