"""Independent VPS watchdog for the Yapfast Windows scheduler.

Run every five minutes with a systemd user timer. Uses only the Python stdlib.
A fine-grained GitHub token with Actions: write on the scheduler repo is needed
to dispatch a workflow; public monitoring continues without that token.
"""
from __future__ import annotations

import json
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UPSTREAM = "yapfast/yapfast"
FORK = "linjeremy670/yapfast"
SCHEDULER = "linjeremy670/yapfast-build-scheduler"
WORKFLOW = ".github/workflows/windows.yml"
REQUIRED_ASSETS = {"SHA256SUMS", "yapfast-windows-x64.exe"}
STATE_PATH = Path.home() / ".local/state/yapfast-fallback/state.json"
TOKEN_PATH = Path.home() / ".config/yapfast-fallback/github-token"
WEBHOOK_PATH = Path.home() / ".config/yapfast-fallback/discord-webhook"
MAX_CHECK_AGE = timedelta(minutes=20)
RECENT_RUN_AGE = timedelta(minutes=12)
DISPATCH_COOLDOWN = timedelta(minutes=20)
ALERT_AGE = timedelta(hours=1)
ALERT_COOLDOWN = timedelta(hours=6)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def request_json(path: str, token: str | None = None, payload: dict | None = None):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "yapfast-independent-watchdog",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(f"https://api.github.com{path}", headers=headers, data=data)
    with urlopen(request, timeout=20) as response:
        body = response.read()
    return json.loads(body) if body else None


def read_secret(path: Path) -> str | None:
    if not path.exists():
        return None
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PermissionError(f"{path}: expected file mode 0600")
    return path.read_text(encoding="utf-8").strip() or None


def read_public_api_token() -> str | None:
    """Use the existing read-only gh login without printing its credentials."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(STATE_PATH)


def complete_release_exists(releases: list[dict], sha: str) -> bool:
    prefix = f"windows-{sha}-"
    for release in releases:
        if release.get("draft") or not release["tag_name"].startswith(prefix):
            continue
        assets = {
            item["name"] for item in release.get("assets", [])
            if item.get("state") == "uploaded" and item.get("size", 0) > 0
        }
        if REQUIRED_ASSETS.issubset(assets):
            return True
    return False


def scheduler_runs(runs: list[dict]) -> list[dict]:
    return [run for run in runs if run.get("path") == WORKFLOW]


def should_dispatch(
    now: datetime,
    needs_build: bool,
    runs: list[dict],
    last_dispatch: datetime | None,
) -> bool:
    if any(run["status"] != "completed" for run in runs):
        return False
    if last_dispatch and now - last_dispatch < DISPATCH_COOLDOWN:
        return False
    latest = max((parse_time(run["created_at"]) for run in runs), default=None)
    age = now - latest if latest else timedelta(days=1)
    return age >= (RECENT_RUN_AGE if needs_build else MAX_CHECK_AGE)


def post_discord(webhook: str, content: str) -> None:
    body = json.dumps({
        "content": content[:1900],
        "allowed_mentions": {"parse": []},
    }).encode("utf-8")
    request = Request(
        webhook, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "YapfastWatchdog/1.0"},
    )
    with urlopen(request, timeout=15):
        pass


def maybe_alert(now: datetime, state: dict, sha: str, webhook: str | None) -> None:
    first_seen = parse_time(state.get("first_seen"))
    last_alert = parse_time(state.get("last_alert"))
    if not first_seen or now - first_seen < ALERT_AGE:
        return
    if last_alert and now - last_alert < ALERT_COOLDOWN:
        return
    url = f"https://github.com/{SCHEDULER}/actions/workflows/windows.yml"
    message = f"Yapfast Windows is over 1 hour behind upstream {sha[:8]}. {url}"
    if webhook:
        post_discord(webhook, message)
        state["last_alert"] = now.isoformat()
        print("Sent stale-build Discord alert.")
    elif not state.get("missing_webhook_warning"):
        print(f"ALERT (no Discord webhook configured): {message}", file=sys.stderr)
        state["missing_webhook_warning"] = True


def main() -> int:
    now = utc_now()
    token = read_secret(TOKEN_PATH)
    webhook = read_secret(WEBHOOK_PATH)
    api_token = read_public_api_token() or token
    upstream = request_json(f"/repos/{UPSTREAM}/commits/main", api_token)["sha"]
    fork = request_json(f"/repos/{FORK}/commits/main", api_token)["sha"]
    releases = request_json(f"/repos/{FORK}/releases?per_page=100", api_token)
    all_runs = request_json(
        f"/repos/{SCHEDULER}/actions/runs?per_page=30", api_token
    )["workflow_runs"]
    runs = scheduler_runs(all_runs)
    state = read_state()
    if state.get("upstream_sha") != upstream:
        state = {"upstream_sha": upstream, "first_seen": now.isoformat()}

    released = complete_release_exists(releases, upstream)
    needs_build = upstream != fork or not released
    print(f"Upstream={upstream[:8]} fork={fork[:8]} released={released} "
          f"active_checks={sum(run['status'] != 'completed' for run in runs)}")

    if not needs_build:
        state.pop("last_alert", None)
        state.pop("missing_webhook_warning", None)

    last_dispatch = parse_time(state.get("last_dispatch"))
    if should_dispatch(now, needs_build, runs, last_dispatch):
        if token:
            request_json(
                f"/repos/{SCHEDULER}/actions/workflows/windows.yml/dispatches",
                token=token,
                payload={"ref": "main", "inputs": {"force_build": "false"}},
            )
            state["last_dispatch"] = now.isoformat()
            print("Dispatched Windows scheduler fallback.")
        elif not state.get("missing_token_warning"):
            print(f"Fallback not armed: add a 0600 Actions-write token at {TOKEN_PATH}.",
                  file=sys.stderr)
            state["missing_token_warning"] = True

    if needs_build:
        maybe_alert(now, state, upstream, webhook)
    write_state(state)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HTTPError, URLError, TimeoutError, ValueError, PermissionError) as error:
        print(f"Yapfast watchdog error: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
