# Project: warehouse_system

## Safety Rules
- Work on the currently checked-out task branch unless the user explicitly
  requests a different branch or asks to merge the completed work.
- Merging to `main` is allowed only when the user explicitly requests it.
- NEVER run `git push --force` or `git reset --hard`.
- Do not modify files outside this worktree.

## PROGRESS.md — Cross-Agent Experience Log
- Read latest: `git show main:PROGRESS.md`
- Write new entries with `progress-write "$PWD" "Title" "Content"` when the
  helper is available.
- When you encounter a significant problem or learn something important, ALWAYS write to PROGRESS.md.

## Secrets & Devices
- NEVER read /mnt/d/project/_hub/secrets.json directly
- Use `secret-list` to see available keys
- Use `secret-run` to execute commands needing secrets
- Use `device-ssh` / `device-run` for remote device access
- View devices: `cat /mnt/d/project/_hub/devices.json`

## Frontend Verification (MANDATORY for UI changes)
If your task modifies frontend/Web UI:
1. Start the dev server
2. Use `playwright-cli` (already installed globally) for all browser interaction — screenshots, clicks, form fills, JS eval
3. NEVER install `playwright` or `@playwright/test` via npm/npx — use `playwright-cli` instead
4. Screenshot every changed page: `playwright-cli screenshot --filename=page.png`
5. Test mobile viewport: `playwright-cli resize 375 812`
6. Assert key elements exist: `playwright-cli snapshot` and check for expected text/selectors
7. Review screenshots visually with the Read tool
8. Fix any rendering issues before marking task as done
Common pitfalls: Jinja2 auto-escaping HTML entities, Python set compound assignment creating local vars, shell literal backslash-n not being real newlines
