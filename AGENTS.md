# MetaboLens public source guide

Work only in this standalone public-source repository. Never copy private Git history, runtime data, credentials or internal documents into it.

Read README.md, docs/README.md, docs/architecture/system-overview.md and docs/DEVELOPMENT.md for the public contract.

FastAPI owns permissions, validation and database writes. Dash is an API client. One worker owns queued workflow execution. Preserve frozen workflow source, input identities, review revisions and provenance. Keep large scientific files outside PostgreSQL. Use migrations for database changes.

Run tests for changed behavior. Run `python3 scripts/check_context_docs.py` after documentation changes. Update CHANGELOG.md for notable changes. Workflow changes also follow the workflow-local AGENTS.md.

Keep demo authentication explicitly opt-in. Never commit runtime/, .env files, SIRIUS login state, runner credentials, logs, raw measurements or generated reports. Retain third-party notices. Use synthetic fixtures for tests.

All Docker tests/builds use distinct metabolens-public names, no live stack volumes, no private SIRIUS home and no private checkout mounts. Never run commands against another checkout's database. Keep exactly one worker replica.
