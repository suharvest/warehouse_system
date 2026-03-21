# Worktree: task-55--api-fuzzy-match-search-xiaozhi
# Branch: wt/task-55--api-fuzzy-match-search-xiaozhi
# Project: warehouse_system

## Safety Rules
- You are working in a worktree. NEVER switch to main/master branch.
- NEVER run `git push --force` or `git reset --hard`.
- Only commit to branch: wt/task-55--api-fuzzy-match-search-xiaozhi
- Do not modify files outside this worktree.

## PROGRESS.md — Cross-Agent Experience Log
- Read latest: `git -C /mnt/d/project/warehouse_system show main:PROGRESS.md`
- Write new entry: `progress-write "/mnt/d/project/warehouse_system" "Title" "Content"`
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
2. Write a Playwright (.mjs) test that screenshots every changed page
3. Assert key elements exist (buttons, forms, data)
4. Test mobile viewport (375x812) for responsive layout
5. Review screenshots visually with the Read tool
6. Fix any rendering issues before marking task as done
Common pitfalls: Jinja2 auto-escaping HTML entities, Python set compound assignment creating local vars, shell literal backslash-n not being real newlines


