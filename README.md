# Yapfast Windows build scheduler

This repository checks upstream Yapfast and calls the reusable Windows builder
in linjeremy670/yapfast on the automation/windows-build branch. The fork's
main branch is only fast-forwarded to exact upstream commits. A build is skipped
if a complete Windows release already exists for the current commit.

## Scheduling and recovery

GitHub Actions cron is best-effort, not a guaranteed 15-minute timer.
The windows.yml workflow supports schedule, workflow_dispatch and
repository_dispatch with event type yapfast-fallback-check.

The independent VPS watchdog (scripts/yapfast_fallback.py) uses a systemd
user timer every five minutes. It checks upstream/fork SHAs, complete releases
and active scheduler runs. When a Windows build is needed and no scheduler check ran in the past 12
minutes, it dispatches a fallback run. It never dispatches solely because
GitHub cron is late while the current commit already has a complete release.
A 20-minute cooldown and active-run check prevent redundant queues.

If an upstream commit has gone more than an hour since first observation
without a complete Windows release, the VPS watchdog can alert via Discord,
with a six-hour per-commit cooldown. The GitHub-hosted health workflow also
checks release freshness every 30 minutes; its alerts may be delayed by
GitHub's cron delivery.

## GitHub Actions secrets

In this scheduler repository:

- YAPFAST_TOKEN: fine-grained token for linjeremy670/yapfast with Contents
  read/write (for fork sync and releases) and Actions read/write (cleanup).
- DISCORD_WEBHOOK_URL: optional release and builder health notifications.

The scheduler's precheck uses its own read-only GITHUB_TOKEN.
The fork write token is passed only to the reusable build workflow.

## Activate the independent VPS fallback

Create a separate fine-grained PAT owned by an account with access to
linjeremy670/yapfast-build-scheduler. Restrict it to that repository with
Actions read/write and implicit Metadata read. Contents write is not required.

As the catsnake user, install these files on the VPS:

- scripts/yapfast_fallback.py to ~/.local/lib/yapfast-fallback/yapfast_fallback.py
- systemd/yapfast-fallback.service to ~/.config/systemd/user/
- systemd/yapfast-fallback.timer to ~/.config/systemd/user/

Set the containing directory ~/.config/yapfast-fallback to mode 0700.
Create ~/.config/yapfast-fallback/github-token with mode 0600, containing
only the new dispatch PAT. Optionally create a mode-0600
~/.config/yapfast-fallback/discord-webhook file with the Discord webhook URL.

Then run:

    systemctl --user daemon-reload
    systemctl --user enable --now yapfast-fallback.timer
    systemctl --user start yapfast-fallback.service
    systemctl --user list-timers yapfast-fallback.timer
    journalctl --user -u yapfast-fallback.service -n 40 --no-pager

Read-only GitHub API polling automatically reuses an existing GitHub CLI login,
if available, to avoid low anonymous API rate limits. This login is never used
for dispatch unless the separate PAT is explicitly provided.

Without the PAT, the watchdog stays in read-only mode and logs that
the fallback is not armed. An unrelated GitHub CLI account will not work
without Actions-write permission on the scheduler repository.

## Health and release safety

- Syncs fork main to the exact upstream commit with fast-forward checks.
- Checks releases before and after the Windows build queue.
- Verifies the Windows executable SHA256 before publication.
- Retains three complete Windows binary releases; old metadata may remain
  as empty historical releases after executable assets have been pruned.
- scripts/builder_health.py detects late scheduler checks *when a build is
  pending*, failed checks, stalled jobs, mismatched fork SHA and missing
  upstream releases after one hour. Delayed idle cron runs are not warnings.
- tests/test_builder_monitoring.py covers release and cooldown logic.
- Validate Windows Workflows runs actionlint and Python unit tests.

## Rust caching

Cargo cache exact-key hit false does not imply a full miss: rust-cache can
restore an older compatible dependency cache. Release diagnostics now make
the exact-match distinction. LLVM and Skia binary caches are also used.

The reusable Windows workflow supports opt-in enable_sccache, disabled by
default, to compare compiler-cache statistics on a forced rebuild. Do not
enable routinely until build duration and cache-quota impact are measured.

## Manual recovery

Open https://github.com/linjeremy670/yapfast-build-scheduler/actions/workflows/windows.yml
and select Run workflow. Leave force_build disabled for a regular sync check,
or enable it to rebuild an existing commit.
