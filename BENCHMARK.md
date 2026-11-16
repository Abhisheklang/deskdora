# DeskDora benchmark results

Standard desktop tasks executed by the agent in a virtualised
Ubuntu/XFCE desktop (Xorg session), each verified by an external
end-state assertion — never by the agent's own claim.

**Score: 20/25 tasks passed.**

| # | Task | Verdict | Run status | Steps | Time | Evidence |
|---|------|---------|------------|-------|------|----------|
| 1 | Organise folder (8 files) | PASS | success | 139 | 110.9s | all 8 files in correct subfolders, none left loose |
| 2 | Enable event sounds in Settings | PASS | success | 7 | 10.6s | xfconf xsettings /Net/EnableEventSounds == true |
| 3 | Fill and submit form (Ada) | PASS | success | 10 | 14.3s | submission matches: {'name': 'Ada Lovelace', 'email': 'ada@example.org', 'city': 'Pune'} |
| 4 | Web: Zorania capital | PASS | success | 5 | 11.8s | extracted capital == 'Velport' |
| 5 | Spreadsheet from PDF (invoice) | PASS | success | 31 | 25.2s | sheet at /home/hatch/deskdora-tasks/bench-b05-pdf-sheet/deskdora-report.gnumeric contains all 6 expected rows |
| 6 | Organise folder (photos only) | PASS | success | 77 | 61.2s | all 5 files organised |
| 7 | Organise folder (16 files) | PASS | success | 181 | 143.1s | all 16 files organised |
| 8 | Fill and submit form (Grace) | PASS | success | 10 | 15.0s | submission {'name': 'Grace Hopper', 'email': 'grace@example.net', 'city': 'New York'} vs expected {'name': 'Grace Hopper', 'email': 'grace@example.net', 'city': 'New York'} |
| 9 | Fill and submit form (Alan) | PASS | success | 10 | 14.9s | submission {'name': 'Alan Turing', 'email': 'alan@example.org', 'city': 'London'} vs expected {'name': 'Alan Turing', 'email': 'alan@example.org', 'city': 'London'} |
| 10 | Web: Belmora capital | PASS | success | 5 | 12.2s | extracted capital == 'San Corvo', want 'San Corvo' |
| 11 | Web: Kestrel Isle capital | PASS | success | 5 | 12.4s | extracted capital == 'Northhaven', want 'Northhaven' |
| 12 | Web: Zorania river | PASS | success | 5 | 12.3s | extracted river == 'Saffron River', want 'Saffron River' |
| 13 | Disable event sounds in Settings | PASS | success | 7 | 11.0s | /Net/EnableEventSounds == 'false' (expected 'false') |
| 14 | Enable input feedback sounds | FAIL | success | 7 | 11.0s | /Net/EnableInputFeedbackSounds == 'false' (expected 'true') |
| 15 | Spreadsheet from PDF (stationery) | PASS | success | 25 | 25.0s | all 4 rows present |
| 16 | Type text in the editor | FAIL | success | 3 | 4.4s | typed text NOT found in editor |
| 17 | Type text in the editor (2) | PASS | success | 3 | 4.5s | typed text found in editor |
| 18 | Terminal: echo to a file | PASS | success | 4 | 5.4s | out.txt contains 'deskdora-bench' |
| 19 | Terminal: mkdir and write | PASS | success | 4 | 5.8s | made/ok.txt contains 'ok' |
| 20 | Open the terminal | PASS | success | 2 | 3.7s | app xfce4-terminal: running |
| 21 | Open the spreadsheet app | PASS | success | 2 | 3.7s | app gnumeric: running |
| 22 | Unsupported goal (defragment) | FAIL | failed | 0 | 0.6s | no end state — unsupported goal |
| 23 | Web: article that does not exist | FAIL | failed | 2 | 7.5s | extracted capital == '', want '—' |
| 24 | Organise with pre-existing subfolder | FAIL | success | 59 | 48.6s | doc.txt not in Documents/; song.mp3 not in Audio/ |
| 25 | Spreadsheet from PDF (other filename) | PASS | success | 31 | 25.8s | all 6 rows present |

Notes:
- Planner: deterministic built-in planner (see README for the
  supported goal grammar). Confirmation gates were exercised
  with an explicitly disclosed auto-approve policy, as is
  standard for unattended benchmark runs; interactive runs ask
  a human instead.
- Tasks b22/b23 are deliberate probes outside the supported
  grammar / with a non-existent target; they are expected to
  fail, and the agent is expected to fail honestly (stop and
  report) rather than improvise.
