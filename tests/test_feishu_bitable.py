"""飞书多维表格 Schema v2 与记录映射测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from knowwhere.adapters.feishu_bitable import (
    CONTENT_ID_FIELD,
    DEFAULT_READ_STATUS,
    FIELD_DEFINITIONS,
    READ_STATUS_FIELD,
    READ_STATUS_OPTIONS,
    TITLE_FIELD,
    VIEW_DEFINITIONS,
    FeishuBitableAdapter,
)
from knowwhere.config import FeishuSettings
from knowwhere.domain.models import (
    AnalysisResult,
    ContentQuality,
    ExtractedContent,
    WorkspaceBinding,
)


# 测试绑定仓储避免访问 PostgreSQL。
class _BindingStore:
    """保存单个内存工作区绑定。"""

    # 当前内存绑定。
    binding: WorkspaceBinding | None = None

    # 返回当前绑定。
    def get(self, provider: str) -> WorkspaceBinding | None:
        """读取内存绑定。"""

        del provider
        return self.binding

    # 保存当前绑定。
    def put(self, binding: WorkspaceBinding) -> None:
        """更新内存绑定。"""

        self.binding = binding


# 创建不发起网络请求的飞书适配器。
def _adapter() -> FeishuBitableAdapter:
    """返回测试适配器。"""

    # 脱敏测试配置。
    settings = FeishuSettings(app_id="test_app", app_secret="test_secret")
    return FeishuBitableAdapter(settings, _BindingStore(), "test-model")


# 创建固定工作区绑定。
def _binding() -> WorkspaceBinding:
    """返回测试工作区绑定。"""

    return WorkspaceBinding(
        provider="feishu_bitable",
        workspace_id="app_test",
        table_id="table_test",
        primary_field_name=TITLE_FIELD,
        workspace_url="https://feishu.cn/base/app_test",
        schema_version=2,
    )


# 创建带平台标识和发布时间的文章内容。
def _content() -> ExtractedContent:
    """返回测试文章。"""

    return ExtractedContent(
        source_url="https://mp.weixin.qq.com/s/source-id",
        canonical_url="https://mp.weixin.qq.com/s/source-id",
        platform="微信公众号",
        title="测试文章",
        author="测试作者",
        body_text="测试正文",
        published_at=datetime(2026, 8, 28, 8, 0, tzinfo=UTC),
        quality=ContentQuality.FULL,
        platform_content_id="source-id",
        warnings=("正文包含一处提取警告",),
    )


# 创建固定 AI 分析结果。
def _analysis() -> AnalysisResult:
    """返回测试分析结果。"""

    return AnalysisResult(
        primary_category="技术与 AI",
        category_confidence=0.875,
        tags=("AI", "知识管理"),
        one_sentence_summary="一句话摘要",
        detailed_summary="详细摘要",
        key_points=("观点一", "观点二"),
        content_quality=ContentQuality.FULL,
    )


# Schema v2 必须使用干净字段并提供固定阅读状态枚举。
def test_schema_v2_uses_clean_typed_fields() -> None:
    """验证字段集合和阅读状态定义。"""

    # 声明字段名称。
    field_names = tuple(definition.name for definition in FIELD_DEFINITIONS)
    # 阅读状态字段定义。
    read_status = next(
        definition for definition in FIELD_DEFINITIONS if definition.name == READ_STATUS_FIELD
    )

    assert field_names[0] == TITLE_FIELD
    assert read_status.field_type == 3
    assert read_status.options == READ_STATUS_OPTIONS == ("未读", "已读")
    assert "单选" not in field_names
    assert "日期" not in field_names
    assert "附件" not in field_names
    assert "内容指纹" not in field_names


# 新记录应使用飞书真实类型，并把阅读状态默认设为未读。
def test_record_fields_use_typed_values_and_default_unread() -> None:
    """验证记录写入值。"""

    # 固定收藏时间。
    collected_at = datetime(2026, 8, 28, 9, 30, tzinfo=UTC)
    # Schema v2 记录映射。
    fields = _adapter()._record_fields(
        _binding(),
        "cnt_test",
        _content(),
        _analysis(),
        collected_at,
    )

    assert fields[CONTENT_ID_FIELD] == "cnt_test"
    assert fields[READ_STATUS_FIELD] == DEFAULT_READ_STATUS
    assert fields["原始链接"] == {
        "link": "https://mp.weixin.qq.com/s/source-id",
        "text": "查看原文",
    }
    assert fields["平台内容 ID"] == "source-id"
    assert fields["原发布时间"] == 1787904000000
    assert fields["收藏时间"] == 1787909400000
    assert fields["分类置信度"] == 0.875
    assert fields["标签"] == ["AI", "知识管理"]
    assert fields["处理次数"] == 1
    assert fields["状态说明"] == "正文包含一处提取警告"
    assert "内容指纹" not in fields


# 未读视图筛选必须使用飞书远端选项 ID，而不是中文显示名称。
def test_unread_view_filter_uses_remote_option_id() -> None:
    """验证单选筛选请求格式。"""

    # 未读视图声明。
    definition = next(item for item in VIEW_DEFINITIONS if item.name == "未读")
    # 模拟飞书字段回读对象。
    fields_by_name = {
        READ_STATUS_FIELD: {
            "field_id": "fld_read_status",
            "field_name": READ_STATUS_FIELD,
            "type": 3,
            "property": {
                "options": [
                    {"id": "opt_unread", "name": "未读"},
                    {"id": "opt_read", "name": "已读"},
                ]
            },
        }
    }
    # 飞书视图筛选属性。
    filter_info = FeishuBitableAdapter._view_filter_info(definition, fields_by_name)

    assert filter_info == {
        "conjunction": "and",
        "conditions": [
            {
                "field_id": "fld_read_status",
                "operator": "is",
                "value": '["opt_unread"]',
                "field_type": 3,
            }
        ],
    }


# 低置信度视图必须把阈值编码为飞书数字筛选值。
def test_low_confidence_view_filter_uses_numeric_literal() -> None:
    """验证数字筛选请求格式。"""

    # 低置信度视图声明。
    definition = next(item for item in VIEW_DEFINITIONS if item.name == "低置信度")
    # 模拟飞书数字字段回读对象。
    fields_by_name = {
        "分类置信度": {
            "field_id": "fld_confidence",
            "field_name": "分类置信度",
            "type": 2,
        }
    }
    # 飞书数字视图筛选属性。
    filter_info = FeishuBitableAdapter._view_filter_info(definition, fields_by_name)

    assert filter_info == {
        "conjunction": "and",
        "conditions": [
            {
                "field_id": "fld_confidence",
                "operator": "isLess",
                "value": '["0.6"]',
                "field_type": 2,
            }
        ],
    }


# 处理中视图的两个状态必须拆成 OR 条件，避免飞书丢弃第二个选项。
def test_processing_view_filter_uses_or_conditions() -> None:
    """验证多选项单选筛选请求格式。"""

    # 待处理与处理中视图声明。
    definition = next(item for item in VIEW_DEFINITIONS if item.name == "待处理与处理中")
    # 模拟飞书处理状态字段回读对象。
    fields_by_name = {
        "处理状态": {
            "field_id": "fld_processing_status",
            "field_name": "处理状态",
            "type": 3,
            "property": {
                "options": [
                    {"id": "opt_pending", "name": "待处理"},
                    {"id": "opt_processing", "name": "处理中"},
                ]
            },
        }
    }
    # 飞书多条件筛选属性。
    filter_info = FeishuBitableAdapter._view_filter_info(definition, fields_by_name)

    assert filter_info is not None
    assert filter_info["conjunction"] == "or"
    assert [condition["value"] for condition in filter_info["conditions"]] == [
        '["opt_pending"]',
        '["opt_processing"]',
    ]
