---
name: swe-fix
description: Fix a repo bug with localize, unique patch, tests, then critic. Use for failing tests, GitHub issues, regressions.
---

1. Read AGENTS.md and the failing test.
2. grep / read until you know the exact file and lines.
3. apply_patch with a unique old string. Do not rewrite the whole file.
4. run_tests. If they fail, patch again from the failure output.
5. Do not finish until tests pass or you have a hard blocker.
