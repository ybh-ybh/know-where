"""供应商无关的音频分段转录编排。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from knowwhere.application.ports import AsrProviderPort, AsrTranscription

# 单个分段完成后的进度回调。
SegmentCompleted = Callable[[int, int], None]


# 统一三个媒体平台的分段转录和结果合并规则。
class AudioTranscriptionService:
    """按原始顺序转录全部本地音频分段。"""

    # 保存可替换的 ASR 供应商。
    def __init__(self, provider: AsrProviderPort) -> None:
        """初始化音频转录服务。"""

        # 当前 ASR 供应商。
        self._provider = provider

    # 转录全部分段并合并正文与警告。
    def transcribe_segments(
        self,
        audio_paths: tuple[Path, ...],
        segment_completed: SegmentCompleted | None = None,
    ) -> AsrTranscription:
        """返回保持分段顺序的完整转录。"""

        if not audio_paths:
            raise ValueError("没有可转录的音频分段")
        # 非空分段正文。
        transcript_parts: list[str] = []
        # 去重后的非致命警告。
        warnings: list[str] = []
        # 全部分段数量。
        segment_count = len(audio_paths)
        for segment_index, audio_path in enumerate(audio_paths):
            # 当前分段的供应商输出。
            result = self._provider.transcribe(audio_path)
            # 去除供应商可能附带的首尾空白。
            normalized_text = result.text.strip()
            if normalized_text:
                transcript_parts.append(normalized_text)
            for warning in result.warnings:
                if warning not in warnings:
                    warnings.append(warning)
            if segment_completed is not None:
                segment_completed(segment_index, segment_count)
        if not transcript_parts:
            raise ValueError("音频转录为空，拒绝生成伪完整摘要")
        return AsrTranscription(text="\n".join(transcript_parts), warnings=tuple(warnings))
