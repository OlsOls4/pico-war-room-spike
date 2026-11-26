# pico-war-room-spike

Pico Space Program — **War-Room** spike. War-Room is the confirmed pick: a solo
on-call engineer's incident room in space, where an AI agent triages 200 log
lines down to three causes with evidence pinned to each, and time is a walkable
axis. (Full council record: see the ideation folder in the parent workspace.)

## What this repo is for

Step 0 host validation: the PICO toolchain refuses Linux entirely, and the
build VM has no KVM, so the PICO emulator cannot run locally. This repo runs
Step 0 on a standard GitHub-hosted macOS runner instead.

- Workflow: [`.github/workflows/step0-spike.yml`](.github/workflows/step0-spike.yml)
  — installs PICO CLI 0.7.0, runs `pico-cli setup`, installs the PICO emulator
  bundle, creates and boots an AVD, then builds, installs and launches the
  official **WelcomeSpace** sample (Apache 2.0) on the emulator.
- `w1/` — the frozen incident bundle (200 seeded log lines, seed 4815) and the
  deterministic scorer. 11/11 tests pass; re-running the generator reproduces
  the bundle byte-identically (md5 `bd57f95c…`). No network, no keys, no LLM.

## Cost: $0

Standard GitHub-hosted runners are **free and unlimited on public
repositories** (docs.github.com, verified 26 Nov 2026). This repo must stay
public. Do not use larger-runner labels — those are billed even on public
repos.

## Status

- 26 Nov 2026: Step 0 BLOCKED locally (Linux refused by Primer; no KVM).
  Workflow prepared and verified as far as possible without a repo: CLI
  commands confirmed from `pico-cli --help`, WelcomeSpace package/activity/
  dependencies verified from the public sample repo, runner specs and free
  minutes verified from docs.github.com.
- **Blocked on:** creating this repo — the GitHub App token cannot create
  repositories or forks (403 "Resource not accessible by integration").
  Creating the empty public repo `pico-war-room-spike` under `OlsOls4` via
  github.com/new, then pushing this content, is the one remaining step.
  Once pushed, the workflow runs on the push itself.
