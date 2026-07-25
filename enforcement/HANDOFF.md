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

## A third connected account exists

`list_connected_accounts()` also returned a third entry, identifier
`vivaansrivastava12@gmail.com`, provider `GITHUBPAT`, status `PENDING_AUTH`,
on a different connection (`connection_id: conn_135795253901787912`,
connector `githubpat-UEOPrOqN`). Not alice or bob, not referenced anywhere
in main's contracts. Possibly leftover from an earlier setup attempt or an
unrelated test. Worth asking Person 1 about before rehearsal so it isn't
mistaken for a third principal.
