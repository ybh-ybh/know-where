"""增加抖音多媒体内容与任务检查点字段。

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# 当前迁移版本。
revision: str = "0002"
# 上一个迁移版本。
down_revision: str | None = "0001"
# 分支标签。
branch_labels: str | Sequence[str] | None = None
# 依赖迁移。
depends_on: str | Sequence[str] | None = None


# 以兼容现有文章数据的默认值增加多媒体字段。
def upgrade() -> None:
    """升级数据库。"""

    op.add_column("processing_tasks", sa.Column("checkpoint_stage", sa.String(64)))
    op.add_column(
        "processing_tasks",
        sa.Column(
            "checkpoint_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "content_items",
        sa.Column("platform", sa.String(64), nullable=False, server_default="未知"),
    )
    op.add_column("content_items", sa.Column("platform_content_id", sa.String(128)))
    op.add_column(
        "content_items",
        sa.Column("content_type", sa.String(32), nullable=False, server_default="article"),
    )
    op.add_column("content_items", sa.Column("author", sa.Text()))
    op.add_column("content_items", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_content_items_platform_content_id",
        "content_items",
        ["platform_content_id"],
    )
    op.alter_column("processing_tasks", "checkpoint_data", server_default=None)
    op.alter_column("content_items", "platform", server_default=None)
    op.alter_column("content_items", "content_type", server_default=None)


# 按升级逆序删除多媒体字段。
def downgrade() -> None:
    """降级数据库。"""

    op.drop_column("content_items", "published_at")
    op.drop_column("content_items", "author")
    op.drop_column("content_items", "content_type")
    op.drop_index("ix_content_items_platform_content_id", table_name="content_items")
    op.drop_column("content_items", "platform_content_id")
    op.drop_column("content_items", "platform")
    op.drop_column("processing_tasks", "checkpoint_data")
    op.drop_column("processing_tasks", "checkpoint_stage")
