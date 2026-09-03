"""本地临时工作目录生命周期测试。"""

from __future__ import annotations

from pathlib import Path

from knowwhere.adapters.local_temp_storage import LocalTempWorkspaceFactory
from knowwhere.config import TempStorageSettings


# 默认策略应在成功退出上下文后删除完整任务目录。
def test_local_temp_workspace_deletes_by_default(tmp_path: Path) -> None:
    """验证默认清理策略。"""

    # 用户配置的临时根目录。
    temp_root = tmp_path / "temp"
    # 默认删除的工作区工厂。
    factory = LocalTempWorkspaceFactory(TempStorageSettings(local_root=temp_root))
    with factory.create("knowwhere-test-") as workspace_value:
        # 当前任务工作目录。
        workspace = Path(workspace_value)
        # 当前任务临时文件。
        artifact = workspace / "audio.mp3"
        artifact.write_bytes(b"audio")
        assert artifact.is_file()

    assert not workspace.exists()
    assert list(temp_root.iterdir()) == []


# 用户关闭清理时应保留任务目录和其中的文件。
def test_local_temp_workspace_can_preserve_files(tmp_path: Path) -> None:
    """验证调试保留策略。"""

    # 用户配置的临时根目录。
    temp_root = tmp_path / "temp"
    # 显式关闭删除的工作区工厂。
    factory = LocalTempWorkspaceFactory(
        TempStorageSettings(local_root=temp_root, delete_after_process=False)
    )
    with factory.create("knowwhere-test-") as workspace_value:
        # 当前任务工作目录。
        workspace = Path(workspace_value)
        # 当前任务临时文件。
        artifact = workspace / "audio.mp3"
        artifact.write_bytes(b"audio")

    assert workspace.is_dir()
    assert artifact.read_bytes() == b"audio"
