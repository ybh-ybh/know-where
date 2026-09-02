"""SQLAlchemy 数据模型和连接生命周期。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


# SQLAlchemy 声明基类只存在于基础设施层。
class Base(DeclarativeBase):
    """数据库映射基类。"""


# 任务持久化模型。
class TaskRow(Base):
    """处理任务行。"""

    # 数据表名称。
    __tablename__ = "processing_tasks"

    # 任务 ID。
    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # 原始 URL。
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    # 当前状态。
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 安全错误摘要。
    error_message: Mapped[str | None] = mapped_column(Text)
    # 最近完成的提取检查点。
    checkpoint_stage: Mapped[str | None] = mapped_column(String(64))
    # 检查点无密钥数据。
    checkpoint_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # 创建时间。
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 更新时间。
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# 完成内容和外部归档模型。
class ContentRow(Base):
    """已完成内容行。"""

    # 数据表名称。
    __tablename__ = "content_items"

    # 内部内容 ID。
    content_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 规范 URL 唯一约束。
    canonical_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # 标题。
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # 来源平台。
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    # 平台作品标识。
    platform_content_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # 统一内容类型。
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 作者名称。
    author: Mapped[str | None] = mapped_column(Text)
    # 来源发布时间。
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 完整正文。
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 内容哈希。
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 结构化分析结果。
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # 外部提供方。
    archive_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    # 外部工作区 ID。
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 外部记录 ID。
    record_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 用户访问链接。
    record_url: Mapped[str] = mapped_column(Text, nullable=False)
    # 完成时间。
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# 系统创建的外部工作区绑定。
class ArchiveWorkspaceRow(Base):
    """归档工作区行。"""

    # 数据表名称。
    __tablename__ = "archive_workspaces"

    # 提供方唯一键。
    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    # 外部工作区 ID。
    workspace_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 外部数据表 ID。
    table_id: Mapped[str] = mapped_column(Text, nullable=False)
    # 主字段名称。
    primary_field_name: Mapped[str] = mapped_column(Text, nullable=False)
    # 工作区访问链接。
    workspace_url: Mapped[str] = mapped_column(Text, nullable=False)
    # Schema 版本。
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)


# 数据库连接封装。
class Database:
    """创建 Engine 与 Session。"""

    # 根据 URL 建立连接池。
    def __init__(self, database_url: str) -> None:
        """初始化数据库。"""

        # SQLAlchemy Engine。
        self.engine = create_engine(database_url, pool_pre_ping=True)
        # Session 工厂。
        self.session_factory = sessionmaker(self.engine, expire_on_commit=False)
