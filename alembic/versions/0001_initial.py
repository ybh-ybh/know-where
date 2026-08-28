"""创建 MVP 内部表。

Revision ID: 0001
Revises:
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# 当前迁移版本。
revision: str = "0001"
# 上一个迁移版本。
down_revision: str | None = None
# 分支标签。
branch_labels: str | Sequence[str] | None = None
# 依赖迁移。
depends_on: str | Sequence[str] | None = None


# 创建 MVP 所需表。
def upgrade() -> None:
    """升级数据库。"""

    op.create_table(
        "processing_tasks",
        sa.Column("task_id", sa.String(length=36), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processing_tasks_status", "processing_tasks", ["status"])
    op.create_table(
        "content_items",
        sa.Column("content_id", sa.String(length=64), primary_key=True),
        sa.Column("canonical_url", sa.Text(), nullable=False, unique=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("archive_provider", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("record_url", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "archive_workspaces",
        sa.Column("provider", sa.String(length=64), primary_key=True),
        sa.Column("workspace_id", sa.Text(), nullable=False),
        sa.Column("table_id", sa.Text(), nullable=False),
        sa.Column("primary_field_name", sa.Text(), nullable=False),
        sa.Column("workspace_url", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
    )


# 删除 MVP 表。
def downgrade() -> None:
    """降级数据库。"""

    op.drop_table("archive_workspaces")
    op.drop_table("content_items")
    op.drop_index("ix_processing_tasks_status", table_name="processing_tasks")
    op.drop_table("processing_tasks")
