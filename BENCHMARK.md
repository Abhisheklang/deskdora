# DeskDora benchmark results

Standard desktop tasks executed by the agent in a virtualised
Ubuntu/XFCE desktop (Xorg session), each verified by an external
end-state assertion — never by the agent's own claim.

**Score: 20/25 tasks passed.** (v3.0.0, commit `d6255aa`;
v1.0 also scored 20/25 with identical per-task verdicts. The
first v3.0.0 run scored 18/25 — two spreadsheet tasks flipped
because the save macro *assumed* a save that never happened;
the fix and the confirmation re-runs are documented in the
release FINAL-REPORT.md.)

| # | Task | Verdict | Run status | Steps | Time | Evidence |
|---|------|---------|------------|-------|------|----------|
| 1 | Organise folder (8 files) | PASS | success | 139 | 125.7s | all 8 files in correct subfolders, none left loose |
| 2 | Enable event sounds in Settings | PASS | success | 7 | 9.5s | xfconf xsettings /Net/EnableEventSounds == true |
| 3 | Fill and submit form (Ada) | PASS | success | 10 | 13.0s | submission matches: {'name': 'Ada Lovelace', 'email': 'ada@example.org', 'city': 'Pune'} |
| 4 | Web: Zorania capital | PASS | success | 5 | 11.0s | extracted capital == 'Velport' |
| 5 | Spreadsheet from PDF (invoice) | PASS | success | 31 | 26.1s | sheet at ~/deskdora-tasks/bench-b05-pdf-sheet/deskdora-report.gnumeric contains all 6 expected rows |
| 6 | Organise folder (photos only) | PASS | success | 77 | 69.8s | all 5 files organised |
| 7 | Organise folder (16 files) | PASS | success | 181 | 163.1s | all 16 files organised |
| 8 | Fill and submit form (Grace) | PASS | success | 10 | 13.0s | submission {'name': 'Grace Hopper', 'email': 'grace@example.net', 'city': 'New York'} vs expected {'name': 'Grace Hopper', 'email': 'grace@example.net', 'city': 'New York'} |
| 9 | Fill and submit form (Alan) | PASS | success | 10 | 13.1s | submission {'name': 'Alan Turing', 'email': 'alan@example.org', 'city': 'London'} vs expected {'name': 'Alan Turing', 'email': 'alan@example.org', 'city': 'London'} |
| 10 | Web: Belmora capital | PASS | success | 5 | 11.1s | extracted capital == 'San Corvo', want 'San Corvo' |
| 11 | Web: Kestrel Isle capital | PASS | success | 5 | 11.2s | extracted capital == 'Northhaven', want 'Northhaven' |
| 12 | Web: Zorania river | PASS | success | 5 | 11.2s | extracted river == 'Saffron River', want 'Saffron River' |
| 13 | Disable event sounds in Settings | PASS | success | 7 | 9.5s | /Net/EnableEventSounds == 'false' (expected 'false') |
| 14 | Enable input feedback sounds | FAIL | failed | 4 | 6.4s | /Net/EnableInputFeedbackSounds == 'false' (expected 'true') |
| 15 | Spreadsheet from PDF (stationery) | PASS | success | 25 | 26.4s | all 4 rows present |
| 16 | Type text in the editor | FAIL | success | 3 | 4.3s | typed text NOT found in editor |
| 17 | Type text in the editor (2) | PASS | success | 3 | 4.6s | typed text found in editor |
| 18 | Terminal: echo to a file | PASS | success | 4 | 5.3s | out.txt contains 'deskdora-bench' |
| 19 | Terminal: mkdir and write | PASS | success | 4 | 5.8s | made/ok.txt contains 'ok' |
| 20 | Open the terminal | PASS | success | 2 | 3.5s | app xfce4-terminal: running |
| 21 | Open the spreadsheet app | PASS | success | 2 | 3.5s | app gnumeric: running |
| 22 | Unsupported goal (defragment) | FAIL | failed | 0 | 0.2s | no end state — unsupported goal |
| 23 | Web: article that does not exist | FAIL | failed | 2 | 6.8s | extracted capital == '', want '—' |
| 24 | Organise with pre-existing subfolder | FAIL | success | 59 | 54.8s | doc.txt not in Documents/; song.mp3 not in Audio/ |
| 25 | Spreadsheet from PDF (other filename) | PASS | success | 31 | 26.2s | all 6 rows present |
