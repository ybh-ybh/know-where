"""faster-whisper 本地离线 ASR 适配器。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any

from knowwhere.application.ports import AsrProviderPort, AsrTranscription
from knowwhere.config import FasterWhisperSettings


# 延迟加载模型，避免健康检查或纯文章任务触发大模型下载。
class FasterWhisperAsrProvider(AsrProviderPort):
    """使用 CTranslate2 在本机转录标准音频。"""

    # 保存配置但不立即加载模型。
    def __init__(self, settings: FasterWhisperSettings) -> None:
        """初始化本地 ASR 适配器。"""

        # 本地推理配置。
        self._settings = settings
        # 延迟初始化的 WhisperModel。
        self._model: Any | None = None
        # 防止并发首请求重复加载模型。
        self._model_lock = Lock()

    # 从本地文件同步执行离线转录。
    def transcribe(self, audio_path: Path) -> AsrTranscription:
        """返回当前音频分段的本地识别文本。"""

        if not audio_path.is_file() or audio_path.stat().st_size == 0:
            raise ValueError("本地 ASR 音频文件不存在或为空")
        # 已加载或刚完成加载的模型。
        model = self._get_model()
        # faster-whisper 返回惰性分段迭代器。
        segments, _info = model.transcribe(
            str(audio_path),
            language=self._settings.language,
            beam_size=self._settings.beam_size,
            vad_filter=self._settings.vad_filter,
        )
        # 按模型分段顺序拼接正文。
        transcript_parts = [str(segment.text).strip() for segment in segments]
        # 丢弃纯静音等空分段。
        normalized_parts = [part for part in transcript_parts if part]
        return AsrTranscription(text="\n".join(normalized_parts))

    # 线程安全地完成首次模型加载。
    def _get_model(self) -> Any:
        """返回进程内复用的 faster-whisper 模型。"""

        if self._model is not None:
            return self._model
        with self._model_lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            # 可选缓存路径字符串。
            download_root = (
                str(self._settings.model_dir.expanduser().resolve())
                if self._settings.model_dir is not None
                else None
            )
            # 本地模型名称或目录。
            model_name = self._settings.model
            # 构建后在后续任务中复用的模型实例。
            self._model = WhisperModel(
                model_name,
                device=self._settings.device,
                compute_type=self._settings.compute_type,
                download_root=download_root,
                cpu_threads=self._settings.cpu_threads,
                local_files_only=not self._settings.allow_model_download,
            )
            return self._model
