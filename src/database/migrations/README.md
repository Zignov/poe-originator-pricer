Migrations are defined in `src/database/models.py` as an append-only `MIGRATIONS`
list, applied in order and tracked in the `schema_version` table.

They live in code rather than in loose `.sql` files here so that the schema and
the code that reads it cannot drift apart, and so a fresh database and an
upgraded one always take the same path.

To add one: append a new `(version, sql)` tuple. Never edit a released entry.
