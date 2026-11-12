# Yapfast Windows build scheduler

This repository owns the six-hour GitHub Actions schedule. It calls the reusable workflow on `linjeremy670/yapfast@automation/windows-build`. Yapfast's `main` stays identical to upstream.

## Required Actions secret

Create a fine-grained GitHub personal access token scoped **only** to `linjeremy670/yapfast` with **Contents: Read and write** (for branch updates and releases), **Actions: Read and write** (for legacy artifact cleanup), and **Metadata: Read** (implicit). Store it as `YAPFAST_TOKEN` under **Settings → Secrets and variables → Actions** in this scheduler repository. Never commit the token.

Optional: store `DISCORD_WEBHOOK_URL` as an Actions secret to enable build notifications.

## Test

Open **Actions → Yapfast Windows Scheduler → Run workflow**. First run should sync upstream into `automation/windows-build`, compile Windows x64 only if the commit lacks a release, then publish the executable and checksum in the Yapfast fork.

The workflow attempts to install LLVM 21.1.7. Check the first run's **Install LLVM 21 for Skia symbol isolation** step: the pinned Chocolatey version may need adjustment if it is unavailable on the runner.

A second run on the same upstream commit should skip compilation once a release with `yapfast-windows-x64.exe` exists.

## Safety

- Never modify `linjeremy670/yapfast:main`; it must match `yapfast/yapfast:main`.
- Keep custom workflow commits on `automation/windows-build`.
- No force pushes in the scheduler.
- Schedule: every six hours (UTC), subject to GitHub Actions scheduling delays.
