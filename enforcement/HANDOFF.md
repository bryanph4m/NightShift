# Enforcement -> Person 1 handoff notes

Things found while doing Phase 2.1 that affect the shared contracts on `main`.
Not committed to `main` directly -- relay and let Person 1 make the call.

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
