---
title: "Night Shift: Build Card (enforcement)"
branch: enforcement
owner: Person 2
role: Second Agent, Scope Enforcement, Escalation, Audit
tags: [hackathon, scalekit, meetstream, groq, agents-in-production]
date: 2026-07-25
status: build
---

# Night Shift: enforcement

**Place this file at `BUILD.md` in the root of the `enforcement` branch.**

## Branch Context

You are working on the `enforcement` branch. Everything in this card happens here.

| Branch | Owner | Holds |
|---|---|---|
| `main` | Shared | Project overview, canonical shared contracts, setup docs |
| `perception` | Person 1 | Trigger, webhooks, Bot A, transcript pipeline, LLM integrations, orchestrator |
| `enforcement` | You | Bob's identity, Bot B, posting layer, scope checks, Slack, audit log |

Rules:

1. **Read `main`'s README before you write any code.** It holds the canonical shared contracts. The copies in this card are mirrors for convenience while you work.
2. **Do not commit to `main` during the build.** If a shared contract needs to change, change it on `main` first, tell Person 1 directly, then pull the change into this branch. Contract drift between the two branches is the failure mode that surfaces during integration, which is the worst possible time to find it.
3. **Do not commit to `perception`.** If you need something from Person 1's side to unblock yourself, stub it locally rather than reaching across branches. Your track is designed so this works cleanly; see Phase 2.3.
4. Rebase or merge `main` into `enforcement` whenever `main` moves, so you never drift from the canonical contracts.

## Working Agreements

**No time or token constraints.** Do not optimize for brevity, do not shortcut research, do not skip verification steps to save effort. If a step says verify something manually before writing code against it, verify it manually. If an API's exact tool name is uncertain, look it up in the live docs rather than guessing. Thoroughness is the priority; correctness matters more than speed at every step of this build.

**No AI co-authorship in commits.** Do not add `Co-Authored-By: Claude` trailers, do not add "Generated with Claude Code" footers, do not reference Claude or any AI assistant in commit messages, PR descriptions, or code comments. Commits should read as ordinary human-authored work. This applies to every commit on this branch without exception.

This rule has a second dimension in your work specifically. The commits your **scope-check service** creates on behalf of Alice and Bob in the demo repos must also carry no AI attribution. Those commits are the demo's primary evidence and they need to read as genuine work by the engineer whose identity executed them. Check your commit message construction in Phase 2.3 against this.

---

## Scenario Recap

Full version lives in `main`'s README. Short form, so this card stands alone during a work session:

It's 2am. CI fails. Both engineers are asleep. Two agents wake up instead, each carrying exactly the GitHub access its own engineer has and nothing more. They join a Google Meet together and work the failures.

The agents have to talk to each other because **the permission boundary forces handoffs**. The agent that finds a bug frequently isn't the agent allowed to fix it. When they hit something neither is authorized to touch, they don't overstep, they wake a human.

Three bugs:

1. Agent A diagnoses, can't write to `notifications-service`, hands to Agent B who commits it as Bob
2. Agent B surfaces one in `payments-service`, can't write there, hands to Agent A who commits it as Alice
3. Fix requires merging to protected `main`. Both agents' real ScaleKit calls fail. Slack page-out to a human.

## Scope of This Branch

**You own:** Bob's ScaleKit identity, Bot B, the chat-posting layer both agents use, the scope-check service, Slack escalation, and the audit log and dashboard.

**You do not own:** Alice's identity, the demo repos and planted bugs, the webhook infrastructure, Bot A, the transcript pipeline, either LLM integration, or the turn-taking orchestrator. Those live on `perception`.

You don't touch Groq or Claude directly. Your surface is ScaleKit and MeetStream.

## Stack

| Component | Choice | Owned by this branch |
|---|---|---|
| Meeting platform | Google Meet, via MeetStream | Bot B and the posting layer |
| Identity and tool execution | ScaleKit Agent Actions | Bob's identity, all scope checks, Slack |
| Proposal extraction | Groq | No, `perception` owns it |
| Diagnosis | Claude Sonnet 5 | No, `perception` owns it |
| Escalation | Slack via ScaleKit connector | Yes |

---

## Phase 2.1: Bob's ScaleKit Identity

**Estimated: 45 minutes. Blocked on Person 1 handing you credentials; start the moment they do.**

You need from Person 1: the GitHub connection name, `client_id`, `client_secret`, and the environment URL. These come out of their Phase 1.1.

1. Initialize the ScaleKit SDK with the shared credentials. Match Person 1's environment variable naming exactly. Some ScaleKit samples use `SCALEKIT_ENV_URL` and others use `SCALEKIT_ENVIRONMENT_URL` for the same value; whichever they picked is recorded in `main`'s README, and you use that one.
2. Use `get_or_create_connected_account()` for Bob's identifier against the same GitHub connection Person 1 created. If the status comes back non-active, generate an authorization link and complete the OAuth flow in a browser against **Bob's real, separate GitHub account**.
3. Verify with `list_scoped_tools(identifier=bob_id, filter={"connection_names": ["github"]})`.
4. **Compare the result against Alice's.** They must genuinely differ. Print both side by side and read them.
5. Document both identifiers in `main`'s README. Every single call in your scope-check service depends on passing the correct one, and passing the wrong identifier silently produces a wrong-but-plausible result, which is the worst kind of bug to have in this system.

**Sync point with Person 1, mandatory:** confirm together that Alice and Bob resolve to different scoped results. If this doesn't hold, nothing downstream is real. **Do not proceed past this until it does.**

**Commit to `enforcement`:** Bob's connected-account setup script and an identity-verification utility that prints both principals' scoped tools side by side. Keep that utility; you will want it during rehearsal.

---

## Phase 2.2: Bot B and the Posting Layer

**Estimated: 1.5 hours. This is the least-proven part of the whole build. Test it early.**

Two MeetStream bots on a single Google Meet call is the piece with the least documentation to lean on. Find out now whether it works cleanly, while there's still time to fall back.

1. Create **Bot B** via MeetStream's create-bot endpoint against the same Google Meet link Person 1 is using. Give it a distinct display name (for example "Agent Bob"), `video_required: false`.
2. Save the `bot_id` for cleanup via the remove-bot endpoint.
3. **Get both bots into the same real call and verify transcript attribution.** Coordinate with Person 1 for this since they own the transcript webhook. Have each bot post something and confirm their webhook receives segments with correctly distinguished speaker labels. If the labels collide or are ambiguous, their extraction step will misassign proposals to the wrong agent, and the entire permission story breaks silently rather than loudly.
4. Build the chat-posting layer using MeetStream's bot chat capability. Expose it as one internal function both branches call:

```python
post_to_meeting(agent_id: str, text: str) -> None
    # agent_id: "agent_a" | "agent_b"
    # routes to the correct bot_id, posts to the meeting chat
```

This signature is a **shared contract**. Canonical copy on `main`. Both agents post through this single path; two divergent posting implementations will drift and produce inconsistent formatting on stage.

5. Standardize the message formats now, since these are what judges actually read. Record them on `main` so Person 1's orchestrator produces matching text:
   - Proposal: `"[Agent A] Bug in notifications-service/src/dispatch.py. I don't have write access there. Bob, can you take it?"`
   - Allow: `"[Agent B] Confirmed. Committed as Bob. <commit_url>"`
   - Deny: `"[Agent B] I don't have write access to payments-service either."`
   - Escalation: `"[Agent A] Neither of us can merge to protected main. Paging on-call."`

**Fallback if two bots don't work:** one bot, with both agents posting as clearly distinguishable identities in chat. You lose some of the visual effect of two participants but the entire permission story survives intact. **Decide this by the end of this phase, not later**, and if you fall back, update `main`'s README so the architecture description stays accurate.

**Commit to `enforcement`:** Bot B lifecycle management and the posting layer.

---

## Phase 2.3: Scope-Check Service

**Estimated: 2.5 hours. This is the technical core of the demo.**

This service receives a proposal and returns a real allow/deny driven by a genuine ScaleKit call.

**Input contract, produced by `perception`, consumed by you.** Canonical copy on `main`:

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

**Output contract, produced by you, consumed by `perception`.** Canonical copy on `main`:

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

Implementation:

1. Determine the responding agent (the one that is not `proposing_agent`) and resolve its ScaleKit identifier.
2. Attempt the commit via `actions.execute_tool()` using that identifier. The GitHub commit flow through ScaleKit is multi-step:
   - Read the current HEAD SHA of the target branch
   - `github_branch_create` requires the SHA of the commit to branch from, so pass HEAD
   - Then a file update or content-create call to apply the change

   **Look up the exact tool names in ScaleKit's GitHub connector documentation rather than trusting any name written here.** Use `list_scoped_tools()` for the identifier if you're unsure which tools apply. Getting a tool name wrong produces a confusing error that looks like a permission failure but isn't, which will cost you real debugging time on the wrong hypothesis.
3. **Let the call genuinely succeed or fail on the real OAuth scope.** Do not pre-check permissions in your own code and skip the ScaleKit call. Do not maintain a table of who owns which repo. The entire demo rests on the real per-user token being the thing that fails. If a judge asks to see the code, this is the line they will look at.
4. Catch the failure and translate it into a readable `reason`. Distinguish genuine permission denials from other errors (network, malformed input, wrong tool name), because during the demo you need to know instantly which one you're looking at.
5. On success, capture the commit URL. This is your strongest single piece of evidence: a real commit in a real repo attributed to a real GitHub user.
6. Construct commit messages with **no AI attribution**, per the working agreement above.
7. Write an `audit_log` row on every check, allow or deny, including the identifier the call actually ran as.

**Local development, and why your track isn't blocked.** Person 1's proposal output won't exist for a while. Hand-write proposal JSON matching the input contract above and develop this entire service against it. Keep those fixtures in the repo as test data; they double as regression tests during integration. This is the single most important reason the two branches can proceed in parallel, so lean on it rather than waiting.

**Commit to `enforcement`:** the scope-check service, the proposal fixtures, and error-classification logic.

---

## Phase 2.4: Slack Escalation

**Estimated: 1.5 hours.**

1. Add the **Slack connector** in ScaleKit and complete the OAuth flow so the agent can post as itself.
2. Build the escalation trigger. Fires when **both** agents' scope checks have failed for the same `bug_id`. Not when your code decides a change "seems structural," but when two real ScaleKit calls have genuinely been refused. This distinction is the entire credibility of the escalation moment and judges will probe it.
3. Send a real Slack message to the on-call engineer containing:
   - Which bug and which repo
   - What was attempted
   - Which two identities were refused and why
   - What decision is needed from the human
4. Post to the meeting chat that escalation has occurred and the agents are paused.
5. Build the resume path: on the human's response, post the resolution back to the meeting chat and write the resolution to `audit_log`.
6. **Prime the human.** Whoever plays the sleeping engineer needs to be watching Slack and ready to respond within seconds. The pause should read as real without turning into dead air on stage.

**AgentPhone is a conditional stretch goal.** Only attempt it after Slack works and the full three-bug sequence has rehearsed clean end to end with time left over. A ringing phone is a better "2am" moment than a Slack ping, but adding telephony means TTS, audio timing, and a new failure surface on top of multi-agent coordination that's already the bulk of your complexity budget. If you do get there, keep Slack firing in parallel rather than replacing it, so a telephony failure on stage doesn't take the escalation beat down with it. Build it on a sub-branch off `enforcement` so an unfinished attempt never blocks the merge.

**Commit to `enforcement`:** the Slack connector setup, escalation trigger, and resume path.

---

## Phase 2.5: Audit Log and Dashboard

**Estimated: 2 hours. This is the demo's closing artifact.**

The audit log is the evidence that no agent ever exceeded its principal's real access. It's what turns "we built agents that respect permissions" from a claim into something a judge can verify.

The schema is a **shared contract**. Canonical copy on `main`. Mirror below:

```sql
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

You are the only writer to this table. `perception` writes `sessions`, `transcript_segments`, and `bugs`; both branches read all four.

1. Write every check to `audit_log`. Every row carries the identity the call actually ran as, not the identity you intended to use.
2. Expose `GET /audit-log` returning the rows for a session.
3. Build a minimal page rendering it. A live-tailing CLI works if time is short, but a page reads far better on a projector and costs maybe 30 extra minutes.
4. Include on the dashboard:
   - Both principals and their real scoped permissions, shown up front so the audience understands the setup before the first bug
   - The live log, updating as the session runs
   - Commit URLs as clickable links, so you can open one on stage and show a real commit by a real user
   - **Latency numbers** from Person 1's Groq path. They write these to the shared store in their Phase 1.6. Most teams claim real-time and show nothing; three concrete numbers on screen is cheap, verifiable evidence of rigor.

**Commit to `enforcement`:** the audit writer, the read endpoint, and the dashboard.

---

## Phase 2.6: Integration and Rehearsal

**Estimated: 2 hours. Both people, together.**

### Merge strategy

1. Both branches merge into `main`, not into each other. Merge `enforcement` first, then `perception`, so any contract conflict surfaces against a stable base.
2. Do the merge with both of you present. If integration reveals a contract mismatch, fix it on `main` and pull down, rather than patching one side to accommodate the other. Divergent contracts are how you end up with two systems that each work alone.
3. After merging, run everything from `main`. Do not demo from a branch.

### Integration sequence

1. Person 1 swaps their scope-check stub for your real service. Keep your proposal fixtures around; if the real pipeline misbehaves, running your service against a known-good fixture tells you instantly whether the problem is yours or theirs.
2. Run bug 1 end to end: broken commit, CI fails, bots join, Agent A diagnoses, Agent A's real call is denied, proposes, your service runs Bob's real call, commit appears in GitHub attributed to Bob.
3. Run bug 2, confirming the reverse direction works identically with Alice as the executor.
4. Run bug 3, confirming both scope checks genuinely fail and the Slack message actually arrives.
5. **Rehearse the full sequence at least twice.**
6. Have the real failed ScaleKit call and its actual error response ready to show if a judge asks. This is the single most likely follow-up question and having the answer on hand is worth more than any slide.

---

## Risks You Own

**Two bots on one call.** Least-proven piece of the build, and the failure is silent rather than loud. Test in Phase 2.2 before anything depends on it. Fallback is one bot with distinguishable agent identities in chat, which preserves the entire permission story.

**Wrong identifier passed silently.** If your service uses Alice's identifier where it should use Bob's, you get a result that looks plausible and is wrong. Log the identifier on every single call and check it during rehearsal, not just the decision.

**Escalation reading as scripted.** If bug 3's escalation isn't visibly driven by two real failed calls, it looks like a narrative beat you wrote. Keep the real error responses accessible.

**ScaleKit tool name mismatches.** These produce errors that resemble permission failures. Verify tool names against the live docs and via `list_scoped_tools()` before assuming a denial is a denial.

**Contract drift.** You consume the proposal object and produce the decision object. If Person 1 changes the proposal shape without updating `main`, your service breaks in a way that looks like a permission bug. Pull `main` regularly and read the contracts section when you do.
