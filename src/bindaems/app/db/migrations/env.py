"""Alembic-Umgebung; läuft nur über ``migrate`` mit einer übergebenen Verbindung."""

from alembic import context

from bindaems.app.db.schema import metadata

connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
