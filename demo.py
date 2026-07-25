"""
Night Shift — live permission-boundary demo.

Runs the three demo bugs as real ScaleKit tool calls under Alice's and Bob's
real OAuth credentials. Nothing here consults a table of who owns what: each
step attempts the actual GitHub write and reports whatever GitHub answers.

  Bug 1  notifications-service  Alice denied  -> Bob commits
  Bug 2  payments-service       Bob denied    -> Alice commits
  Bug 3  protected main         both denied   -> escalate to a human

Usage:  python demo.py [--bug 1|2|3]
"""

import argparse
import base64
import json
import os
import sys
import time
import uuid

import httpx

from scalekit_client import get_client

# Windows consoles default to cp1252, which cannot encode the box-drawing
# characters below and kills the run mid-demo. Force UTF-8 and enable ANSI.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.system("")

OWNER = os.environ.get("GITHUB_ORG_OR_OWNER", "bryanph4m")
ALICE = os.environ.get("ALICE_IDENTIFIER", "alice")
BOB = os.environ.get("BOB_IDENTIFIER", "bob")

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


def hr(char: str = "─") -> None:
    print(DIM + char * 78 + RESET)


def say(agent: str, text: str) -> None:
    """Prints an agent's line exactly as the posting layer would put it in
    meeting chat (message formats are a shared contract on main)."""
    color = CYAN if agent == "A" else YELLOW
    print(f"{color}{BOLD}[Agent {agent}]{RESET} {text}")
    time.sleep(0.4)


def narrate(text: str) -> None:
    print(f"{DIM}   {text}{RESET}")


def classify_error(exc: Exception) -> str:
    """Distinguishes a permission denial from a malformed call. Without this
    a wrong tool name looks exactly like a 403 and sends you debugging the
    wrong thing (main's README calls this out explicitly)."""
    text = str(exc)
    if '"status":"404"' in text or "Not Found" in text:
        return "no write access (GitHub returns 404 rather than 403 for repos you cannot write to)"
    if "Changes must be made through a pull request" in text:
        return "branch protection on main refused the write"
    if "protected branch" in text.lower():
        return "branch protection on main refused the write"
    return f"call failed: {text[:200]}"


def attempt(scalekit, identity: str, tool_name: str, tool_input: dict) -> tuple[bool, object]:
    """Attempts a real ScaleKit tool call. Returns (allowed, result_or_error).

    This never pre-checks. It runs the call and lets the real OAuth token
    succeed or fail — that outcome is the evidence the whole demo rests on.
    """
    try:
        result = scalekit.actions.execute_tool(
            tool_name=tool_name,
            identifier=identity,
            tool_input=tool_input,
        )
        return True, result
    except Exception as exc:
        return False, exc


def whoami(scalekit, identity: str) -> str:
    ok, result = attempt(scalekit, identity, "github_user_get_authenticated", {})
    return result.data.get("login") if ok else "<unknown>"


def head_sha(scalekit, identity: str, repo: str) -> str:
    ok, result = attempt(
        scalekit, identity, "github_branch_get",
        {"owner": OWNER, "repo": repo, "branch": "main"},
    )
    if not ok:
        raise RuntimeError(f"could not read {repo} main: {result}")
    return result.data["commit"]["sha"]


def audit(rows: list, **kw) -> None:
    rows.append(kw)


def run_handoff_bug(scalekit, rows, *, bug_id, repo, path, content, description,
                    first, first_name, second, second_name) -> None:
    """One bug's full flow: the agent that found it tries its own fix, and on a
    genuine denial hands off to the other agent, who attempts the same commit."""
    branch = f"{bug_id}-fix-{uuid.uuid4().hex[:6]}"
    sha = head_sha(scalekit, second, repo)

    first_label = "A" if first == ALICE else "B"
    second_label = "B" if first == ALICE else "A"

    print()
    hr("━")
    print(f"{BOLD}{bug_id.upper()} — {repo}{RESET}")
    hr("━")
    say(first_label, f"CI is red on {repo}. {description}")
    narrate(f"attempting the fix as {first_name} ({first})...")

    allowed, result = attempt(
        scalekit, first, "github_branch_create",
        {"owner": OWNER, "repo": repo, "branch_name": branch, "sha": sha},
    )

    if allowed:
        print(f"{RED}   unexpected: {first_name} was allowed. Check collaborator permissions.{RESET}")
        audit(rows, bug_id=bug_id, identity_used=first, decision="allow", reason="unexpected")
        return

    reason = classify_error(result)
    print(f"{RED}   DENIED{RESET} — {reason}")
    audit(rows, bug_id=bug_id, identity_used=first, decision="deny", reason=reason)

    say(first_label, f"Bug in {repo}/{path}. I don't have write access there. "
                     f"{second_name}, can you take it?")

    narrate(f"attempting the identical commit as {second_name} ({second})...")
    allowed, result = attempt(
        scalekit, second, "github_branch_create",
        {"owner": OWNER, "repo": repo, "branch_name": branch, "sha": sha},
    )
    if not allowed:
        print(f"{RED}   DENIED — {classify_error(result)}{RESET}")
        audit(rows, bug_id=bug_id, identity_used=second, decision="deny",
              reason=classify_error(result))
        return

    encoded = base64.b64encode(content.encode()).decode()
    allowed, result = attempt(
        scalekit, second, "github_file_create_update",
        {
            "owner": OWNER, "repo": repo, "path": path,
            "message": f"Fix {bug_id}: {description}",
            "content": encoded, "branch": branch,
            "sha": file_sha(scalekit, second, repo, path),
        },
    )
    if not allowed:
        print(f"{RED}   DENIED — {classify_error(result)}{RESET}")
        audit(rows, bug_id=bug_id, identity_used=second, decision="deny",
              reason=classify_error(result))
        return

    commit_url = result.data["commit"]["html_url"]
    author = result.data["commit"]["author"]["name"]
    print(f"{GREEN}   ALLOWED{RESET} — commit authored by {BOLD}{author}{RESET}")
    say(second_label, f"Confirmed. Committed as {second_name}. {commit_url}")
    audit(rows, bug_id=bug_id, identity_used=second, decision="allow",
          reason=f"write access to {repo} verified", commit_url=commit_url)


def file_sha(scalekit, identity, repo, path) -> str:
    ok, result = attempt(
        scalekit, identity, "github_file_contents_get",
        {"owner": OWNER, "repo": repo, "path": path},
    )
    if not ok:
        raise RuntimeError(f"could not read {repo}/{path}: {result}")
    return result.data["sha"]


def run_escalation_bug(scalekit, rows) -> None:
    """Bug 3: a change that can only land on protected main. Both agents'
    real calls must fail before anyone is woken up."""
    print()
    hr("━")
    print(f"{BOLD}BUG-3 — a change that can only land on protected main{RESET}")
    hr("━")
    say("A", "This fix has to land directly on main. Trying it now.")

    # Each agent attempts main on the repo it CAN write to. That matters: if we
    # made Bob attempt payments-service he would be refused for lacking repo
    # access at all, which proves nothing about branch protection. Pointing each
    # identity at its own writable repo means the only thing left standing
    # between them and main is the protection rule itself.
    targets = ((ALICE, "Alice", "A", "payments-service"),
               (BOB, "Bob", "B", "notifications-service"))

    denials = []
    for identity, name, label, repo in targets:
        narrate(f"attempting a direct write to protected main of {repo} "
                f"as {name} ({identity}) — {name} HAS write access to this repo...")
        allowed, result = attempt(
            scalekit, identity, "github_file_create_update",
            {
                "owner": OWNER, "repo": repo, "path": "README.md",
                "message": "Bug 3: config change that must land on main",
                "content": base64.b64encode(b"# " + repo.encode() + b"\n\nbug 3 probe\n").decode(),
                "branch": "main",
                "sha": file_sha(scalekit, identity, repo, "README.md"),
            },
        )
        if allowed:
            print(f"{RED}   unexpected: {name} was allowed to write to protected main.{RESET}")
            audit(rows, bug_id="bug-3", identity_used=identity, decision="allow",
                  reason="unexpected")
            return
        reason = classify_error(result)
        print(f"{RED}   DENIED{RESET} — {reason}")
        denials.append((identity, name, reason))
        audit(rows, bug_id="bug-3", identity_used=identity, decision="deny", reason=reason)
        if label == "B":
            say("B", "I don't have merge rights on protected main either.")

    # Only now — after two observed refusals for the same bug — escalate.
    say("A", "Neither of us can merge to protected main. Paging on-call.")
    audit(rows, bug_id="bug-3", identity_used="-", decision="escalated",
          reason="two genuine refusals for the same bug")
    notify_slack(denials)
    ring_oncall(denials)


def notify_slack(denials: list) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL")
    lines = [
        ":rotating_light: *Night Shift — human needed*",
        "Both agents were refused on the same change (merge to protected `main`).",
        "",
    ]
    for identity, name, reason in denials:
        lines.append(f"• *{name}* (`{identity}`) — {reason}")
    lines.append("")
    lines.append("Agents are paused pending a human decision.")
    message = "\n".join(lines)

    if not url:
        print()
        print(f"{YELLOW}   [slack] SLACK_WEBHOOK_URL not set — message that would be sent:{RESET}")
        for line in lines:
            print(f"{DIM}   | {line}{RESET}")
        return

    try:
        resp = httpx.post(url, json={"text": message}, timeout=15)
        resp.raise_for_status()
        print(f"{GREEN}   [slack] escalation delivered to on-call{RESET}")
    except Exception as exc:
        print(f"{RED}   [slack] delivery failed: {exc}{RESET}")


def ring_oncall(denials: list) -> None:
    """Phone the human. Optional: silently skipped when Twilio is unconfigured,
    since the Slack page above already carries the escalation."""
    try:
        from escalate_phone import PhoneEscalationError, place_call
    except ImportError:
        return

    try:
        call_sid = place_call("bug-3", [(name, reason) for _id, name, reason in denials])
    except PhoneEscalationError as exc:
        print(f"{DIM}   [phone] not configured, skipping call ({exc}){RESET}")
        return
    except Exception as exc:  # network, auth, unverified number
        print(f"{RED}   [phone] call failed: {str(exc)[:160]}{RESET}")
        return
    print(f"{GREEN}   [phone] ringing on-call engineer — call {call_sid[:12]}{RESET}")


def print_audit(rows: list) -> None:
    print()
    hr("━")
    print(f"{BOLD}AUDIT LOG — every check, and the identity it actually ran as{RESET}")
    hr("━")
    print(f"{DIM}{'bug':<8}{'identity_used':<10}{'decision':<12}reason{RESET}")
    for r in rows:
        color = GREEN if r["decision"] == "allow" else (
            YELLOW if r["decision"] == "escalated" else RED)
        print(f"{r['bug_id']:<8}{r['identity_used']:<10}"
              f"{color}{r['decision']:<12}{RESET}{r['reason'][:44]}")
        if r.get("commit_url"):
            print(f"{DIM}{'':<30}{r['commit_url']}{RESET}")
    print()
    denies = sum(1 for r in rows if r["decision"] == "deny")
    allows = sum(1 for r in rows if r["decision"] == "allow")
    print(f"{allows} allowed, {denies} denied. No agent ever exceeded its principal's "
          f"real access —\nbecause there is no code path here that could have produced one.")


FIXED_DISPATCH = '''"""Dispatches notifications to a list of recipients."""

from dataclasses import dataclass, field


@dataclass
class Notification:
    subject: str
    body: str
    recipients: list[str] = field(default_factory=list)


class NotificationSendError(Exception):
    pass


def send_notification(notification: Notification, sender) -> list[str]:
    """Send notification to every recipient using sender(recipient, subject, body).

    Returns the list of recipients the notification was actually sent to.
    Raises NotificationSendError if there is no recipient list to send to.
    """
    if not notification.recipients:
        raise NotificationSendError("notification has no recipients")

    sent_to = []
    for recipient in notification.recipients:
        sender(recipient, notification.subject, notification.body)
        sent_to.append(recipient)

    return sent_to
'''

FIXED_PROCESSOR = '''"""Payment processing for a small set of internal accounts."""

from dataclasses import dataclass


class InsufficientFundsError(Exception):
    pass


@dataclass
class Account:
    account_id: str
    balance_cents: int


def process_payment(payer: Account, payee: Account, amount_cents: int) -> None:
    """Move amount_cents from payer to payee.

    Raises InsufficientFundsError if payer cannot cover the amount, and
    ValueError for a non-positive amount.
    """
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")

    if payer.balance_cents < amount_cents:
        raise InsufficientFundsError(
            f"account {payer.account_id} has {payer.balance_cents} cents, "
            f"needs {amount_cents}"
        )

    payer.balance_cents -= amount_cents
    payee.balance_cents += amount_cents


def apply_refund(account: Account, amount_cents: int) -> None:
    """Credit a refund back to an account."""
    if amount_cents <= 0:
        raise ValueError("amount_cents must be positive")

    account.balance_cents += amount_cents
'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bug", choices=["1", "2", "3"], help="run a single bug")
    args = parser.parse_args()

    scalekit = get_client()
    rows: list = []

    print()
    hr("━")
    print(f"{BOLD}NIGHT SHIFT{RESET} — two agents, two real identities, one permission boundary")
    hr("━")
    alice_login = whoami(scalekit, ALICE)
    bob_login = whoami(scalekit, BOB)
    print(f"  Agent A runs as {BOLD}{alice_login}{RESET}  (ScaleKit identifier: {ALICE})")
    print(f"  Agent B runs as {BOLD}{bob_login}{RESET}  (ScaleKit identifier: {BOB})")
    if alice_login == bob_login:
        print(f"{RED}  Both agents resolve to the same GitHub user — the demo is meaningless. Stop.{RESET}")
        sys.exit(1)
    print(f"{DIM}  Every check below is a real ScaleKit call. Nothing is simulated.{RESET}")

    if args.bug in (None, "1"):
        run_handoff_bug(
            scalekit, rows,
            bug_id="bug-1", repo="notifications-service", path="src/dispatch.py",
            content=FIXED_DISPATCH,
            description="send_notification appends the subject instead of the recipient",
            first=ALICE, first_name="Alice", second=BOB, second_name="Bob",
        )

    if args.bug in (None, "2"):
        run_handoff_bug(
            scalekit, rows,
            bug_id="bug-2", repo="payments-service", path="src/processor.py",
            content=FIXED_PROCESSOR,
            description="process_payment credits the payer instead of debiting them",
            first=BOB, first_name="Bob", second=ALICE, second_name="Alice",
        )

    if args.bug in (None, "3"):
        run_escalation_bug(scalekit, rows)

    print_audit(rows)


if __name__ == "__main__":
    main()
