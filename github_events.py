import hashlib
import hmac
import os

import httpx

GITHUB_API_BASE = "https://api.github.com"


class InvalidSignature(Exception):
    pass


def verify_signature(payload_body: bytes, signature_header: str | None, secret: str) -> None:
    """Raises InvalidSignature if signature_header doesn't match payload_body.

    GitHub sends the signature as 'sha256=<hex digest>' in the
    X-Hub-Signature-256 header, computed as HMAC-SHA256 of the raw request
    body using the webhook secret as key.
    """
    if not signature_header:
        raise InvalidSignature("missing X-Hub-Signature-256 header")

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise InvalidSignature("signature mismatch")


def is_failed_workflow_run(payload: dict) -> bool:
    """True only for a completed workflow_run whose conclusion is failure.

    Filters out in-progress runs, cancellations, and successful runs so a
    green build never starts a session.
    """
    return (
        payload.get("action") == "completed"
        and payload.get("workflow_run", {}).get("conclusion") == "failure"
    )


def extract_failure_info(payload: dict) -> dict:
    run = payload["workflow_run"]
    repo = payload["repository"]
    return {
        "owner": repo["owner"]["login"],
        "repo": repo["name"],
        "workflow_name": run.get("name"),
        "run_id": run["id"],
        "head_sha": run["head_sha"],
        "html_url": run.get("html_url"),
    }


def _auth_headers() -> dict:
    token = os.environ.get("GITHUB_API_TOKEN")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_job_logs(owner: str, repo: str, run_id: int) -> str:
    """Concatenates raw log text for every job in a workflow run.

    Two-step fetch: list the run's jobs, then pull each job's raw log text.
    The workflow_run webhook payload itself doesn't carry log content, so
    this always requires a follow-up API call.
    """
    with httpx.Client(base_url=GITHUB_API_BASE, headers=_auth_headers(), timeout=30) as client:
        jobs_resp = client.get(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
        jobs_resp.raise_for_status()
        jobs = jobs_resp.json()["jobs"]

        logs = []
        for job in jobs:
            log_resp = client.get(
                f"/repos/{owner}/{repo}/actions/jobs/{job['id']}/logs",
                follow_redirects=True,
            )
            log_resp.raise_for_status()
            logs.append(f"=== job: {job['name']} ===\n{log_resp.text}")

        return "\n\n".join(logs)
