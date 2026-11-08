"""Small additive migrations for databases created before content metadata.

Startup integration (after importing app.models.note to register its tables)::

    Base.metadata.create_all(bind=engine)
    migrate_schema(engine)

Run before ORM queries or background workers. Calling before create_all is also
safe: absent tables are skipped and create_all subsequently supplies new tables.
No table is rebuilt or note data changed. The SQLite ID allocator watermark is
only advanced, never reset; repeated calls are safe.
"""
from sqlalchemy import inspect
from sqlalchemy.engine import Engine


# Trusted, fixed identifiers and SQL definitions only; never interpolate input.
_ADDITIONS = {
    "notes": {
        "content_status": "VARCHAR(50) NOT NULL DEFAULT 'unknown'",
        "images": "JSON NOT NULL DEFAULT '[]'",
        "image_total": "INTEGER NOT NULL DEFAULT 0",
        "image_failed": "INTEGER NOT NULL DEFAULT 0",
        "content_warning": "TEXT DEFAULT NULL",
        "ai_stale": "INTEGER NOT NULL DEFAULT 0",
    },
    "data_sources": {
        "auto_ai": "INTEGER NOT NULL DEFAULT 0",
        "sync_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "sync_interval_minutes": "INTEGER NOT NULL DEFAULT 1440",
        "next_sync_at": "DATETIME DEFAULT NULL",
    },
}


def migrate_schema(engine: Engine) -> None:
    """Add absent columns and seed SQLite's persistent note ID high-water mark.

    SQLite's legacy DBAPI transaction mode does not BEGIN for DDL, so explicitly
    begin a write transaction before inspecting/altering. BEGIN IMMEDIATE also
    serializes concurrent SQLite migrators so they cannot race column checks.
    Other dialects use SQLAlchemy's transaction; DDL atomicity is dialect-specific.
    Exceptions propagate to the caller so startup does not continue half-upgraded.
    """
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            # Accommodate engines that already BEGIN via a SQLAlchemy event hook
            # or use Python sqlite3's non-legacy autocommit=False mode.
            dbapi_connection = connection.connection.driver_connection
            if not dbapi_connection.in_transaction:
                connection.exec_driver_sql("BEGIN IMMEDIATE")

        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        quote = connection.dialect.identifier_preparer.quote_identifier
        for table, additions in _ADDITIONS.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for name, definition in additions.items():
                if name not in existing:
                    connection.exec_driver_sql(
                        f"ALTER TABLE {quote(table)} ADD COLUMN {quote(name)} {definition}"
                    )

        if connection.dialect.name == "sqlite" and "notes" in tables:
            # Additive only: legacy notes tables remain INTEGER PRIMARY KEY.
            # Seed before workers can delete today's maximum. A repeated
            # migration must retain a watermark above all surviving note IDs.
            connection.exec_driver_sql("""CREATE TABLE IF NOT EXISTS id_allocator (
                name VARCHAR(50) NOT NULL PRIMARY KEY,
                value INTEGER NOT NULL,
                CONSTRAINT ck_id_allocator_nonnegative CHECK (value >= 0)
            )""")
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO id_allocator (name, value) VALUES ('note', 0)"
            )
            connection.exec_driver_sql(
                "UPDATE id_allocator SET value = max(value, "
                "coalesce((SELECT max(id) FROM notes), 0)) WHERE name = 'note'"
            )
