from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnalyticsTable:
    name: str
    columns: dict[str, str]
    partition_columns: dict[str, str]

    @property
    def all_columns(self) -> dict[str, str]:
        return {**self.columns, **self.partition_columns}


GOLD_DOCUMENTS = AnalyticsTable(
    name="gold_documents",
    columns={
        "run_id": "string",
        "document_id": "string",
        "source_s3_key": "string",
        "source_file_name": "string",
        "document_type": "string",
        "document_date": "string",
        "vendor_name": "string",
        "total_amount": "double",
        "currency": "string",
        "extraction_engine": "string",
        "normalization_engine": "string",
        "llm_model_id": "string",
        "bedrock_invoked": "boolean",
        "bedrock_completed_fields": "array<string>",
        "processing_status": "string",
        "quality_status": "string",
        "quality_flags": "array<string>",
        "rejection_reason": "string",
        "created_at": "string",
        "document_fingerprint": "string",
        "business_key": "string",
        "is_duplicate": "boolean",
        "duplicate_of_document_id": "string",
        "duplicate_strategy": "string",
        "duplicate_confidence": "double",
    },
    partition_columns={"batch_id": "string"},
)

GOLD_INVOICE_SUMMARY = AnalyticsTable(
    name="gold_invoice_summary",
    columns={
        "invoice_id": "string",
        "invoice_date": "string",
        "supplier_name": "string",
        "currency": "string",
        "total_amount": "double",
        "subtotal_amount": "decimal(18,2)",
        "tax_amount": "decimal(18,2)",
        "document_type": "string",
        "processing_date": "string",
    },
    partition_columns={},
)

TABLES = {
    GOLD_DOCUMENTS.name: GOLD_DOCUMENTS,
    GOLD_INVOICE_SUMMARY.name: GOLD_INVOICE_SUMMARY,
}


def table_schema_prompt(table_name: str | None = None) -> str:
    """Render column info for one table, or all tables when table_name is None.

    SQL generation needs every table's real columns in context -- the prompt
    tells the model to pick between gold_invoice_summary and gold_documents,
    and a model that can't see a table's actual columns will guess at names
    that don't exist.
    """
    tables = [TABLES[table_name]] if table_name else list(TABLES.values())
    blocks = []
    for table in tables:
        lines = [f"Table: {table.name}", "Columns:"]
        for column_name, column_type in table.all_columns.items():
            lines.append(f"- {column_name}: {column_type}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

