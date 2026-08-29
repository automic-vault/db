Commit at sensible intervals.

Published YAML is for rarely changing curation data. Never add fields that change
regularly, including package versions, source archive or download URLs, checksums,
download counts, and refresh timestamps. Runtime artifacts such as JSON and SQLite
must obtain volatile package metadata directly from the relevant authoritative source.
