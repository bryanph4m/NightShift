# Night Shift — `enforcement`

Person 2's workstream: Bob's ScaleKit identity, Bot B, the chat-posting layer both agents use, the scope-check service, Slack escalation, and the audit log and dashboard.

This is a working document. The project overview lives on [`main`](../../tree/main), and **`main`'s README is the canonical copy of every shared contract.** The contracts reproduced here are mirrors for convenience during a work session. If one needs to change, change it on `main` first, tell Person 1, then pull into this branch.

The full phase card for this workstream is in [`BUILD.md`](BUILD.md).

---

## Scope

**This branch owns:**

| Piece | Detail |
|---|---|
| Bob's ScaleKit identity | Connected account, OAuth flow, scoped tool verification against Alice's |
| Bot B | MeetStream bot joining as "Agent Bob", plus its lifecycle |
| Posting layer | `post_to_meeting(agent_id, text)` — the single path **both** agents post through |
| Scope-check service | The technical core: a real allow/deny driven by a genuine ScaleKit call |
| Slack escalation | Fires only after two genuine refusals for the same `bug_id` |
| Audit log and dashboard | The demo's closing artifact and its primary evidence |

**This branch does not own:** Alice's identity, the demo repos and planted bugs, the webhook infrastructure, Bot A, the transcript pipeline, either LLM integration, or the turn-taking orchestrator. Those live on `perception`.

**You do not touch Groq or Claude directly.** Your surface is ScaleKit and MeetStream. Proposals arrive as JSON; you never see the transcript they came from.

---

## Phases

### 2.1 — Bob's ScaleKit identity · ~45 min

**Blocked on Person 1 handing you credentials. Start the moment they do.**

You need from their Phase 1.1: the GitHub connection name, `client_id`, `client_secret`, and the environment URL.

1. Initialise the SDK. **Env var naming is `SCALEKIT_ENVIRONMENT_URL`** for our own `.env` — but the installed SDK's actual constructor (`scalekit-sdk-python` 2.15.0) takes the keyword argument **`env_url`**, not `environment_url`. Pass `ScalekitClient(env_url=os.environ["SCALEKIT_ENVIRONMENT_URL"], client_id=..., client_secret=...)` — settled on `main`, the env var name and the constructor kwarg name are different strings, don't conflate them.
2. `scalekit.actions.get_or_create_connected_account(connection_name="github", identifier="bob")` against **the same connection** Person 1 created. If `response.connected_account.status != "ACTIVE"`, call `get_authorization_link(...)` and complete the OAuth flow in a browser against **Bob's real, separate GitHub account**.
3. **Verify Bob and Alice are genuinely different — but not with `list_scoped_tools()`.** Tested against the live environment: it returns an identical tool-name list for both, because it reflects the GitHub connector's static catalog (which API operations exist), not the connected user's actual repo permissions. The reliable check calls a tool and reads back who GitHub says you are:

   ```python
   for who in ("alice", "bob"):
       result = scalekit.actions.execute_tool(
           tool_name="github_user_get_authenticated",
           identifier=who,
           tool_input={},
       )
       print(who, "->", result.data.get("login"))
   ```

   Print both logins side by side. If they match, the OAuth flows were completed as the same GitHub user — redo step 2 in a separate browser profile before going further.
4. Document both identifiers on `main`. Every call in your scope-check service depends on passing the correct one, and **passing the wrong identifier silently produces a wrong-but-plausible result** — the worst kind of bug to have in this system.

> **Use a separate browser profile or a private window for Bob's OAuth flow.** Completing it while still signed in as Alice is the easiest way to end up with two identical principals and a demo that proves nothing.

**⚠ Mandatory sync point with Person 1.** Confirm together that Alice and Bob resolve to genuinely different real GitHub logins via the `execute_tool` check above (not `list_scoped_tools()` — see step 3). **If this does not hold, nothing downstream is real. Do not proceed past it.**

✅ **Already verified this session:** `bob` → `ybalrs2-lab`, `alice` → `Yba1` — two distinct, real GitHub accounts. Re-run `enforcement/scripts/verify_identities.py` during rehearsal to reconfirm this still holds.

**Commit:** Bob's connected-account setup script and an identity-verification utility printing both principals' logins side by side. **Keep that utility** — you will want it during rehearsal.

### 2.2 — Bot B and the posting layer · ~1.5 hr

**This is the least-proven part of the whole build. Test it early**, while there is still time to fall back.

1. Create **Bot B** via `POST https://api.meetstream.ai/api/v1/bots/create_bot` against the same Meet link Person 1 is using. `bot_name: "Agent Bob"`, **`video_required: false`** (it defaults to `true`). Auth header is `Authorization: Token <MEETSTREAM_API_KEY>`.
2. Save the returned `bot_id`. Teardown is `GET /bots/{bot_id}/remove_bot`.
3. **Get both bots into the same real call and verify attribution.** Coordinate with Person 1, who owns the transcript webhook. Have each bot post something and confirm the messages come back with correctly distinguished speaker labels. If labels collide or are ambiguous, their extraction step will misassign proposals to the wrong agent and **the entire permission story breaks silently rather than loudly.** See the flag below — this step has a second, larger question buried in it.
4. Build the chat-posting layer on `POST /bots/{bot_id}/send_message` with body `{"message": "..."}`. Expose it as one internal function both branches call:

```python
post_to_meeting(agent_id: str, text: str) -> None
    # agent_id: "agent_a" | "agent_b"
    # routes to the correct bot_id, posts to the meeting chat
```

This signature is a **shared contract**; canonical copy on `main`. Both agents post through this single path — two divergent implementations will drift and produce inconsistent formatting on stage.

5. Standardise the message formats now, since these are what judges actually read. They are recorded on `main` so Person 1's orchestrator produces matching text:

| Event | Text |
|---|---|
| Proposal | `[Agent A] Bug in notifications-service/src/dispatch.py. I don't have write access there. Bob, can you take it?` |
| Allow | `[Agent B] Confirmed. Committed as Bob. <commit_url>` |
| Deny | `[Agent B] I don't have write access to payments-service either.` |
| Escalation | `[Agent A] Neither of us can merge to protected main. Paging on-call.` |

**Fallback if two bots do not work:** one bot, with both agents posting as clearly distinguishable identities in chat. You lose the visual effect of two participants; **the entire permission story survives intact.** Decide this **by the end of this phase, not later**, and if you fall back, update `main`'s README so the architecture description stays accurate.

### 2.3 — Scope-check service · ~2.5 hr

**The technical core of the demo.** Receives a proposal, returns a real allow/deny driven by a genuine ScaleKit call. Contracts under [Contracts](#contracts) below.

1. Determine the responding agent — the one that is **not** `proposing_agent` — and resolve its ScaleKit identifier.
2. Attempt the commit via `scalekit.actions.execute_tool()` using that identifier. The GitHub commit flow through ScaleKit is multi-step:
   - Read the current HEAD SHA of the target branch (`github_branch_get`)
   - Create a branch from that SHA (`github_branch_create` — it requires the SHA to branch from)
   - Apply the change (`github_file_create_update`)

   Fetching current file contents first uses `github_file_contents_get`, which returns Base64 for files.

   > **Confirm every tool name against the live connector docs and `list_scoped_tools()` for your identifier before building on it.** The names above were correct at the time of writing (and confirmed this session against a live response — see `enforcement/HANDOFF.md`), but connector catalogues change, and **a wrong tool name produces an error that closely resembles a permission denial.** That will send you debugging the wrong hypothesis and cost you real time.

3. **⚠ Let the call genuinely succeed or fail on the real OAuth scope.** Do not pre-check permissions in your own code and skip the ScaleKit call. Do not maintain a table of who owns which repo. The entire demo rests on the real per-user token being the thing that fails. **If a judge asks to see the code, this is the line they will look at.**
4. Catch the failure and translate it into a readable `reason`. **Classify the error type** — genuine permission denial vs. network vs. malformed input vs. wrong tool name — because during the demo you need to know instantly which one you are looking at.
5. On success, capture the commit URL. This is your strongest single piece of evidence: a real commit in a real repo attributed to a real GitHub user.
6. Construct commit messages with **no AI attribution.** These commits are the demo's primary evidence and they need to read as genuine work by the engineer whose identity executed them.
7. Write an `audit_log` row on **every** check, allow or deny, recording the identifier the call **actually ran as**.

**Commit:** the scope-check service, the proposal fixtures, and error-classification logic.

### 2.4 — Slack escalation · ~1.5 hr

1. Add the **Slack connector** in ScaleKit and complete the OAuth flow so the agent posts as itself.
2. Build the escalation trigger. It fires when **both** agents' scope checks have failed for the same `bug_id`. **Not** when your code decides a change "seems structural" — when two real ScaleKit calls have genuinely been refused. This distinction is the entire credibility of the escalation moment and judges will probe it.
3. Send a real Slack message to the on-call engineer containing: which bug and which repo, what was attempted, **which two identities were refused and why**, and what decision is needed from the human.
4. Post to the meeting chat that escalation has occurred and the agents are paused.
5. Build the resume path: on the human's response, post the resolution back to the meeting chat and write it to `audit_log`.
6. **Prime the human.** Whoever plays the sleeping engineer needs to be watching Slack and ready to respond within seconds. The pause should read as real without turning into dead air on stage.

**AgentPhone is a conditional stretch goal.** Only after Slack works and the full three-bug sequence has rehearsed clean end to end with time left over. A ringing phone is a better "2am" moment than a Slack ping, but telephony means TTS, audio timing, and a new failure surface on top of multi-agent coordination that is already the bulk of your complexity budget. If you get there, **keep Slack firing in parallel** rather than replacing it, so a telephony failure on stage does not take the escalation beat down with it. **Build it on a sub-branch off `enforcement`** so an unfinished attempt never blocks the merge.

**Commit:** the Slack connector setup, escalation trigger, and resume path.

### 2.5 — Audit log and dashboard · ~2 hr

The demo's closing artifact. The audit log is the evidence that no agent ever exceeded its principal's real access — it is what turns "we built agents that respect permissions" from a claim into something a judge can verify.

**You are the only writer to `audit_log`.** `perception` writes `sessions`, `transcript_segments`, and `bugs`; both branches read all four.

1. Write every check to `audit_log`. Every row carries the identity the call **actually ran as**, not the identity you intended to use.
2. Expose `GET /audit-log` returning the rows for a session.
3. Build a minimal page rendering it. A live-tailing CLI works if time is short, but a page reads far better on a projector and costs maybe 30 extra minutes.
4. Include on the dashboard:
   - **Both principals and their real scoped permissions**, shown up front so the audience understands the setup before the first bug
   - The live log, updating as the session runs
   - **Commit URLs as clickable links**, so you can open one on stage and show a real commit by a real user
   - **Latency numbers** from Person 1's Groq path — they write these to the shared store in their Phase 1.6. Most teams claim real-time and show nothing; three concrete numbers on screen is cheap, verifiable evidence of rigor.

**Commit:** the audit writer, the read endpoint, and the dashboard.

### 2.6 — Integration and rehearsal · ~2 hr, both people

**Merge strategy.** Both branches merge into `main`, not into each other. Merge `enforcement` **first**, then `perception`, so any contract conflict surfaces against a stable base. Do it with both people present. If integration reveals a mismatch, fix it on `main` and pull down rather than patching one side to accommodate the other. **Run the demo from `main`, never from a branch.**

**Sequence.**

1. Person 1 swaps their scope-check stub for your real service. **Keep your proposal fixtures around** — if the real pipeline misbehaves, running your service against a known-good fixture tells you instantly whether the problem is yours or theirs.
2. Bug 1 end to end: broken commit → CI fails → bots join → Agent A diagnoses → A's real call denied → proposes → your service runs Bob's real call → commit in GitHub attributed to Bob.
3. Bug 2, confirming the reverse direction works identically with Alice as the executor.
4. Bug 3, confirming both scope checks genuinely fail and the Slack message actually arrives.
5. **Rehearse the full sequence at least twice.**
6. **Have the real failed ScaleKit call and its actual error response ready to show** if a judge asks. This is the single most likely follow-up question and having the answer on hand is worth more than any slide.

---

## Contracts

Mirrors. **[`main`'s README](../../blob/main/README.md#shared-contracts) is canonical.**

### Consumed by this branch — proposal object

Produced by `perception`.

```json
{
  "bug_id": "bug-1",
  "session_id": "sess-abc",
  "description": "Null check missing on recipient list before send",
  "target_repo": "notifications-service",
  "target_path": "src/dispatch.py",
  "proposed_change": "<diff or full file content>",
  "proposing_agent": "agent_a"
}
```

**`proposed_change` carries full replacement file contents, not a unified diff** — settled on `main`. Apply it directly through `github_file_create_update`; there is no patch to fuzz.

### Produced by this branch — decision object

Consumed by `perception`.

```json
{
  "bug_id": "bug-1",
  "decision": "allow",
  "identity_used": "bob_scalekit_identifier",
  "reason": "write access to notifications-service verified",
  "commit_url": "https://github.com/...",
  "error": null
}
```

`decision` is `allow`, `deny`, or `escalated`. On a denial `commit_url` is `null` and `error` carries the classified failure. **`identity_used` is always the identifier the call actually executed as** — read it back from the call site, do not populate it from the value you meant to pass.

### Owned by this branch — posting function

Called by both branches.

```python
post_to_meeting(agent_id: str, text: str) -> None
    # agent_id: "agent_a" | "agent_b"
    # routes to the correct bot_id, posts to the meeting chat
```

### Shared schema

```sql
CREATE TABLE sessions (
  session_id      TEXT PRIMARY KEY,
  meet_link       TEXT,
  triggered_by    TEXT,          -- repo + workflow that failed
  started_at      TIMESTAMP,
  ended_at        TIMESTAMP
);

CREATE TABLE transcript_segments (
  id              INTEGER PRIMARY KEY,
  session_id      TEXT,
  speaker_label   TEXT,          -- as reported by MeetStream
  text            TEXT,
  timestamp       TIMESTAMP
);

CREATE TABLE bugs (
  bug_id          TEXT PRIMARY KEY,
  session_id      TEXT,
  description     TEXT,
  target_repo     TEXT,
  target_path     TEXT,
  proposed_change TEXT,
  status          TEXT           -- diagnosed | proposed | resolved | escalated
);

CREATE TABLE audit_log (
  id               INTEGER PRIMARY KEY,
  session_id       TEXT,
  bug_id           TEXT,
  proposing_agent  TEXT,
  responding_agent TEXT,
  identity_used    TEXT,         -- ScaleKit identifier the call actually ran as
  decision         TEXT,         -- allow | deny | escalated
  reason           TEXT,
  commit_url       TEXT,
  timestamp        TIMESTAMP
);
```

**This branch is the only writer to `audit_log`.** `perception` writes the other three. Both read all four.

---

## Dependencies on `perception`

### What you are blocked on from them

| You need | From | Blocks |
|---|---|---|
| GitHub connection name, `client_id`, `client_secret`, environment URL | Their Phase 1.1 step 6 | **Your entire Phase 2.1.** This is a hard block — you cannot start until they deliver it. |
| Alice's identifier and scoped tool list | Their Phase 1.1 step 5 | The mandatory sync point |
| Demo repos with permissions and branch protection configured | Their Phase 1.2 | Your scope-check service has nothing real to call against. **Until then, develop against fixtures** — see [Local development](#local-development). |
| A `sessions` row to poll | Their Phase 1.3 step 5 | Bot B's automatic join. Dispatch Bot B manually while developing. |
| Real proposal objects | Their Phase 1.5 / 1.6 | **Nothing.** Hand-write the JSON. This is the whole reason your track runs in parallel. |
| Groq latency numbers in the shared store | Their Phase 1.6 step 7 | The dashboard's latency panel only |

### What Person 1 is blocked on from you

| They need | From | Blocks |
|---|---|---|
| `post_to_meeting()` | Phase 2.2 | Nothing immediately — they stub it |
| Scope-check service | Phase 2.3 | Nothing immediately — they stub it. It becomes a hard block at integration. |
| Confirmation that two bots on one call produce distinguishable speaker labels | Phase 2.2 step 3 | **Their extraction step, materially.** Get this answer to them early. |
| The escalation path | Phase 2.4 | Their orchestrator's `ESCALATED` state |

### ⚠ The mandatory sync point

Before anything downstream is real, both of you must independently confirm **Alice and Bob resolve to genuinely different real GitHub accounts.** `list_scoped_tools()` cannot be used for this — tested live, it returns an identical tool-name list for both, since it reflects the connector's static catalog rather than per-user permissions. Instead run `execute_tool(tool_name="github_user_get_authenticated", identifier=who, tool_input={})` for each principal and compare the `login` field in the response, side by side.

If the logins match, both OAuth flows were completed as the same GitHub user — most likely because Bob's was done in a browser still signed in as Alice. Redo it in a separate profile or private window. Do not build past this point until they differ. **Confirmed this session: `bob` → `ybalrs2-lab`, `alice` → `Yba1`.**

### ⚠ Open question: which channel do the agents actually talk on?

This is the highest-priority integration question in the build, and it surfaces during your Phase 2.2 verification.

Your posting layer writes to **meeting chat** via `POST /bots/{bot_id}/send_message`. MeetStream documents chat retrieval as polling `GET /bots/{bot_id}/get_chats` — there is no documented chat webhook. Person 1's live transcription webhook carries **transcribed speech**, attributed by `speakerName`.

If those are genuinely separate channels, **their transcript webhook will never see your posted messages** and their Groq extraction step will have nothing to read. The recommended default recorded on `main` is that `perception` polls `get_chats` for agent-to-agent messages and keeps the transcript webhook for any human speech. `get_chats` returns `speakerName` and `speakerDisplayName` per message, so attribution survives either way.

Answer this on a live two-bot call in Phase 2.2 — it is the same call where you verify speaker labels — and **record the answer on `main` immediately.** Person 1 cannot build the extraction step correctly until they have it.

---

## Local development

**Your track is not blocked, and this is the reason.** Person 1's proposal output will not exist for hours. Hand-write proposal JSON matching the input contract and develop the entire scope-check service against it. Keep those fixtures in the repo as test data — they double as regression tests during integration, and on demo day they are how you tell instantly whether a failure is yours or theirs.

```
fixtures/
  proposal-bug1-agent-a.json   # A proposes, B should be allowed
  proposal-bug2-agent-b.json   # B proposes, A should be allowed
  proposal-bug3-agent-a.json   # protected main, both should be denied
  proposal-malformed.json      # error classification path
```

Run the service directly against a fixture:

```bash
python -m enforcement.scope_check fixtures/proposal-bug1-agent-a.json
```

This exercises the real ScaleKit call, the real GitHub permission boundary, and the real audit write, with **no meeting, no transcript, and no LLM anywhere in the loop.**

**Develop Bot B and the posting layer against a Meet link you open yourself.** You do not need Person 1's trigger, their session row, or Bot A to test that a bot joins and posts. Dispatch it manually.

**Develop the dashboard against seeded `audit_log` rows.** Insert a handful covering allow, deny, and escalated, plus a latency row, and build the page against those. It will render correctly the first time a real session runs.

**Test Slack escalation without bug 3.** Call the trigger directly with two synthetic denial records for the same `bug_id`. Verify it does **not** fire on one denial.

---

## Verification

Everything below must pass before integration.

**Two principals differ.** `execute_tool(tool_name="github_user_get_authenticated", ...)` for Alice and Bob returns two different real GitHub logins, side by side. (Not `list_scoped_tools()` — confirmed live that it returns an identical tool list for both regardless of identity, since it reflects the connector's static catalog.) See the mandatory sync point above. Nothing else on this list means anything until this passes.

**Two bots on one call produce correctly distinguished speaker labels.** Get Bot A and Bot B into the same real Meet call with Person 1, have each post, and confirm the messages come back attributed to the right bot with unambiguous labels. **This is the single riskiest verification in the build** and its failure mode is silent: ambiguous labels do not error, they just cause proposals to be assigned to the wrong agent, and the permission story quietly stops being true.

> **Documented fallback if labels collide or two bots will not co-exist:** run **one bot** and have both agents post as clearly distinguishable identities in chat — the `[Agent A]` / `[Agent B]` prefixes already in the message formats carry the distinction on their own. You lose the visual effect of two participants in the roster. **The entire permission story survives intact,** because it was never carried by the bot count — it is carried by which ScaleKit identifier each `execute_tool` call runs as. **Decide by the end of Phase 2.2, not later,** and update `main`'s README so the architecture description stays accurate.

**A denial is a real denial.** Attempt a commit as Bob against `payments-service` and confirm the failure is a genuine GitHub permission error surfaced through ScaleKit — not a tool-name error, not a network error, not a malformed-input error. **Save the raw error response.** You will want it on stage.

**An allow is a real commit.** Attempt as Bob against `notifications-service` and confirm the resulting `commit_url` resolves to a real commit **authored by Bob's GitHub account** — and that its message carries no AI attribution.

**No pre-checking.** Read your own service back and confirm there is no code path that decides allow/deny before calling ScaleKit, and no table mapping identities to repos. This is the property the whole demo rests on.

**Correct identifier on every call.** Log the identifier on every single call and check it during rehearsal — not just the decision. A wrong identifier produces a plausible-looking wrong answer, which is the worst failure mode available here.

**Escalation fires on two denials and only two.** One denial must not trigger Slack. Two denials against the same `bug_id` must. Two denials against *different* `bug_id`s must not.

**Slack arrives.** A real message in a real workspace, naming both refused identities and the reason for each.

**Every check writes a row.** Allow, deny, and escalated all land in `audit_log` with `identity_used` populated from the actual call site.

---

## Risks owned by this workstream

**Two bots on one call.** The least-proven piece of the build, and **the failure is silent rather than loud.** Test in Phase 2.2 before anything depends on it. Fallback documented above, and it preserves the entire permission story.

**Wrong identifier passed silently.** If your service uses Alice's identifier where it should use Bob's, you get a result that looks plausible and is wrong. **Log the identifier on every single call** and check it during rehearsal, not just the decision.

**Escalation reading as scripted.** If bug 3's escalation is not visibly driven by two real failed calls, it looks like a narrative beat you wrote. Keep the real error responses accessible and be ready to show them.

**ScaleKit tool name mismatches.** These produce errors that resemble permission failures. **Verify tool names against the live docs and via `list_scoped_tools()` before assuming a denial is a denial.** Your error classification in Phase 2.3 step 4 exists specifically so you can tell these apart under time pressure.

**Contract drift.** You consume the proposal object and produce the decision object. If Person 1 changes the proposal shape without updating `main`, your service breaks in a way that **looks like a permission bug.** Pull `main` regularly and re-read the contracts section when you do. See `enforcement/HANDOFF.md` for a live example: the GitHub connection name documented on `main` (`github`) does not match the actual connection in the shared environment (`github-98UjwezY`) — flagged, not yet resolved.

**AI attribution leaking into demo commits.** Your service authors the commits that are the demo's primary evidence. They must read as ordinary human work by the engineer whose identity executed them. Check the commit-message construction in Phase 2.3 step 6 and verify it on a real commit before demo day.
