"""Alembic 在线迁移入口。"""

from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from knowwhere.infrastructure.database import Base

# Alembic 当前配置。
config = context.config
# 环境变量或应用入口注入的数据库地址。
database_url = os.getenv("KW_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
if not database_url:
    raise RuntimeError("必须通过 KW_DATABASE_URL 提供 Alembic 数据库地址")
config.set_main_option("sqlalchemy.url", database_url)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
# 自动迁移元数据。
target_metadata = Base.metadata


# 离线生成 SQL。
def run_migrations_offline() -> None:
    """运行离线迁移。"""

    # 配置中的数据库 URL。
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


# 在线连接数据库迁移。
def run_migrations_online() -> None:
    """运行在线迁移。"""

    # Alembic Engine。
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
