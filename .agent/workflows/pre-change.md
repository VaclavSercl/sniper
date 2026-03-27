---
description: Steps to follow before making any changes to the Beroun Sniper project
---

# Pre-Change Workflow

// turbo-all

Before making ANY code changes to the Beroun Sniper project, ALWAYS:

1. Read the change checklist:
```bash
cat /home/wwwenda/sniper/CHECKLIST.md
```

2. Check current system status:
```bash
systemctl is-active beroun-sniper.service && curl -s -o /dev/null -w "Dashboard: %{http_code}\n" http://localhost:3000
```

3. After making changes, verify against the checklist that ALL required files have been updated.

4. After deploying, verify no panics:
```bash
journalctl -u beroun-sniper.service --since "15 sec ago" --no-pager | grep -c panic
```
