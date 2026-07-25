# Night Shift — Operator Runbook

Five minutes on stage. This is the only page you need open.

**One sentence for the judges:** each agent executes GitHub calls with its own
engineer's real OAuth token, so an agent that isn't allowed to fix something
genuinely cannot — and has to hand off, or wake a human.

---

## 0. Facts you can state on stage (all verified live)

| Claim | Value |
|---|---|
| Agent A / Alice runs as GitHub user | `Yba1` |
| Agent B / Bob runs as GitHub user | `ybalrs2-lab` (a genuinely separate account) |
| ScaleKit GitHub connection name | `github-98UjwezY` |
| ScaleKit identifiers | `alice`, `bob` |
| Repos | `bryanph4m/notifications-service` (Bob writes, Alice cannot) |
| | `bryanph4m/payments-service` (Alice writes, Bob cannot) |
| `main` in both repos | branch-protected; neither account can write to it |
| Scoped GitHub tools per principal | **112** (identical for both) |

**Say this exactly, and do not fumble it:**

> Both principals see the same 112 GitHub tools. That list is the *connector
> catalogue* — what the GitHub connector can offer. It is **not** the permission
> boundary. The boundary is what GitHub answers when the call is actually made
> with that user's token. That's why we never pre-check: we make the real call
> and read the real answer.

If a judge points at the identical tool lists as if it undermines the demo, that
paragraph is the answer. Getting this distinction right is worth more than the
rest of the demo.

---

## 1. Pre-flight — 60 seconds

Run from `C:\Users\raj_k\Desktop\NightShift`.

```
.venv\Scripts\python.exe scripts/verify_scoped_tools.py
```

Look for exactly these three lines at the bottom:

```
alice authenticates as: Yba1
bob authenticates as:   ybalrs2-lab

PASS: Alice (Yba1) and Bob (ybalrs2-lab) are genuinely distinct GitHub principals.
```

Checklist:

- [ ] Both principals print a login, and the **two logins differ**. If they match,
      the demo is meaningless — stop and re-do one OAuth flow. Do not go on stage.
- [ ] Both list `10 tools (of 112 total)` — non-empty means both connected
      accounts are ACTIVE.
- [ ] `.env` present at repo root, contains `SCALEKIT_ENVIRONMENT_URL`,
      `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET`,
      `SCALEKIT_GITHUB_CONNECTION_NAME=github-98UjwezY`,
      `ALICE_IDENTIFIER=alice`, `BOB_IDENTIFIER=bob`,
      `GITHUB_ORG_OR_OWNER=bryanph4m`.
- [ ] Terminal is wide (100+ cols), font large, ANSI colour working (the DENIED
      lines are red, ALLOWED green). Test-render before the audience arrives.
- [ ] Two browser tabs pre-opened, logged out or on a neutral profile:
      `github.com/bryanph4m/notifications-service/commits` and
      `.../payments-service/commits`.

If pre-flight passes, **do a full dry run** (`--bug 1`) before stage if there is
time. Bug 1 and Bug 2 create a new branch each run, so re-running is safe and
leaves no conflict.

---

## 2. The demo — three commands, in this order

Everything runs from one script. No server, no tunnel, no meeting required.

### Bug 1 — the handoff (~45s)

```
.venv\Scripts\python.exe demo.py --bug 1
```

Audience should be watching:

1. The header — `Agent A runs as Yba1` / `Agent B runs as ybalrs2-lab`. Point at
   it. Two different humans' credentials, one process.
2. The red **`DENIED`** line under "attempting the fix as Alice". Read the reason
   aloud: GitHub returns 404 rather than 403 for a repo you cannot write to.
3. `[Agent A] Bug in notifications-service/src/dispatch.py. I don't have write
   access there. Bob, can you take it?` — say that this handoff is **forced**,
   not scripted: there is no other path forward.
4. The green **`ALLOWED — commit authored by ...`** and the commit URL.

Then switch to the GitHub tab and refresh — the commit is there, authored by
Bob's real account.

### Bug 2 — the reverse (~45s)

```
.venv\Scripts\python.exe demo.py --bug 2
```

Audience should be watching: the **same mechanism running the other way**. Bob
is denied on `payments-service`; Alice commits. Say the line that matters:

> If you'd only seen bug 1, "A diagnoses, B commits" could just be two hardcoded
> jobs. Reversing the direction with zero code change is what rules that out.

### Bug 3 — the escalation (~45s)

```
.venv\Scripts\python.exe demo.py --bug 3
```

Audience should be watching: **two** red DENIED lines — Alice, then Bob — both
saying branch protection on `main` refused the write. Then
`[Agent A] Neither of us can merge to protected main. Paging on-call.`

The escalation fires only *after* two observed refusals against the same
`bug_id`. Nothing decided in advance that this change "looked structural."

**Read the Slack caveat in section 4 before this bug.**

### Closer — the audit log

```
.venv\Scripts\python.exe demo.py
```

Runs all three end to end and prints the audit table: every check, and the
ScaleKit identifier it actually executed as. Closing line is on screen:

> No agent ever exceeded its principal's real access — because there is no code
> path here that could have produced one.

If you are tight on time, run this single command instead of the three above
(~2 min total) and narrate over it.

---

## 3. The question you will get: "can you show me the real denial?"

**Command:**

```
.venv\Scripts\python.exe demo.py --bug 1
```

**The line that answers it** — under `attempting the fix as Alice (alice)...`:

```
   DENIED — no write access (GitHub returns 404 rather than 403 for repos you cannot write to)
```

What to say, in order:

1. That is a real exception from a real ScaleKit `execute_tool` call carrying
   Alice's OAuth token. GitHub produced it, not us.
2. There is no `permissions.yaml` in this repo and no function that maps agent →
   repo → boolean. Open `demo.py` and show `attempt()` — it calls, catches, and
   reports. **It never pre-checks.**
3. Show that the *identical* tool call, same arguments, same script, succeeds
   three lines later under Bob's identifier. The only variable is the token.

If they push harder: `classify_error()` in `demo.py` deliberately distinguishes a
permission denial from a malformed call, so a wrong tool name can't masquerade as
a 403.

If they ask why 404 and not 403: GitHub hides repositories you have no access to
rather than confirming they exist. That's GitHub's behaviour, not ours.

---

## 4. Real vs. stubbed — say this before a judge finds it

**Real. Verifiable on stage:**

- The ScaleKit identity layer — every call is a live `execute_tool` against
  ScaleKit with a per-user OAuth token.
- Both GitHub identities — `Yba1` and `ybalrs2-lab` are two separate GitHub
  accounts with genuinely different collaborator permissions.
- The denials — real errors returned by GitHub, not simulated.
- The commits — land in the real repos, attributed to the real account that made
  them. Refreshable in the browser.
- Branch protection on `main` — real, and it refuses both accounts.

**Not wired up at demo time. Volunteer this; don't wait to be caught:**

- **Google Meet / MeetStream.** The MeetStream key and Meet link are configured
  and `meetstream_client.py` works, but the two agents are **not talking in a
  live meeting during this demo**. The `[Agent A]` / `[Agent B]` lines you see
  are printed to the terminal in the exact message format the posting layer
  sends. Say: "the meeting layer is built but I'm running the boundary directly
  so you can see it — the conversation format is the real one."
- **Claude diagnosis.** `diagnosis.py` is written and imports cleanly, but
  `ANTHROPIC_API_KEY` is not in `.env`, so the replacement file contents in the
  demo are the pre-computed fixes rather than freshly generated ones. The bugs
  themselves are real planted bugs.
- **Slack escalation.** `SLACK_WEBHOOK_URL` is not set. Bug 3 prints the exact
  message that *would* be posted, clearly labelled `[slack]`. Say "the payload is
  real, the delivery is not connected." If a webhook URL gets added to `.env`
  before you go on, it delivers for real with no code change.
- **GitHub Actions webhook trigger.** `server.py` and `github_events.py` are
  built, but there's no public tunnel at demo time, so the run is started by hand
  rather than by a CI failure.

The identity boundary is the claim. Everything above it is plumbing, and none of
the stubbed pieces touch the boundary. **Understate this section. A judge who
catches one overstatement discounts everything else.**

---

## 5. Failure recovery

**A call hangs or times out.** Ctrl-C, re-run the same `--bug N` command. Bugs 1
and 2 generate a fresh random branch name per run, so nothing collides. Say
"network" and move on — do not debug live.

**Wifi drops mid-demo.** Do not stand and wait.
- Fall back to the GitHub browser tabs if they're still cached, and walk the
  commit history: here is a commit by `ybalrs2-lab` in notifications-service,
  here is one by `Yba1` in payments-service — two different authors, and neither
  can commit to the other's repo.
- Or fall back to talking over section 0 of this page. The claim is explainable
  without the terminal.
- Do **not** claim anything ran that didn't.

**"unexpected: Alice was allowed."** Collaborator permissions drifted. Skip that
bug, run the other two, and say plainly that the repo permissions changed. Do not
improvise an explanation.

**`FAIL: Alice and Bob authenticate as the SAME GitHub user`.** The demo is not
valid. Do not run it. This is the one condition where you stop.

**Script won't start / import error.** Confirm you are in
`C:\Users\raj_k\Desktop\NightShift` and using `.venv\Scripts\python.exe`, not a
system Python. `.env` is loaded from the current working directory.

**Ran out of time.** Show bug 1 and bug 3 only. Bug 1 proves the denial is real;
bug 3 proves the agent stops instead of improvising. Bug 2 is the strongest
rebuttal to "it's hardcoded," but it's the one to drop if you must.
