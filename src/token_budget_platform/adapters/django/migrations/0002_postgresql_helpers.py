"""PostgreSQL-only operational helpers.

Installs pg_trgm generalized-inverted indexes used by case-insensitive containment
search (Req 2.1) and the monthly partition helper used by maintenance-managed
partition tables (Req 16.2).  Both are skipped on other vendors so SQLite remains
the dependency-free local fallback.
"""

from __future__ import annotations

from django.db import migrations


def install_helpers(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    # Fully literal DDL only: identifiers are fixed schema names, never user input.
    schema_editor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS record_org_search_trgm_idx"
        " ON token_budget_record USING gin (organization_search gin_trgm_ops)"
    )
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS record_provider_search_trgm_idx"
        " ON token_budget_record USING gin (provider_search gin_trgm_ops)"
    )
    schema_editor.execute(
        "CREATE INDEX IF NOT EXISTS record_product_search_trgm_idx"
        " ON token_budget_record USING gin (product_search gin_trgm_ops)"
    )
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION token_budget_create_month_partition(
            parent_table text, partition_month date
        ) RETURNS void LANGUAGE plpgsql AS $fn$
        DECLARE
            month_start date := date_trunc('month', partition_month)::date;
            month_end date := (date_trunc('month', partition_month) + interval '1 month')::date;
            child_name text := parent_table || '_' || to_char(month_start, 'YYYY_MM');
        BEGIN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I PARTITION OF %I FOR VALUES FROM (%L) TO (%L)',
                child_name, parent_table, month_start, month_end
            );
        END;
        $fn$;
        """
    )


def remove_helpers(apps, schema_editor) -> None:
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP INDEX IF EXISTS record_org_search_trgm_idx")
    schema_editor.execute("DROP INDEX IF EXISTS record_provider_search_trgm_idx")
    schema_editor.execute("DROP INDEX IF EXISTS record_product_search_trgm_idx")
    schema_editor.execute("DROP FUNCTION IF EXISTS token_budget_create_month_partition(text, date)")


class Migration(migrations.Migration):
    dependencies = [
        ("token_budget_adapter", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(install_helpers, remove_helpers),
    ]
