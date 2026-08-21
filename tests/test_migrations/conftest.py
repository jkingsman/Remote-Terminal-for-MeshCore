# Updated automatically when a new migration is added.  Migration tests that
# run ``run_migrations`` to completion assert ``get_version == LATEST`` and
# ``applied == LATEST - starting_version`` so only this constant needs to
# change, not every individual assertion.
LATEST_SCHEMA_VERSION = 64
