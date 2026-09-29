# Beroun deployment host

Sniper is the only trading repository. Existing beroun-* service names, Unix
accounts, database names, sockets and state paths remain compatibility identifiers.
They do not imply a second trading repository.

Run plan_cutover.py on the host against the verified Sniper checkout:

    python3 infra/beroun/plan_cutover.py --output cutover-plan.json

This only reads installed unit definitions and validates mapped entrypoints.
It refuses existing unreviewed drop-ins, multiline commands and missing scripts.
The proposed code root is /opt/sniper/current, with immutable releases under
/opt/sniper/releases/<verified-commit>. Original source trees remain preserved.

Cutover requires a separately reviewed, privileged operation: install the exact
verified source release, archive previous unit definitions and link target, write
only the listed managed drop-ins, reload systemd, then restart only previously
active read-only components. Do not launch sniper-armada or its automatic LIVE
promotion. Do not automatically start failed/inactive services.

Pause the old T15 timer before cutover: its legacy paper results are invalid and
the repaired v2 epoch cannot run without missing validated quote/funding inputs.
Preserve the old database row. Do not create a v2 epoch or relax data checks merely
to make the service report success. This pause is part of the explicit cutover
approval, not a side effect of source validation or GitHub publication.

Rollback restores the previous current link and only migration-owned drop-ins
whose hashes still match. Then reload and restore exactly the previous active
service/timer set. Never delete old repositories, databases, environment files
or unrecognized service overrides. Inspect live states and socket readiness
after either cutover or rollback before reporting a deployed result.
