"""Daily health check for the Yapfast Windows build pipeline."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCHEDULER = "linjeremy670/yapfast-build-scheduler"
YAPFAST = "linjeremy670/yapfast"
RELEASE_TAG = re.compile(r"windows-([0-9a-f]{40})-[0-9]+-[0-9]+$")
RELEASE_ASSETS = {"yapfast-windows-x64.exe", "SHA256SUMS"}
LEGACY_ORPHAN_RUN = 43197649420


def github_data(path: str) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "yapfast-builder-health",
    }
    if token := os.getenv("GH_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com{path}", headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def api_runs(repository: str) -> list[dict]:
    return github_data(f"/repos/{repository}/actions/runs?per_page=100")["workflow_runs"]


def parse_utc(timestamp: str | None) -> datetime | None:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")) if timestamp else None


def fresh_runs(runs: list[dict], cutoff: datetime) -> list[dict]:
    return [run for run in runs if parse_utc(run["created_at"]) >= cutoff]


def complete_windows_release(release: dict) -> bool:
    if not RELEASE_TAG.fullmatch(release["tag_name"]):
        return False
    return RELEASE_ASSETS.issubset({asset["name"] for asset in release["assets"]})


def get_latest_release() -> dict | None:
    items = github_data(f"/repos/{YAPFAST}/releases?per_page=100")
    complete = [item for item in items if complete_windows_release(item)]
    return max(complete, key=lambda item: item["published_at"], default=None)


def get_stale_runs(runs: list[dict], now: datetime) -> list[dict]:
    return [
        run for run in runs
        if run["status"] == "queued"
        and now - parse_utc(run["created_at"]) > timedelta(minutes=90)
    ]


def latest_scheduled_run(runs: list[dict]) -> dict | None:
    scheduled = [
        run for run in runs
        if run["name"] == "Yapfast Windows Scheduler" and run["event"] == "schedule"
    ]
    return max(scheduled, key=lambda run: run["created_at"], default=None)


def collect_health(now: datetime) -> dict:
    scheduler_runs = api_runs(SCHEDULER)
    yapfast_runs = api_runs(YAPFAST)
    cutoff = now - timedelta(hours=24)
    releases = github_data(f"/repos/{YAPFAST}/releases?per_page=100")
    return {
        "now": now,
        "scheduler": scheduler_runs,
        "yapfast": yapfast_runs,
        "recent": fresh_runs(scheduler_runs + yapfast_runs, cutoff),
        "new_releases": sum(complete_windows_release(item) and parse_utc(item["published_at"]) >= cutoff for item in releases),
        "latest": max((item for item in releases if complete_windows_release(item)), key=lambda r: r["published_at"], default=None),
        "main": github_data(f"/repos/{YAPFAST}/branches/main")["commit"]["sha"],
        "cache": github_data(f"/repos/{SCHEDULER}/actions/cache/usage"),
        "artifacts": github_data(f"/repos/{SCHEDULER}/actions/artifacts?per_page=1")["total_count"],
    }


def check_problems(data: dict) -> list[str]:
    now = data["now"]
    scheduled = latest_scheduled_run(data["scheduler"])
    problems = []
    if scheduled is None or scheduled["conclusion"] != "success":
        problems.append("Latest scheduled build check is missing or unsuccessful")
    elif now - parse_utc(scheduled["created_at"]) > timedelta(hours=9):
        problems.append("No scheduled build check started in the last nine hours")
    if not data["latest"] or data["main"] not in data["latest"]["tag_name"]:
        problems.append("Current fork main lacks a matching complete Windows release")
    for run in get_stale_runs(data["scheduler"] + data["yapfast"], now):
        if run["id"] != LEGACY_ORPHAN_RUN:
            problems.append(f"Queued for over 90 minutes: {run['html_url']}")
    return problems


def report(data: dict, problems: list[str]) -> str:
    counts = Counter(run["name"] for run in data["recent"])
    failures = [run for run in data["recent"] if run["conclusion"] in {"failure", "startup_failure", "timed_out"}]
    status = "WARNING" if problems else "HEALTHY"
    release = data["latest"]
    latest_sha = release["tag_name"].split("-")[1][:8] if release else "none"
    lines = [
        f"**Yapfast Windows builder: {status}** ({data['now']:%Y-%m-%d} UTC)",
        f"Fork main: `{data['main'][:8]}` | Latest complete release: `{latest_sha}`",
        f"Past 24h: {counts['Yapfast Windows Scheduler']} scheduler checks, "
        f"{data['new_releases']} complete release(s), "
        f"{counts['Validate Windows Workflows']} validation(s), {len(failures)} failed run(s)",
        f"Cache: {data['cache']['active_caches_size_in_bytes'] / 1024**3:.2f} GiB "
        f"({data['cache']['active_caches_count']} caches) | Artifacts: {data['artifacts']}",
    ]
    lines.extend(f"Warning: {problem}" for problem in problems)
    if any(run["id"] == LEGACY_ORPHAN_RUN for run in data["scheduler"]):
        lines.append("Note: An old canceled-workflow orphan remains queued (GitHub returns HTTP 409); no job is running.")
    lines.append(f"Actions: https://github.com/{SCHEDULER}/actions")
    return "\n".join(lines)


def publish_summary(message: str) -> None:
    print(message)
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(message + "\n")


def post_discord(message: str) -> None:
    webhook = os.getenv("DISCORD_WEBHOOK_URL")
    if not webhook:
        print("::warning::DISCORD_WEBHOOK_URL not set; GitHub summary only")
        return
    body = json.dumps({"content": message[:1900], "allowed_mentions": {"parse": []}}).encode()
    request = Request(webhook, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "DiscordBot (https://github.com/linjeremy670/yapfast-build-scheduler, 1.0)",
    })
    try:
        with urlopen(request, timeout=15):
            pass
    except HTTPError as error:
        print(f"::warning::Discord builder health webhook returned HTTP {error.code}")
    except Exception as error:
        print(f"::warning::Unable to send Discord builder health summary: {type(error).__name__}")


def main() -> None:
    data = collect_health(datetime.now(timezone.utc))
    message = report(data, check_problems(data))
    publish_summary(message)
    post_discord(message)


if __name__ == "__main__":
    main()
