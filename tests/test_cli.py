"""命令行基础设施测试。"""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from alembic.config import Config

from alembic import command
from knowwhere.cli import _upgrade_database
from knowwhere.config import AppSettings


# 数据库密码含 URL 编码字符时，迁移入口不能触发 ConfigParser 插值错误。
def test_upgrade_database_accepts_percent_encoded_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证迁移配置保留百分号编码。"""

    # 模拟只提供数据库地址的已校验配置。
    settings = Mock(spec=AppSettings)
    settings.database_url = (
        "postgresql+psycopg://app_user:fake%2Bpassword%40db.example/knowwhere"
    )
    # 保存 Alembic 实际读取到的数据库地址。
    captured: dict[str, str] = {}

    # 截获迁移调用，避免单元测试连接数据库。
    def fake_upgrade(config: Config, revision: str) -> None:
        """记录 Alembic 解析后的配置。"""

        captured["database_url"] = config.get_main_option("sqlalchemy.url") or ""
        captured["revision"] = revision

    monkeypatch.setattr(command, "upgrade", fake_upgrade)

    _upgrade_database(settings)

    assert captured == {
        "database_url": settings.database_url,
        "revision": "head",
    }
