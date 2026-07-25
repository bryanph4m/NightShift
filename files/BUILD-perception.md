---
title: "Night Shift: Build Card (perception)"
branch: perception
owner: Person 1
role: Trigger, Call Infrastructure, Perception, Orchestration
tags: [hackathon, scalekit, meetstream, groq, agents-in-production]
date: 2026-07-25
status: build
---

# Night Shift: perception

**Place this file at `BUILD.md` in the root of the `perception` branch.**

## Branch Context

You are working on the `perception` branch. Everything in this card happens here.

| Branch | Owner | Holds |
|---|---|---|
| `main` | Shared | Project overview, canonical shared contracts, setup docs |
| `perception` | You | Trigger, webhooks, Bot A, transcript pipeline, LLM integrations, orchestrator |
| `enforcement` | Person 2 | Bob's identity, Bot B, posting layer, scope checks, Slack, audit log |

Rules:

1. **Read `main`'s README before you write any code.** It holds the canonical shared contracts. The copies in this card are mirrors for convenience while you work.
2. **Do not commit to `main` during the build.** If a shared contract needs to change, change it on `main` first, tell Person 2 directly, then pull the change into this branch. Contract drift between the two branches is the failure mode that surfaces during integration, which is the worst possible time to find it.
3. **Do not commit to `enforcement`.** If you need something from Person 2's side to unblock yourself, stub it locally rather than reaching across branches.
4. Rebase or merge `main` into `perception` whenever `main` moves, so you never drift from the canonical contracts.

## Working Agreements

**No time or token constraints.** Do not optimize for brevity, do not shortcut research, do not skip verification steps to save effort. If a step says verify something twenty times, verify it twenty times. If an API's exact tool name is uncertain, look it up in the live docs rather than guessing. Thoroughness is the priority; correctness matters more than speed at every step of this build.

**No AI co-authorship in commits.** Do not add `Co-Authored-By: Claude` trailers, do not add "Generated with Claude Code" footers, do not reference Claude or any AI assistant in commit messages, PR descriptions, or code comments. Commits should read as ordinary human-authored work. This applies to every commit on this branch without exception.

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

**You own:** the CI trigger, the shared webhook infrastructure, Bot A, the transcript pipeline, both LLM integrations, and the turn-taking orchestrator.

**You do not own:** Bob's ScaleKit identity, Bot B, the chat-posting layer, the scope-check service, Slack escalation, or the audit log. Those live on `enforcement`. Stub them locally when you need them.

## Stack

| Component | Choice | Owned by this branch |
|---|---|---|
| Meeting platform | Google Meet, via MeetStream | Bot A only |
| Identity and tool execution | ScaleKit Agent Actions | Alice's identity only |
| Proposal extraction | Groq (latency-critical path) | Yes |
| Diagnosis | Claude Sonnet 5 (runs 3 times per demo) | Yes |
| Trigger | GitHub Actions failure webhook | Yes |

**Note on Groq:** this is Groq with a q (console.groq.com), the inference provider running open-weight models on custom hardware. Not Grok with a k, which is xAI's model family and unrelated. The API is OpenAI-compatible, so if you've written an OpenAI call before, you change the base URL and model name.

---

## Phase 1.1: Alice's ScaleKit Identity

**Estimated: 45 minutes. Do this first, before anything else. Person 2 is blocked until you finish step 6.**

1. Create a ScaleKit account. Pull `client_id` and `client_secret` from the dashboard under Developers, then Settings, then API Credentials. You'll also need the environment URL.
2. Install the ScaleKit SDK and initialize the client. Environment variables: `SCALEKIT_ENV_URL`, `SCALEKIT_CLIENT_ID`, `SCALEKIT_CLIENT_SECRET`. Some older ScaleKit samples use `SCALEKIT_ENVIRONMENT_URL` for the same value; pick one naming convention, record it in `main`'s README, and make sure Person 2 uses the same one.
3. Create a **GitHub connection** in the ScaleKit dashboard under AgentKit, then Connections. This requires a real GitHub OAuth App:
   - In GitHub Developer Settings, create an OAuth App
   - Copy the Client ID from the app's settings page
   - Generate a Client Secret
   - Paste both into the ScaleKit connection and save
4. Use `get_or_create_connected_account()` for Alice's identifier. If the status comes back non-active, generate an authorization link and complete the OAuth flow in a browser against Alice's real GitHub account.
5. Verify with `list_scoped_tools(identifier=alice_id, filter={"connection_names": ["github"]})` that Alice resolves to a real scoped tool set. Print the result and read it.
6. **Hand to Person 2 immediately:** the connection name you used, the `client_id`, the `client_secret`, and the env URL. They cannot start their Phase 2.1 without these.

**Commit to `perception`:** the ScaleKit client initialization and Alice's connected-account setup script.

**Deliverable:** Alice's ScaleKit identifier, documented in `main`'s README so both of you can reference it, with a verified non-empty scoped tool list.

---

## Phase 1.2: Repos, Permissions, and Planted Bugs

**Estimated: 1 hour. Paired with Person 2 for the verification step.**

These are external GitHub repos, separate from this project repo. Nothing here gets committed to `perception`, but document the final configuration in `main`'s README so it's reproducible.

Two repos, not one repo with path-level permissions. Simpler to configure, easier to verify, far easier to explain on stage.

1. Create `payments-service` and `notifications-service`. Give each a minimal but real codebase, something with enough structure that a CI failure is legible. A small Python or Node service with a test suite is ideal.
2. Set collaborator permissions to create a **symmetric asymmetry**:
   - Alice: write on `payments-service`, no write on `notifications-service`
   - Bob: write on `notifications-service`, no write on `payments-service`

   This symmetry is what makes bug 2's reverse handoff work with zero extra setup. Each agent is the capable one exactly once.
3. Enable branch protection on `main` for **both** repos, configured so neither Alice nor Bob can merge. This is bug 3's trigger and it needs to fail for both identities regardless of which service the bug is in.
4. Add a GitHub Actions workflow to each repo that runs the test suite on push.
5. Plant three bugs. Design them deliberately:
   - **Bug 1:** lives in `notifications-service`. Something Sonnet 5 can diagnose confidently from CI output. A wrong variable name, an off-by-one, a bad type coercion.
   - **Bug 2:** lives in `payments-service`. Same design principle.
   - **Bug 3:** requires a change that can only land via a merge to protected `main`. Frame it as a config or schema change that can't go through a feature branch.

   Keep bugs 1 and 2 genuinely simple. The demo's technical claim is about the permission boundary, not about how clever your LLM diagnosis is. A bug that takes three reasoning attempts to fix is a liability on stage.
6. Verify permissions manually in the GitHub UI for both accounts on both repos before writing any code against them.

**Sync point with Person 2, mandatory:** both of you independently run `list_scoped_tools()` for your own principal and confirm Alice and Bob resolve to genuinely different results. If this doesn't hold, nothing downstream is real. **Do not proceed past this until it does.**

---

## Phase 1.3: Webhook Infrastructure and CI Trigger

**Estimated: 1.5 hours. You own this endpoint; Person 2's Bot B signals through it.**

1. Stand up a publicly reachable HTTP server. ngrok is fine for a hackathon; a deployed endpoint is more stable if you have somewhere to put it. This single server handles three inbound event types, so route them cleanly:
   - `POST /webhook/github` for GitHub Actions failure events
   - `POST /webhook/meetstream/lifecycle` for bot joined, in-meeting, left
   - `POST /webhook/meetstream/transcript` for live transcript segments
2. Configure a GitHub webhook on both repos for workflow run events. Filter to failures only; you don't want successful runs triggering a call.
3. On a failure event, parse out: which repo, which workflow, the failing commit SHA, and the job logs or failure output. You need the actual error text for diagnosis, so if the webhook payload doesn't carry it, follow up with an API call to fetch the run logs.
4. On failure, start the session:
   - Use a **pre-created, fixed Google Meet link**. Generating one dynamically adds a dependency you don't need and a failure mode you don't want on stage.
   - Call MeetStream's create-bot endpoint for **Bot A** with the meeting link, a distinct display name (for example "Agent Alice"), and `video_required: false` to reduce bandwidth and processing overhead.
   - Save the returned `bot_id`. You'll need it to remove the bot cleanly via the remove-bot endpoint.
   - Set `live_transcription_required` with your `/webhook/meetstream/transcript` URL so you receive speaker-labeled segments in real time during the meeting rather than waiting for post-processing.
5. Signal Person 2's Bot B to join. Simplest reliable approach: write a session row to the shared store that their side polls, or expose an internal endpoint they call. Agree on which, and record the decision in `main`'s README.

**Commit to `perception`:** the webhook server, GitHub event parsing, and Bot A lifecycle management.

**Deliverable:** pushing a broken commit causes CI to fail, which causes Bot A to appear in the Meet call, with transcript segments arriving at your webhook.

---

## Phase 1.4: Transcript Store and Shared Schema

**Estimated: 1 hour.**

The schema is a **shared contract**. Its canonical copy lives in `main`'s README. The version below is a mirror for convenience. If it needs to change, change it on `main` first, tell Person 2, then pull into this branch.

Use SQLite or Postgres. Both branches read and write to this, so it's the integration surface.

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

**Ownership:** this branch writes `sessions`, `transcript_segments`, and `bugs`. The `enforcement` branch writes `audit_log`. Both read all four.

Write incoming transcript segments as they arrive. Do not batch; the extraction path needs them immediately.

**Commit to `perception`:** migrations or schema setup, and the transcript writer.

---

## Phase 1.5: Diagnosis with Claude Sonnet 5

**Estimated: 1.5 hours.**

This runs three times across the entire demo. Latency is nearly irrelevant here, and a couple of seconds of the agent visibly thinking before it speaks actually reads as natural on a call rather than as lag. Optimize purely for correctness.

1. Input: the CI failure output, the repo name, and enough repository context for the model to locate the problem. Fetching the relevant file contents through the GitHub connector is worth doing; diagnosing from a stack trace alone is unnecessarily hard.
2. Output: a proposal object matching the contract below.
3. Prompt the model explicitly for JSON only, no preamble, no markdown fences. Strip any fences defensively before parsing anyway.
4. Test against all three planted bugs before wiring it into the live pipeline. Run each one at least five times and confirm the diagnosis is stable. If bug 1 or 2 diagnoses inconsistently, the bug is too subtle; go back to Phase 1.2 and simplify it.

**Proposal object.** Canonical copy on `main`. This is what you produce and what `enforcement` consumes:

```json
{
  "bug_id": "bug-1",
  "session_id": "sess-abc",
  "description": "Null check missing on recipient list before send",
  "target_repo": "notifications-service",
  "target_path": "src/dispatch.py",
  "proposed_change": "<the actual diff or full replacement file content>",
  "proposing_agent": "agent_a"
}
```

`proposed_change` needs to be something the scope-check service can actually commit. Decide with Person 2 whether that's a unified diff or full file contents, and record it on `main`. Full file contents is simpler to apply and less likely to fail on a fuzzy patch.

**Commit to `perception`:** the diagnosis module and its prompt.

---

## Phase 1.6: Proposal Extraction with Groq

**Estimated: 1.5 hours.**

This runs continuously on rolling transcript windows and is the latency-critical path. MeetStream's media path already costs roughly 200ms, so slowness here shows up directly as the agents talking over each other or leaving dead air. The task itself is easy: is this a proposal, and what are the fields.

1. Sign up at console.groq.com and get an API key.
2. **Check their current model list rather than assuming a specific one.** Their catalog rotates as new open-weight models land. You want the current small-to-mid instruct model, not the largest one available; the largest is slower and this task doesn't need it.
3. The API is OpenAI-compatible. Point your OpenAI client at Groq's base URL and change the model name.
4. Build the extraction call: given the last N transcript segments, classify whether an agent has just proposed a fix, and extract the proposal object fields.
5. **Verify JSON reliability before integrating.** Write a throwaway script that runs your extraction prompt twenty times against the same input and confirm you get parseable JSON on every single run. Groq's structured output support is less polished than the frontier APIs. If it's flaky, tighten the prompt with an explicit schema and a hard "return only JSON, no preamble, no markdown" instruction. **Do not proceed until you get twenty clean parses.**
6. Keep the prompt short and the window small. Both help latency, and the task genuinely doesn't need broad context.
7. Measure and log actual round-trip latency on every call. You want this number for two reasons: to tune the window size, and because a live latency readout on Person 2's dashboard is real evidence of engineering rigor that most teams won't have. Write it to the shared store so their dashboard can read it.

**Commit to `perception`:** the Groq extraction module, the verification script from step 5 (keep it, it's proof of diligence), and latency instrumentation.

---

## Phase 1.7: Turn-Taking Orchestrator

**Estimated: 1.5 hours. This is the piece that keeps two LLM-driven agents from racing.**

Two agents on one call will talk over each other unless you enforce order in code. Build an explicit state machine per bug:

```
DIAGNOSED       -> agent that found it attempts its own fix
SELF_ATTEMPTED  -> ScaleKit call returned. If allowed: RESOLVED.
                   If denied: agent proposes on the call -> PROPOSED
PROPOSED        -> other agent's scope check runs
                   If allowed: commits, posts confirmation -> RESOLVED
                   If denied: both have now failed -> ESCALATED
ESCALATED       -> Slack sent, waiting on human
RESOLVED        -> logged, move to next bug
```

Rules to enforce:

- An agent only speaks when it has a proposal to make or a decision to report. No filler, no acknowledgments.
- After an agent speaks, it waits for the other's response before continuing. No overlapping turns.
- Bugs are processed strictly in sequence. Do not run bug 2's flow while bug 1 is still pending.
- Every state transition writes to `audit_log` via the `enforcement` branch's service.

**Critical:** the transition from `SELF_ATTEMPTED` to `PROPOSED` must be driven by a genuine ScaleKit denial, not by your code checking a table of who owns which repo. The whole demo rests on the real OAuth token being what fails. If you shortcut this, the demo is theater and a judge who asks to see the code will notice.

**Local development:** the scope-check service lives on `enforcement` and won't exist for a while. Stub it as a local function returning hardcoded decision objects matching the contract, so the orchestrator can be built and tested in full isolation. Keep the stub in the repo behind a flag; it's useful during rehearsal when you want to test call flow without burning real API calls.

**Commit to `perception`:** the orchestrator state machine and the scope-check stub.

---

## Phase 1.8: Integration and Rehearsal

**Estimated: 2 hours. Both people, together.**

### Merge strategy

1. Both branches merge into `main`, not into each other. Merge `enforcement` first, then `perception`, so any contract conflict surfaces against a stable base.
2. Do the merge with both of you present. If integration reveals a contract mismatch, fix it on `main` and pull down, rather than patching one side to accommodate the other. Divergent contracts are how you end up with two systems that each work alone.
3. After merging, run everything from `main`. Do not demo from a branch.

### Integration sequence

1. Replace your scope-check stub with the real service.
2. Run bug 1 end to end: broken commit, CI fails, bots join, Agent A diagnoses, Agent A's real call is denied, proposes, Agent B's real call succeeds, commit appears in GitHub attributed to Bob.
3. Run bug 2, confirming the reverse direction works identically.
4. Run bug 3, confirming both scope checks genuinely fail and the Slack message actually arrives.
5. **Rehearse the full sequence at least twice.** Live speech plus live OAuth plus two coordinating agents is exactly the category of system that fails once and then works.
6. Prime whoever plays the sleeping engineer to respond to the Slack message within seconds. The pause should read as real without becoming dead air.

---

## Risks You Own

**Groq JSON reliability.** Verify in Phase 1.6 before it's load-bearing. Fallback if it stays flaky: Claude Haiku 4.5 on the extraction path, which is slower but has stronger structured output. Make this call early, not during integration.

**Agents racing.** Phase 1.7 exists specifically to prevent this. Test it with deliberately overlapping input.

**Diagnosis instability.** If Sonnet 5 gives different fixes across runs for the same bug, the bug is too subtle. Simplify it rather than trying to prompt around it.

**Webhook reachability.** ngrok URLs change on restart. If you restart the tunnel, you have to update both the GitHub webhook config and the MeetStream bot config. Build a script that does both, or you will lose time to this at the worst moment.

**Contract drift.** You produce the proposal object that Person 2's entire service consumes. If you change its shape without updating `main` and telling them, integration breaks in a way that looks like a permission bug. Change contracts on `main` first, always.
