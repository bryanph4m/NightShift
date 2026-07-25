"""Classifies a failed ScaleKit execute_tool call into a readable reason.

Built against real observed errors (see enforcement/HANDOFF.md), not guessed:

- ScaleKit's own http_status/tool_error_code are uninformative (always 400 /
  INTERNAL_ERROR) -- the real GitHub status lives inside tool_error_message,
  which is a JSON string: {"message": ..., "status": "<github status>"}.
- A genuine permission denial on a write call comes back from GitHub as
  404 "Not Found", not 403 -- GitHub hides write-restricted resources this
  way rather than confirming they exist. Trustworthy as "permission_denied"
  here specifically because our own flow always does a successful read
  (github_branch_get) against the same repo before attempting a write, so
  a 404 on the write step cannot mean "repo doesn't exist".
- A direct commit to a protected branch fails with 409 and a message
  containing "pull request" -- a distinct failure mode from a plain
  permission denial: the identity may have real write access to the repo
  and still be refused here, because nobody bypasses branch protection.
"""

import json


def classify_error(exc: Exception) -> tuple[str, str]:
    """Returns (error_type, reason)."""
    tool_error_message = getattr(exc, "tool_error_message", None)
    if tool_error_message:
        try:
            payload = json.loads(tool_error_message)
        except (ValueError, TypeError):
            payload = None

        if isinstance(payload, dict):
            status = str(payload.get("status", ""))
            message = payload.get("message", "") or ""

            if status == "409" or "pull request" in message.lower():
                return "protected_branch", f"branch protection refused the change: {message}"
            if status in ("403", "404"):
                return "permission_denied", f"identity lacks required access: {message}"
            if status == "422":
                return "malformed_input", f"GitHub rejected the request as invalid: {message}"
            return "unknown_github_error", f"GitHub returned {status or 'an error'}: {message}"

        return "unknown_scalekit_error", tool_error_message

    error_code = getattr(exc, "error_code", None)
    if error_code:
        return "scalekit_error", f"{error_code}: {getattr(exc, 'message', str(exc))}"

    return "network_error", str(exc)
