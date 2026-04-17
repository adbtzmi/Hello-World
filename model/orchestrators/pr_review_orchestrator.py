#!/usr/bin/env python3
"""
model/orchestrators/pr_review_orchestrator.py
===============================================
PR Review Orchestrator — Model layer for the automated JIRA / PR Approval pipeline.

Handles the full deployment workflow:
  1. Attach validation doc to JIRA and tag requestor for review
  2. On requestor approval → commit & push code changes to remote
  3. Create pull request to TSE leads/seniors for code review
  4. On PR approval → merge code and close/transition the release JIRA
  5. Send Teams notifications at each milestone or on timeout

All Bitbucket REST API calls and git subprocess calls live here.
The controller calls these functions from background threads.
"""

import json
import logging
import os
import ssl
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("bento_app")

# ── Constants ──────────────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = 30          # How often to poll for PR approval
PR_STALE_TIMEOUT_SECONDS = 7200     # 2 hours before sending "stale" reminder
JIRA_TRANSITION_DONE = "Done"       # Default JIRA transition name for closing


# ══════════════════════════════════════════════════════════════════════════════
# 1. JIRA OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def attach_file_to_jira(
    issue_key: str,
    file_path: str,
    jira_base_url: str,
    email: str,
    jira_token: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Attach a file (e.g. validation doc) to a JIRA issue.

    Uses JIRA REST API v3:
        POST /rest/api/3/issue/{issueKey}/attachments

    Returns:
        dict with 'success' and optional 'error' or 'attachment_id'.
    """
    _log(log_callback, f"📎 Attaching {os.path.basename(file_path)} to {issue_key}...")

    if not os.path.exists(file_path):
        return {"success": False, "error": f"File not found: {file_path}"}

    url = f"{jira_base_url}/rest/api/3/issue/{issue_key}/attachments"

    try:
        import base64
        auth_str = f"{email}:{jira_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        # Build multipart form data manually
        boundary = "----BentoUploadBoundary"
        filename = os.path.basename(file_path)

        with open(file_path, "rb") as f:
            file_data = f.read()

        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + file_data + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Authorization", f"Basic {auth_b64}")
        req.add_header("X-Atlassian-Token", "no-check")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            resp_data = json.loads(resp.read().decode())

        attachment_id = resp_data[0].get("id", "") if isinstance(resp_data, list) else ""
        _log(log_callback, f"✓ File attached to {issue_key} (id={attachment_id})")
        return {"success": True, "attachment_id": attachment_id}

    except Exception as e:
        _log(log_callback, f"✗ Failed to attach file: {e}")
        return {"success": False, "error": str(e)}


def add_jira_comment(
    issue_key: str,
    comment_body: str,
    jira_base_url: str,
    email: str,
    jira_token: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Add a comment to a JIRA issue (e.g. tagging requestor for review).

    Uses JIRA REST API v3:
        POST /rest/api/3/issue/{issueKey}/comment

    Args:
        comment_body: Plain text or ADF JSON string for the comment.

    Returns:
        dict with 'success'.
    """
    _log(log_callback, f"💬 Adding comment to {issue_key}...")

    url = f"{jira_base_url}/rest/api/3/issue/{issue_key}/comment"

    # Build Atlassian Document Format (ADF) body
    adf_body = {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": comment_body}
                    ]
                }
            ]
        }
    }

    try:
        import base64
        auth_str = f"{email}:{jira_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        data = json.dumps(adf_body).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {auth_b64}")
        req.add_header("Content-Type", "application/json")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            resp.read()

        _log(log_callback, f"✓ Comment added to {issue_key}")
        return {"success": True}

    except Exception as e:
        _log(log_callback, f"✗ Failed to add comment: {e}")
        return {"success": False, "error": str(e)}


def transition_jira_issue(
    issue_key: str,
    transition_name: str,
    jira_base_url: str,
    email: str,
    jira_token: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Transition a JIRA issue to a target status (e.g. 'Done', 'Closed').

    Steps:
      1. GET available transitions
      2. Find matching transition by name
      3. POST transition

    Returns:
        dict with 'success'.
    """
    _log(log_callback, f"🔄 Transitioning {issue_key} → '{transition_name}'...")

    try:
        import base64
        auth_str = f"{email}:{jira_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        ctx = ssl._create_unverified_context()

        # Step 1: Get available transitions
        trans_url = f"{jira_base_url}/rest/api/3/issue/{issue_key}/transitions"
        req = urllib.request.Request(trans_url)
        req.add_header("Authorization", f"Basic {auth_b64}")

        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            transitions = json.loads(resp.read().decode()).get("transitions", [])

        # Step 2: Find matching transition
        target = None
        for t in transitions:
            if t["name"].lower() == transition_name.lower():
                target = t
                break

        if not target:
            available = [t["name"] for t in transitions]
            return {
                "success": False,
                "error": f"Transition '{transition_name}' not found. Available: {available}"
            }

        # Step 3: Execute transition
        payload = json.dumps({"transition": {"id": target["id"]}}).encode()
        req = urllib.request.Request(trans_url, data=payload, method="POST")
        req.add_header("Authorization", f"Basic {auth_b64}")
        req.add_header("Content-Type", "application/json")

        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            resp.read()

        _log(log_callback, f"✓ {issue_key} transitioned to '{transition_name}'")
        return {"success": True}

    except Exception as e:
        _log(log_callback, f"✗ Transition failed: {e}")
        return {"success": False, "error": str(e)}


def get_jira_reporter(
    issue_key: str,
    jira_base_url: str,
    email: str,
    jira_token: str,
    log_callback: Optional[Callable] = None,
) -> Optional[str]:
    """
    Fetch the reporter's display name from a JIRA issue.

    Returns:
        Reporter display name string, or None on failure.
    """
    try:
        import base64
        auth_str = f"{email}:{jira_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        ctx = ssl._create_unverified_context()

        url = f"{jira_base_url}/rest/api/3/issue/{issue_key}?fields=reporter"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {auth_b64}")

        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        reporter = data.get("fields", {}).get("reporter", {})
        display_name = reporter.get("displayName", reporter.get("name", "Unknown"))
        _log(log_callback, f"  Reporter: {display_name}")
        return display_name

    except Exception as e:
        _log(log_callback, f"⚠ Could not fetch reporter: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# 2. GIT OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def git_add_commit_push(
    repo_path: str,
    commit_message: str,
    branch: Optional[str] = None,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Stage all changes, commit, and push to remote.

    Args:
        repo_path: Local git repository path.
        commit_message: Commit message string.
        branch: Branch name to push. If None, pushes current branch.

    Returns:
        dict with 'success', 'commit_hash', optional 'error'.
    """
    _log(log_callback, f"📤 Committing & pushing changes in {repo_path}...")

    try:
        # git add -A
        _run_git(["git", "add", "-A"], repo_path, log_callback)

        # Check if there are staged changes
        status = _run_git(["git", "status", "--porcelain"], repo_path, log_callback)
        if not status.strip():
            _log(log_callback, "ℹ No changes to commit")
            # Get current commit hash anyway
            commit_hash = _run_git(
                ["git", "rev-parse", "HEAD"], repo_path, log_callback
            ).strip()
            return {"success": True, "commit_hash": commit_hash, "no_changes": True}

        # git commit
        _run_git(
            ["git", "commit", "-m", commit_message],
            repo_path, log_callback
        )

        # Get commit hash
        commit_hash = _run_git(
            ["git", "rev-parse", "HEAD"], repo_path, log_callback
        ).strip()

        # git push
        push_cmd = ["git", "push", "origin"]
        if branch:
            push_cmd.append(branch)
        _run_git(push_cmd, repo_path, log_callback)

        _log(log_callback, f"✓ Pushed commit {commit_hash[:8]}")
        return {"success": True, "commit_hash": commit_hash}

    except subprocess.CalledProcessError as e:
        error_msg = e.output if e.output else str(e)
        _log(log_callback, f"✗ Git operation failed: {error_msg}")
        return {"success": False, "error": str(error_msg)}
    except Exception as e:
        _log(log_callback, f"✗ Git error: {e}")
        return {"success": False, "error": str(e)}


def git_get_current_branch(
    repo_path: str,
    log_callback: Optional[Callable] = None,
) -> Optional[str]:
    """Return the current branch name of the local repo."""
    try:
        branch = _run_git(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            repo_path, log_callback
        ).strip()
        return branch
    except Exception:
        return None


def git_get_diff_summary(
    repo_path: str,
    log_callback: Optional[Callable] = None,
) -> str:
    """Return a short diff stat summary for the working tree."""
    try:
        return _run_git(
            ["git", "diff", "--stat", "HEAD"],
            repo_path, log_callback
        ).strip()
    except Exception:
        return "(unable to get diff)"


def generate_commit_message(
    repo_path: str,
    issue_key: str,
    ai_client: Any,
    log_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Generate a commit message using AI based on the current git diff.

    Gets the actual diff content (truncated to ~4000 chars) and sends it
    to the AI Gateway to produce a concise, conventional commit message.

    Args:
        repo_path:    Path to the local git repository.
        issue_key:    JIRA issue key (e.g. "TSESSD-14270") to prefix the message.
        ai_client:    An ``AIGatewayClient`` instance with ``chat_completion()``.
        log_callback: Optional logging callback.

    Returns:
        ``{"success": True, "message": "<commit msg>"}`` or
        ``{"success": False, "error": "<reason>"}``.
    """
    try:
        # 1. Get the diff content (actual changes, not just stat)
        diff_text = _run_git(
            ["git", "diff", "HEAD"],
            repo_path, log_callback,
        ).strip()

        if not diff_text:
            # Try staged changes if working tree is clean
            diff_text = _run_git(
                ["git", "diff", "--cached"],
                repo_path, log_callback,
            ).strip()

        if not diff_text:
            return {"success": False, "error": "No changes detected in repository"}

        # 2. Also get the stat summary for context
        stat_text = _run_git(
            ["git", "diff", "--stat", "HEAD"],
            repo_path, log_callback,
        ).strip()

        # 3. Truncate diff to avoid exceeding token limits
        max_diff_chars = 4000
        truncated = len(diff_text) > max_diff_chars
        diff_snippet = diff_text[:max_diff_chars]
        if truncated:
            diff_snippet += "\n\n... (diff truncated)"

        # 4. Build the AI prompt
        prompt = (
            f"Generate a concise git commit message for the following changes.\n"
            f"The JIRA issue key is: {issue_key}\n\n"
            f"Rules:\n"
            f"- Start with [{issue_key}] prefix\n"
            f"- First line: max 72 characters, imperative mood (e.g. 'Add', 'Fix', 'Update')\n"
            f"- Optionally add a blank line followed by bullet points for details\n"
            f"- Be specific about what changed, not why\n"
            f"- Do NOT include markdown formatting or code fences\n"
            f"- Return ONLY the commit message text, nothing else\n\n"
            f"File change summary:\n{stat_text}\n\n"
            f"Diff:\n{diff_snippet}"
        )

        _log(log_callback, "🤖 Generating commit message with AI...")

        # 5. Call the AI Gateway
        result = ai_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            task_type="analysis",
        )

        if not result.get("success"):
            error = result.get("error", "Unknown AI error")
            return {"success": False, "error": f"AI generation failed: {error}"}

        # 6. Extract the generated message
        response = result.get("response", {})
        choices = response.get("choices", [])
        if not choices:
            return {"success": False, "error": "AI returned empty response"}

        message = choices[0].get("message", {}).get("content", "").strip()
        if not message:
            return {"success": False, "error": "AI returned empty message"}

        # Clean up: remove any markdown code fences the AI might add
        if message.startswith("```"):
            lines = message.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            message = "\n".join(lines).strip()

        _log(log_callback, f"✅ AI commit message generated ({len(message)} chars)")
        return {"success": True, "message": message}

    except Exception as e:
        _log(log_callback, f"⚠️ Commit message generation failed: {e}")
        return {"success": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
# 3. BITBUCKET PULL REQUEST OPERATIONS
# ══════════════════════════════════════════════════════════════════════════════

def search_bitbucket_users(
    query: str,
    bitbucket_base_url: str,
    bitbucket_username: str,
    bitbucket_token: str,
    limit: int = 50,
    log_callback: Optional[Callable] = None,
) -> List[Dict[str, str]]:
    """
    Search Bitbucket Server users by username, display name, or email.

    Uses Bitbucket REST API 1.0:
        GET /rest/api/1.0/users?filter=<query>&limit=<limit>

    Args:
        query:              Search string (min 3 chars recommended).
        bitbucket_base_url: e.g. "https://bitbucket.micron.com/bbdc/scm"
        bitbucket_username: Bitbucket username for auth.
        bitbucket_token:    Bitbucket personal access token.
        limit:              Max results to return (default 25).
        log_callback:       Optional logging callback.

    Returns:
        List of dicts: [{"username": "jdoe", "display_name": "John Doe",
                         "email": "jdoe@micron.com"}, ...]
        Returns empty list on error or if query is too short.
    """
    if not query or len(query) < 3:
        return []

    # Normalise base URL (strip trailing /scm if present)
    base = bitbucket_base_url.rstrip("/")
    if base.endswith("/scm"):
        base = base[:-4]

    url = f"{base}/rest/api/1.0/users?filter={urllib.parse.quote(query)}&limit={limit}"
    _log(log_callback, f"🔍 Bitbucket user search URL: {url}")

    try:
        import base64
        auth_string = f"{bitbucket_username}:{bitbucket_token}"
        auth_header = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

        headers = {
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/json",
        }

        req = urllib.request.Request(url, headers=headers)
        ssl_context = ssl._create_unverified_context()

        resp = urllib.request.urlopen(req, context=ssl_context, timeout=30)
        data = json.loads(resp.read().decode("utf-8"))

        users = []
        for u in data.get("values", []):
            users.append({
                "username": u.get("name", u.get("slug", "")),
                "display_name": u.get("displayName", ""),
                "email": u.get("emailAddress", ""),
            })

        _log(log_callback,
             f"🔍 Bitbucket user search '{query}': {len(users)} result(s)")
        return users

    except Exception as e:
        _log(log_callback,
             f"⚠️ Bitbucket user search failed: {type(e).__name__}: {e}")
        return []


def create_pull_request(
    repo_slug: str,
    project_key: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
    reviewers: List[str],
    bitbucket_base_url: str,
    bitbucket_username: str,
    bitbucket_token: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Create a pull request on Bitbucket Server.

    Uses Bitbucket REST API 1.0:
        POST /rest/api/1.0/projects/{projectKey}/repos/{repoSlug}/pull-requests

    Args:
        reviewers: List of Bitbucket usernames to add as reviewers.

    Returns:
        dict with 'success', 'pr_id', 'pr_url', optional 'error'.
    """
    _log(log_callback, f"🔀 Creating PR: {source_branch} → {target_branch}...")

    # Normalise base URL (strip trailing /scm if present)
    base = bitbucket_base_url.rstrip("/")
    if base.endswith("/scm"):
        base = base[:-4]

    url = f"{base}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}/pull-requests"

    payload = {
        "title": title,
        "description": description,
        "fromRef": {
            "id": f"refs/heads/{source_branch}",
            "repository": {
                "slug": repo_slug,
                "project": {"key": project_key}
            }
        },
        "toRef": {
            "id": f"refs/heads/{target_branch}",
            "repository": {
                "slug": repo_slug,
                "project": {"key": project_key}
            }
        },
        "reviewers": [{"user": {"name": r}} for r in reviewers],
    }

    try:
        import base64
        auth_str = f"{bitbucket_username}:{bitbucket_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        data = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {auth_b64}")
        req.add_header("Content-Type", "application/json")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            resp_data = json.loads(resp.read().decode())

        pr_id = resp_data.get("id", "")
        # Build browsable URL
        pr_url = (
            f"{base}/projects/{project_key}/repos/{repo_slug}"
            f"/pull-requests/{pr_id}"
        )

        _log(log_callback, f"✓ PR #{pr_id} created: {pr_url}")
        return {"success": True, "pr_id": pr_id, "pr_url": pr_url}

    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        _log(log_callback, f"✗ PR creation failed ({e.code}): {body}")
        return {"success": False, "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        _log(log_callback, f"✗ PR creation error: {e}")
        return {"success": False, "error": str(e)}


def get_pull_request_status(
    repo_slug: str,
    project_key: str,
    pr_id: int,
    bitbucket_base_url: str,
    bitbucket_username: str,
    bitbucket_token: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Check the status of a Bitbucket pull request.

    Returns:
        dict with 'state' (OPEN/MERGED/DECLINED), 'approved' (bool),
        'reviewers' list, 'merge_result' etc.
    """
    base = bitbucket_base_url.rstrip("/")
    if base.endswith("/scm"):
        base = base[:-4]

    url = (
        f"{base}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}"
        f"/pull-requests/{pr_id}"
    )

    try:
        import base64
        auth_str = f"{bitbucket_username}:{bitbucket_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {auth_b64}")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        state = data.get("state", "UNKNOWN")
        reviewers_info = []
        all_approved = True
        for r in data.get("reviewers", []):
            user = r.get("user", {}).get("name", "?")
            approved = r.get("approved", False)
            status = r.get("status", "UNAPPROVED")
            reviewers_info.append({
                "user": user,
                "approved": approved,
                "status": status,
            })
            if not approved:
                all_approved = False

        return {
            "success": True,
            "state": state,
            "approved": all_approved and len(reviewers_info) > 0,
            "reviewers": reviewers_info,
            "pr_id": pr_id,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


def merge_pull_request(
    repo_slug: str,
    project_key: str,
    pr_id: int,
    pr_version: int,
    bitbucket_base_url: str,
    bitbucket_username: str,
    bitbucket_token: str,
    log_callback: Optional[Callable] = None,
) -> Dict:
    """
    Merge an approved pull request on Bitbucket Server.

    Uses:
        POST /rest/api/1.0/projects/{projectKey}/repos/{repoSlug}/pull-requests/{prId}/merge

    Returns:
        dict with 'success'.
    """
    _log(log_callback, f"🔀 Merging PR #{pr_id}...")

    base = bitbucket_base_url.rstrip("/")
    if base.endswith("/scm"):
        base = base[:-4]

    url = (
        f"{base}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}"
        f"/pull-requests/{pr_id}/merge"
    )

    try:
        import base64
        auth_str = f"{bitbucket_username}:{bitbucket_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        payload = json.dumps({"version": pr_version}).encode()
        req = urllib.request.Request(url, data=payload, method="POST")
        req.add_header("Authorization", f"Basic {auth_b64}")
        req.add_header("Content-Type", "application/json")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
            resp.read()

        _log(log_callback, f"✓ PR #{pr_id} merged successfully")
        return {"success": True}

    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        _log(log_callback, f"✗ Merge failed ({e.code}): {body}")
        return {"success": False, "error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        _log(log_callback, f"✗ Merge error: {e}")
        return {"success": False, "error": str(e)}


def get_pr_version(
    repo_slug: str,
    project_key: str,
    pr_id: int,
    bitbucket_base_url: str,
    bitbucket_username: str,
    bitbucket_token: str,
) -> int:
    """Get the current version number of a PR (needed for merge)."""
    base = bitbucket_base_url.rstrip("/")
    if base.endswith("/scm"):
        base = base[:-4]

    url = (
        f"{base}/rest/api/1.0/projects/{project_key}/repos/{repo_slug}"
        f"/pull-requests/{pr_id}"
    )

    try:
        import base64
        auth_str = f"{bitbucket_username}:{bitbucket_token}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()

        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {auth_b64}")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        return data.get("version", 0)
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
# 4. TEAMS NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

def send_pr_review_notification(
    title: str,
    facts: List[Dict[str, str]],
    color: str,
    webhook_url: str,
    log_callback: Optional[Callable] = None,
) -> bool:
    """
    Send a Teams Adaptive Card notification for PR review events.

    Args:
        title: Card title (e.g. "PR Created", "PR Approved", "Stale PR Warning").
        facts: List of {"title": ..., "value": ...} dicts for the FactSet.
        color: "Good" | "Attention" | "Warning".
        webhook_url: Teams incoming webhook URL.

    Returns:
        True if sent successfully.
    """
    if not webhook_url:
        _log(log_callback, "⚠ Teams webhook URL not configured — skipping notification.")
        return False

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        facts_with_time = facts + [{"title": "Time", "value": timestamp}]

        adaptive_card = {
            "type": "AdaptiveCard",
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "size": "Medium",
                    "weight": "Bolder",
                    "text": title,
                    "style": "heading",
                    "color": color,
                },
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": f.get("title", ""), "value": f.get("value", "")}
                        for f in facts_with_time
                    ],
                },
            ],
        }

        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": adaptive_card,
                }
            ],
        }

        data = json.dumps(payload).encode()
        req = urllib.request.Request(webhook_url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")

        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            resp.read()

        _log(log_callback, f"✓ Teams notification sent: {title}")
        return True

    except Exception as e:
        _log(log_callback, f"⚠ Teams notification failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# 5. PIPELINE ORCHESTRATION (full end-to-end)
# ══════════════════════════════════════════════════════════════════════════════

def run_full_pr_pipeline(
    issue_key: str,
    repo_path: str,
    repo_slug: str,
    project_key: str,
    source_branch: str,
    target_branch: str,
    reviewers: List[str],
    commit_message: str,
    validation_doc_path: Optional[str],
    jira_base_url: str,
    email: str,
    jira_token: str,
    bitbucket_base_url: str,
    bitbucket_username: str,
    bitbucket_token: str,
    webhook_url: str = "",
    transition_name: str = JIRA_TRANSITION_DONE,
    auto_merge: bool = False,
    auto_close_jira: bool = False,
    log_callback: Optional[Callable] = None,
    phase_callback: Optional[Callable] = None,
    cancel_event=None,
) -> Dict:
    """
    Execute the full PR review pipeline:

      Phase 1: Attach validation doc to JIRA + tag requestor
      Phase 2: Commit & push code changes
      Phase 3: Create pull request
      Phase 4: Poll for PR approval (with stale notifications)
      Phase 5: Merge PR (if auto_merge)
      Phase 6: Close JIRA (if auto_close_jira)

    Args:
        cancel_event: threading.Event — set to cancel the pipeline.
        phase_callback: callable(phase_name, detail) for UI updates.

    Returns:
        dict with 'success', 'pr_id', 'pr_url', 'phases_completed', etc.
    """
    phases_completed = []
    result = {"success": False, "phases_completed": phases_completed}

    def _phase(name, detail=""):
        if phase_callback:
            phase_callback(name, detail)
        _log(log_callback, f"\n{'='*60}")
        _log(log_callback, f"  PHASE: {name}")
        if detail:
            _log(log_callback, f"  {detail}")
        _log(log_callback, f"{'='*60}")

    def _cancelled():
        return cancel_event and cancel_event.is_set()

    # ── Phase 1: Attach validation doc & tag requestor ─────────────────────
    _phase("1/6 — Attach Validation Doc", f"File: {validation_doc_path or 'N/A'}")

    if validation_doc_path and os.path.exists(validation_doc_path):
        attach_result = attach_file_to_jira(
            issue_key, validation_doc_path,
            jira_base_url, email, jira_token, log_callback
        )
        if not attach_result["success"]:
            _log(log_callback, f"⚠ Attachment failed (non-fatal): {attach_result.get('error')}")
        else:
            phases_completed.append("attach_validation")

        # Tag requestor
        reporter = get_jira_reporter(
            issue_key, jira_base_url, email, jira_token, log_callback
        )
        comment_text = (
            f"[BENTO] Validation document attached for review.\n"
            f"Requestor: {reporter or 'N/A'}\n"
            f"Please review and approve to proceed with code deployment."
        )
        add_jira_comment(
            issue_key, comment_text,
            jira_base_url, email, jira_token, log_callback
        )

        # Teams notification
        send_pr_review_notification(
            title="📋 Validation Doc Attached",
            facts=[
                {"title": "JIRA", "value": issue_key},
                {"title": "Requestor", "value": reporter or "N/A"},
                {"title": "Status", "value": "Awaiting review"},
            ],
            color="Default",
            webhook_url=webhook_url,
            log_callback=log_callback,
        )
    else:
        _log(log_callback, "ℹ No validation doc to attach — skipping Phase 1")
        phases_completed.append("attach_validation_skipped")

    if _cancelled():
        result["error"] = "Cancelled by user"
        return result

    # ── Phase 2: Commit & push ─────────────────────────────────────────────
    _phase("2/6 — Commit & Push", f"Branch: {source_branch}")

    push_result = git_add_commit_push(
        repo_path, commit_message, source_branch, log_callback
    )
    if not push_result["success"]:
        result["error"] = f"Git push failed: {push_result.get('error')}"
        send_pr_review_notification(
            title="❌ Push Failed",
            facts=[
                {"title": "JIRA", "value": issue_key},
                {"title": "Branch", "value": source_branch},
                {"title": "Error", "value": push_result.get("error", "Unknown")},
            ],
            color="Attention",
            webhook_url=webhook_url,
            log_callback=log_callback,
        )
        return result

    phases_completed.append("commit_push")
    commit_hash = push_result.get("commit_hash", "")

    send_pr_review_notification(
        title="✅ Code Pushed",
        facts=[
            {"title": "JIRA", "value": issue_key},
            {"title": "Branch", "value": source_branch},
            {"title": "Commit", "value": commit_hash[:8] if commit_hash else "N/A"},
        ],
        color="Good",
        webhook_url=webhook_url,
        log_callback=log_callback,
    )

    if _cancelled():
        result["error"] = "Cancelled by user"
        return result

    # ── Phase 3: Create pull request ───────────────────────────────────────
    _phase("3/6 — Create Pull Request",
           f"{source_branch} → {target_branch}")

    pr_title = f"[{issue_key}] {commit_message}"
    pr_description = (
        f"**JIRA:** {issue_key}\n"
        f"**Branch:** {source_branch} → {target_branch}\n"
        f"**Commit:** {commit_hash[:8] if commit_hash else 'N/A'}\n\n"
        f"Auto-generated by BENTO PR Review pipeline."
    )

    pr_result = create_pull_request(
        repo_slug, project_key, source_branch, target_branch,
        pr_title, pr_description, reviewers,
        bitbucket_base_url, bitbucket_username, bitbucket_token,
        log_callback
    )

    if not pr_result["success"]:
        result["error"] = f"PR creation failed: {pr_result.get('error')}"
        send_pr_review_notification(
            title="❌ PR Creation Failed",
            facts=[
                {"title": "JIRA", "value": issue_key},
                {"title": "Error", "value": pr_result.get("error", "Unknown")},
            ],
            color="Attention",
            webhook_url=webhook_url,
            log_callback=log_callback,
        )
        return result

    pr_id = pr_result["pr_id"]
    pr_url = pr_result["pr_url"]
    result["pr_id"] = pr_id
    result["pr_url"] = pr_url
    phases_completed.append("create_pr")

    # Add PR link as JIRA comment
    add_jira_comment(
        issue_key,
        f"[BENTO] Pull Request created: PR #{pr_id}\n{pr_url}\nReviewers: {', '.join(reviewers)}",
        jira_base_url, email, jira_token, log_callback
    )

    send_pr_review_notification(
        title="🔀 Pull Request Created",
        facts=[
            {"title": "JIRA", "value": issue_key},
            {"title": "PR", "value": f"#{pr_id}"},
            {"title": "URL", "value": pr_url},
            {"title": "Reviewers", "value": ", ".join(reviewers)},
        ],
        color="Default",
        webhook_url=webhook_url,
        log_callback=log_callback,
    )

    if _cancelled():
        result["error"] = "Cancelled by user"
        return result

    # ── Phase 4: Poll for PR approval ──────────────────────────────────────
    _phase("4/6 — Awaiting PR Approval", f"PR #{pr_id}")

    start_time = time.time()
    stale_notified = False

    while True:
        if _cancelled():
            result["error"] = "Cancelled by user"
            return result

        elapsed = int(time.time() - start_time)

        pr_status = get_pull_request_status(
            repo_slug, project_key, pr_id,
            bitbucket_base_url, bitbucket_username, bitbucket_token,
            log_callback
        )

        if not pr_status.get("success"):
            _log(log_callback, f"⚠ Could not check PR status: {pr_status.get('error')}")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        state = pr_status["state"]

        if state == "MERGED":
            _log(log_callback, f"✓ PR #{pr_id} already merged externally")
            phases_completed.append("pr_approved")
            phases_completed.append("pr_merged")
            break

        if state == "DECLINED":
            result["error"] = f"PR #{pr_id} was declined"
            send_pr_review_notification(
                title="❌ PR Declined",
                facts=[
                    {"title": "JIRA", "value": issue_key},
                    {"title": "PR", "value": f"#{pr_id}"},
                    {"title": "Elapsed", "value": f"{elapsed // 60}m"},
                ],
                color="Attention",
                webhook_url=webhook_url,
                log_callback=log_callback,
            )
            return result

        if pr_status["approved"]:
            _log(log_callback, f"✓ PR #{pr_id} approved!")
            phases_completed.append("pr_approved")

            send_pr_review_notification(
                title="✅ PR Approved",
                facts=[
                    {"title": "JIRA", "value": issue_key},
                    {"title": "PR", "value": f"#{pr_id}"},
                    {"title": "Elapsed", "value": f"{elapsed // 60}m {elapsed % 60}s"},
                ],
                color="Good",
                webhook_url=webhook_url,
                log_callback=log_callback,
            )
            break

        # Stale notification
        if not stale_notified and elapsed > PR_STALE_TIMEOUT_SECONDS:
            stale_notified = True
            reviewers_status = ", ".join(
                f"{r['user']}({'✓' if r['approved'] else '⏳'})"
                for r in pr_status.get("reviewers", [])
            )
            send_pr_review_notification(
                title="⏰ PR Awaiting Review (Stale)",
                facts=[
                    {"title": "JIRA", "value": issue_key},
                    {"title": "PR", "value": f"#{pr_id}"},
                    {"title": "Waiting", "value": f"{elapsed // 60}m"},
                    {"title": "Reviewers", "value": reviewers_status},
                ],
                color="Warning",
                webhook_url=webhook_url,
                log_callback=log_callback,
            )

        if phase_callback:
            reviewers_str = ", ".join(
                f"{r['user']}({'✓' if r['approved'] else '⏳'})"
                for r in pr_status.get("reviewers", [])
            )
            phase_callback(
                "4/6 — Awaiting PR Approval",
                f"PR #{pr_id} | {elapsed // 60}m elapsed | {reviewers_str}"
            )

        time.sleep(POLL_INTERVAL_SECONDS)

    if _cancelled():
        result["error"] = "Cancelled by user"
        return result

    # ── Phase 5: Merge PR ──────────────────────────────────────────────────
    if auto_merge and "pr_merged" not in phases_completed:
        _phase("5/6 — Merging PR", f"PR #{pr_id}")

        pr_version = get_pr_version(
            repo_slug, project_key, pr_id,
            bitbucket_base_url, bitbucket_username, bitbucket_token
        )

        merge_result = merge_pull_request(
            repo_slug, project_key, pr_id, pr_version,
            bitbucket_base_url, bitbucket_username, bitbucket_token,
            log_callback
        )

        if merge_result["success"]:
            phases_completed.append("pr_merged")
            send_pr_review_notification(
                title="🔀 PR Merged",
                facts=[
                    {"title": "JIRA", "value": issue_key},
                    {"title": "PR", "value": f"#{pr_id}"},
                    {"title": "Target", "value": target_branch},
                ],
                color="Good",
                webhook_url=webhook_url,
                log_callback=log_callback,
            )
        else:
            _log(log_callback, f"⚠ Auto-merge failed: {merge_result.get('error')}")
    else:
        _log(log_callback, "ℹ Auto-merge disabled or PR already merged — skipping Phase 5")
        phases_completed.append("merge_skipped")

    if _cancelled():
        result["error"] = "Cancelled by user"
        return result

    # ── Phase 6: Close JIRA ────────────────────────────────────────────────
    if auto_close_jira:
        _phase("6/6 — Closing JIRA", f"{issue_key} → '{transition_name}'")

        close_result = transition_jira_issue(
            issue_key, transition_name,
            jira_base_url, email, jira_token, log_callback
        )

        if close_result["success"]:
            phases_completed.append("jira_closed")
            send_pr_review_notification(
                title="🎉 Release Complete",
                facts=[
                    {"title": "JIRA", "value": issue_key},
                    {"title": "Status", "value": transition_name},
                    {"title": "PR", "value": f"#{pr_id}"},
                ],
                color="Good",
                webhook_url=webhook_url,
                log_callback=log_callback,
            )
        else:
            _log(log_callback,
                 f"⚠ JIRA transition failed: {close_result.get('error')}")
    else:
        _log(log_callback, "ℹ Auto-close JIRA disabled — skipping Phase 6")
        phases_completed.append("jira_close_skipped")

    result["success"] = True
    result["phases_completed"] = phases_completed
    _log(log_callback, f"\n✅ PR Review pipeline complete — {len(phases_completed)} phases done")
    return result


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _log(callback: Optional[Callable], msg: str):
    if callback:
        callback(msg)
    else:
        logger.info(msg)


def _run_git(cmd: list, cwd: str, log_callback: Optional[Callable] = None) -> str:
    """Run a git command and return stdout."""
    _log(log_callback, f"  $ {' '.join(cmd)}")
    result = subprocess.run(
        cmd, cwd=cwd,
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd,
            output=result.stdout + result.stderr
        )
    return result.stdout
