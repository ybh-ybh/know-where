"""可配置的本地任务级临时工作目录。"""

from __future__ import annotations

import tempfile

from knowwhere.config import TempStorageSettings


# 为每个媒体任务创建隔离目录并执行可配置清理。
class LocalTempWorkspaceFactory:
    """管理下载、FFmpeg 和本地 ASR 使用的工作目录。"""

    # 校验并准备受控根目录。
    def __init__(self, settings: TempStorageSettings) -> None:
        """初始化本地临时工作目录工厂。"""

        # 规范化后的受控根目录。
        self._root = settings.local_root.expanduser().resolve()
        # 是否在处理结束后删除任务目录。
        self._delete_after_process = settings.delete_after_process
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise ValueError("KW_TEMP_LOCAL_ROOT 必须是目录")

    # 创建只位于受控根目录下的任务工作区。
    def create(self, prefix: str) -> tempfile.TemporaryDirectory[str]:
        """返回遵循清理配置的临时目录上下文。"""

        if not prefix.startswith("knowwhere-"):
            raise ValueError("临时目录前缀必须属于 knowwhere")
        return tempfile.TemporaryDirectory(
            prefix=prefix,
            dir=self._root,
            delete=self._delete_after_process,
        )
