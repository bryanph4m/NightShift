# Enforcement -> Person 1 handoff notes

Things found while doing Phase 2.1 that affect the shared contracts on `main`.
Not committed to `main` directly -- relay and let Person 1 make the call.

## BLOCKING before integration: `target_branch` must reach Person 1

**This is not a nice-to-have note -- bug 3 does not work without it, and
it fails silently.** If `perception`'s extraction/orchestrator builds
proposals without this field, bug 3's proposal will look identical in
shape to bug 1 and bug 2, the scope-check service will default to the
feature-branch path, the commit will genuinely succeed, and the
escalation that's supposed to be the demo's third beat simply never
fires -- with no error anywhere to point at. This needs an explicit
conversation with Person 1 before Phase 2.4 or integration, not just a
read of this file.

Phase 2.3's scope-check service needs this and it isn't in the proposal
object contract on `main`. Reasoning:

Branch protection on `main` in both demo repos (required PR review,
`enforce_admins: true`) blocks a direct commit from **any** identity,
verified empirically -- even Bob, who has real write access to
notifications-service, gets a real GitHub 409 ("Changes must be made
through a pull request") when `github_file_create_update` targets
`branch: "main"` directly. That's exactly what makes bug 3's "both
denied" outcome genuine. But it also means bug 1 and bug 2's fixes
can't land on `main` directly either -- they need a fresh branch off
`main`'s current HEAD, which only requires ordinary repo push access
and isn't affected by main's protection.

So the service needs a signal for "this one targets main directly"
(bug 3) vs "create a feature branch" (bug 1 / bug 2, the default).
Added `target_branch` as an optional field: omit it and the service
creates `fix/{bug_id}` from HEAD; set it (e.g. `"main"`) and the
service commits straight to that branch instead, with no branch-create
step. Purely additive -- doesn't change the existing required fields.

Verified against real repos, both directions: Bob attempting `bug-3`
on notifications-service (where he has write) gets `protected_branch`
(409); Alice attempting the same repo (where she only has read) gets
`permission_denied` (404) instead -- a different, more mundane failure
that happens to also be a real denial. To cleanly demonstrate "branch
protection refuses even the writer" for both repos, test each identity
against the repo where *they* are the writer (see
`proposal-bug3-payments-agent-b.json`), not just one repo both ways.

## Error classification, verified against real GitHub responses

ScaleKit's own `http_status` / `tool_error_code` on `ScalekitToolException`
are uninformative (always 400 / `INTERNAL_ERROR`). The real GitHub status
lives inside `tool_error_message`, a JSON string: `{"message": ..., "status":
"<github status>"}`. Parse that, not the outer exception fields.

Confirmed real response shapes (`enforcement/errors.py`):
- **404 "Not Found"** on a write-oriented call (branch create, file
  update) from an identity with only read access -- this is GitHub
  hiding a write-restricted resource behind a 404 rather than a 403.
  Trustworthy as a genuine permission denial *only* because the service
  always does a successful read (`github_branch_get`) against the same
  repo first, so a 404 on the write step can't mean "repo doesn't exist."
- **409**, message containing "pull request" -- protected branch,
  distinct from a plain permission denial: the identity can have real
  write access to the repo and still be refused here.
- **422** on `github_file_create_update` missing `sha` -- not a
  permission signal at all, just a missing-field error on updates to an
  existing file (omit `sha` only for genuinely new files).

## Contract drift: GitHub connection name

`main`'s README documents `SCALEKIT_GITHUB_CONNECTION_NAME=github`. The actual
connection in the shared ScaleKit environment is named `github-98UjwezY`.
Filtering `list_connected_accounts()` / `list_scoped_tools()` by `"github"`
alone returns `NOT_FOUND: connection not found for key: github`; the
`-98UjwezY` suffixed slug is required.

Two ways to resolve, pick one:
- Rename the connection to `github` in the ScaleKit dashboard (AgentKit ->
  Connections), matching what main already documents, or
- Update `main`'s README to the real slug and have both branches read
  `SCALEKIT_GITHUB_CONNECTION_NAME` from env rather than hardcoding either
  value (already done this way in `enforcement/config.py`).

## Phase 2.1 mandatory sync point: confirmed

Both `bob` and `alice` connected accounts already existed in ScaleKit (status
`ACTIVE`, provider `GITHUB`) before this session started -- presumably set up
by Person 1. Verified independently, beyond just comparing scoped-tool lists
(which are identical for both, see note in `verify_identities.py` -- that's
expected for OAuth connectors and not a red flag):

Ran `execute_tool(tool_name="github_user_get_authenticated", ...)` as each
identity and confirmed they resolve to two distinct, real GitHub accounts:

- `bob` -> `ybalrs2-lab` (id 309194785, created 2026-07-25)
- `alice` -> `Yba1` (id 155815909, created 2024-01-06, 8 public repos)

This is the strongest form of the sync-point check: a real GitHub API call,
not just differing ScaleKit-side ids. Re-run `enforcement/scripts/verify_identities.py`
during rehearsal to reconfirm this still holds.

## Tool names confirmed against live docs

Cross-checked the five GitHub tool names main's README lists in "GitHub
connector tool names" against a real `list_scoped_tools()` response --
all five match exactly, including required input fields:

| Tool | Required inputs |
|---|---|
| `github_branch_get` | owner, repo, branch |
| `github_branch_create` | owner, repo, branch_name, sha |
| `github_file_contents_get` | owner, repo, path |
| `github_file_create_update` | owner, repo, path, message, content |
| `github_issue_create` | owner, repo, title |

No drift here -- safe to build Phase 2.3 against these as documented.

## Demo repos created

`payments-service` and `notifications-service` now exist under `bryanph4m`
(neither Alice's nor Bob's account, so no owner-bypass risk), each with a
small real Python codebase + pytest suite + passing CI, per main's Setup
section:

- https://github.com/bryanph4m/payments-service
- https://github.com/bryanph4m/notifications-service

Branch protection on `main` in both: 1 required approving review,
`enforce_admins: true` (blocks the owner too, not just Alice/Bob), no
restrictions. Verified via the API, not just the create call.

Collaborator permissions, verified live:

| | payments-service | notifications-service |
|---|---|---|
| Alice (`Yba1`) | write | read |
| Bob (`ybalrs2-lab`) | read | write |

Both are read (not zero-access) on the repo they can't write to, so the
diagnosing agent can still fetch file contents there. Invitations were
accepted programmatically using each identity's own ScaleKit-connected
GitHub token via `actions.request()` (the generic authenticated-proxy
method) with `PATCH /user/repository_invitations/{id}` -- there's no
dedicated accept-invitation tool in the connector's 112-tool catalog, so
this needed the escape hatch rather than a prebuilt tool.

Still needed from Person 1: the `workflow_run` webhook isn't wired up yet
(needs their `PUBLIC_WEBHOOK_BASE_URL`), and the three demo bugs haven't
been planted. Both repos currently pass CI cleanly.

## Bot B verified live in the real Meet call

Created against the real shared link (`https://meet.google.com/hnh-wkmi-btq`),
reached `InMeeting` after being admitted, and `send_message` was confirmed
visually in the meeting chat.

`get_chats` returned `"Manifest is still being processed"` immediately after
a `send_message` call -- **not reliable for real-time confirmation.** Don't
build the Phase 2.2 attribution check (or anything else) around polling
`get_chats` right after posting; use the live transcript webhook or visual
confirmation instead, and only treat `get_chats` as authoritative after some
delay or after the meeting ends.

## A third connected account exists

`list_connected_accounts()` also returned a third entry, identifier
`vivaansrivastava12@gmail.com`, provider `GITHUBPAT`, status `PENDING_AUTH`,
on a different connection (`connection_id: conn_135795253901787912`,
connector `githubpat-UEOPrOqN`). Not alice or bob, not referenced anywhere
in main's contracts. Possibly leftover from an earlier setup attempt or an
unrelated test. Worth asking Person 1 about before rehearsal so it isn't
mistaken for a third principal.
