from deskdora.safety import (AllowList, ConfirmationGate, KillSwitch,
                             approve_all, classify_step, deny_all)


def test_classify_clicks():
    assert classify_step({"action": "click",
                          "target": {"name_contains": "Delete"}}) == "destructive"
    assert classify_step({"action": "click",
                          "target": {"name_contains": "Submit"}}) == "destructive"
    assert classify_step({"action": "click",
                          "target": {"name_contains": "Save"}}) == "safe"
    assert classify_step({"action": "type", "text": "hello"}) == "safe"
    assert classify_step({"action": "key", "key": "Delete",
                          "context": "files"}) == "destructive"
    assert classify_step({"action": "key", "key": "Delete"}) == "safe"


def test_gate_blocks_when_denied():
    gate = ConfirmationGate(deny_all)
    step = {"action": "click", "target": {"name_contains": "Delete"}}
    assert gate.check(step, "delete file") is False
    assert gate.history and gate.history[0]["approved"] is False


def test_gate_approves_and_skips_safe_steps():
    calls = []

    def policy(step, desc):
        calls.append(step)
        return True

    gate = ConfirmationGate(policy)
    assert gate.check({"action": "type", "text": "x"}) is True
    assert calls == []  # safe steps never consult the policy
    assert gate.check({"action": "click",
                       "target": {"name_contains": "Install"}}) is True
    assert len(calls) == 1


def test_gate_approve_all_records_history():
    gate = ConfirmationGate(approve_all)
    # "delete_files" is not an executable action (vibe V2); use a
    # real destructive step to prove the policy is consulted.
    assert gate.check({"action": "click",
                       "target": {"name_contains": "Delete"}}) is True
    assert gate.history[0]["approved"] is True


def test_kill_switch_file(tmp_path):
    stop = tmp_path / "STOP"
    kill = KillSwitch(str(stop))
    assert kill.is_tripped() is False
    kill.trip()
    assert stop.exists() and kill.is_tripped() is True
    kill.reset()
    assert kill.is_tripped() is False


def test_allow_list():
    assert AllowList().permits("anything --flag") is True
    allow = AllowList(["thunar", "gnumeric"])
    assert allow.permits("thunar /home/user") is True
    assert allow.permits("/usr/bin/gnumeric") is True
    assert allow.permits("rm -rf /") is False
