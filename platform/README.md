# Sniper platform on the Beroun host

Canonical source repository: **VaclavSercl/sniper**. Beroun names the host and
existing runtime resources, not another trading product.

The root Rust engines and architect/ remain unchanged. This directory brings
the host's Python application into the same repository:

- gateway/, ingest/, risk-kernel/, research/, scripts/, db/: corrected application
  sources from the previous Beroun working repository, including repaired T15.
- runtime/: deployed gateway, risk kernel, ingestion and operational entrypoints.
- legacy/: older deployed research and collection programs retained for
  compatibility. These are source modules within Sniper, not a separate Git repo.
- tests/: relocated application tests and private PostgreSQL fixture.

SOURCE_MANIFEST.json records every imported source and checksum. Generic agent
instructions/harness, credentials, database contents, logs and runtime state are
not imported. Historical strategy scripts do not constitute performance approval.

Source references now use /opt/sniper/current/platform. Database beroun,
/opt/beroun/state, /etc/beroun, socket paths and Unix accounts remain compatible.
Renaming these would be a separate state migration. Original directories stay
untouched as recovery material until cutover is explicitly verified.

Validate from the Sniper repository root:

    python3 -B tools/verify_platform.py

This requires already installed PostgreSQL tools and starts only private,
temporary local test clusters. It never enables a trading service. Missing
capabilities cause a failure, not an automatic install or successful skip.

T15 remains unqualified: missing validated currency quotes, settlement events
and execution evidence block a new paper epoch. Do not launch the root Armada
boot script as part of this source consolidation: it includes LIVE-promotion
logic and is not the deployment path for these read-only/paper components.
