# Database migrations - what these files actually are

**These `.sql` files are a reference mirror, not an automated migration
tool.** The application creates its schema by calling SQLAlchemy's
`Base.metadata.create_all()` on startup (see
`central-platform/app/core/db.py::init_db`), reading directly from the
models in `central-platform/app/models/db.py`. That call creates tables
that don't exist yet; it does **not** run `ALTER TABLE`/`ALTER TYPE`
statements against a table that already exists in an older shape.

## What this means in practice

- **Fresh install** (`docker compose down -v && docker compose up`, or
  any new environment): `create_all()` builds the *current* schema
  directly from the models, including every column and enum value added
  by `002_operational_upgrades.sql`. You do not need to run these `.sql`
  files by hand for a new deployment - the app does the equivalent work
  itself, correctly, on first boot.
- **An existing running instance with data you want to keep**, upgrading
  from before Priority 2-5 were added: `create_all()` will **not**
  retroactively add `recovered_at`, the new enum values, or the AI
  validation columns to your already-existing tables. You would need to
  run `002_operational_upgrades.sql` by hand against that database (or
  just drop and recreate it, if the data isn't precious - this project
  has no production data to protect).

## Why there's no Alembic

Alembic (or an equivalent versioned-migration tool) is the correct real
answer to this gap, and is explicitly listed as a "Production evolution"
item in `docs/deployment.md` and `FINAL_IMPLEMENTATION_AUDIT.md`. It was
deliberately **not** added here: this project's brief is a controlled,
fresh-install interview demo, not a system with existing production data
that needs safe, incremental, reversible schema changes - adding a real
migration framework (env.py, a versions/ directory, autogenerate wiring)
would be genuine, real infrastructure for a problem this project doesn't
actually have yet, which is exactly the kind of over-engineering the
brief asked to avoid. If this project ever did need to preserve real
data across schema changes, Alembic is what should be added next.
