---
name: ci
description: "SPHinXsim CI workflow and test-coverage guidance. Invoke when editing or debugging GitHub Actions workflows, CI scripts, or platform-specific test execution."
---

# SPHinXsim CI

## Purpose

Use this skill when changing or diagnosing CI workflows, CI helper scripts, or
test execution across supported platforms.

## Invocation Triggers

- The task modifies `.github/workflows/`, `.github/scripts/`, or CI configuration.
- The task investigates a CI failure or asks which platforms execute a test.
- The task changes test setup, platform-specific dependencies, or CI coverage.

## Workflow overview

The primary workflow is `.github/workflows/ci.yml`. It has Linux, Windows, and
macOS build-and-test jobs, plus a Linux SYCL job and wheel smoke-test jobs.
Check the current workflow before relying on this summary; job coverage and
platforms can change.

The Linux build-and-test job runs the broad Python unit and example suites and
has a separate desktop-graphics step. That step enables native desktop tests
and runs these files under Xvfb with software rendering:

- `tests/runtime/test_desktop.py`
- `tests/test_agent_preview.py`
- `tests/test_workbench.py`
- `tests/test_particle_visibility.py`
- `tests/test_visualization.py`
- `tests/test_cli_templates_results.py`
- `tests/test_shell_agent_hardening.py`

The Linux SYCL job runs the unit and example suites but does not currently have
that dedicated Xvfb desktop-graphics step.

Windows and macOS run their unit and example suites without that dedicated
desktop-graphics step. All four build-and-test jobs install the visualization
extra; Windows also smoke-tests importing `pyvistaqt.BackgroundPlotter`. Do not
describe the general test-suite runs or the Windows import smoke test as
equivalent to the Linux native desktop and screenshot coverage.

## Coverage invariants

- When changing, renaming, or adding desktop/screenshot tests, check that the
  Linux Xvfb command includes the intended tests and that the required desktop
  environment variables and software-rendering setup remain present.
- Keep headless or mocked graphics tests in the general test suite when they
  are suitable for cross-platform execution. Tests requiring a real Qt display
  must have an explicit display setup in each CI job expected to run them.
- Audit tests that construct `pyvista.Plotter` or another real VTK render
  window. Gate each such test with the suite's `requires_desktop` marker, which
  skips it unless `SPHINXSIM_DESKTOP_TESTS=1`; `off_screen=True` is not a
  substitute for this gate because PyVista/VTK may initialize OpenGL during
  plotter construction and abort before pytest can catch an error. Keep these
  tests in the ordinary test commands so they report as skipped, and include
  their files in the Linux Xvfb desktop step so they run there. Apply the same
  rule to any other CI job that runs tests without a display.
- Install the `chatbot` extra in jobs that run the real workbench desktop smoke
  test: its embedded Chainlit server starts in a child process using the same
  Python environment as pytest.
- In Qt desktop probes, advance the probe stage before calls that process Qt
  events. Event processing can invoke timer callbacks reentrantly and repeat
  setup actions if the state still appears to be in the prior stage.
- If moving real desktop coverage to Windows, macOS, or the SYCL job, configure
  and validate an appropriate display/rendering backend there; do not assume
  that running `pytest tests/` provides a display.
- If a test is intentionally Linux-only, make that limitation explicit in the
  workflow or test configuration rather than implying it runs on every OS.
- Preserve unrelated platform coverage when editing the workflow, and report
  clearly which jobs and platforms execute the changed tests.

## Verification

For workflow changes, inspect the affected job commands and environment
variables. Run the focused tests locally when feasible, and validate the
workflow syntax using the repository's available CI checks.
