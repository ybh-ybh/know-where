"""飞书多维表格归档与动态分类适配器。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from knowwhere.application.ports import (
    CategoryCatalogPort,
    RecordArchivePort,
    WorkspaceBindingStorePort,
)
from knowwhere.config import FeishuSettings
from knowwhere.domain.models import (
    AnalysisResult,
    ArchiveResult,
    ExtractedContent,
    WorkspaceBinding,
)

# MVP 默认分类与 PRD 保持一致，用户可在飞书单选字段中继续添加。
DEFAULT_CATEGORIES: Final[tuple[str, ...]] = (
    "技术与 AI",
    "产品与商业",
    "职场与管理",
    "内容与运营",
    "学习与研究",
    "财经与投资",
    "生活与健康",
    "社会与文化",
    "灵感与案例",
    "其他",
)

# 当前由应用管理的多维表格 Schema 版本。
SCHEMA_VERSION: Final[int] = 2

# 飞书多维表格的数据表名称。
TABLE_NAME: Final[str] = "内容库"

# 默认用户视图名称。
DEFAULT_VIEW_NAME: Final[str] = "收件箱"

# 飞书主字段名称。
TITLE_FIELD: Final[str] = "标题"

# 飞书归档记录的稳定内容标识字段名。
CONTENT_ID_FIELD: Final[str] = "内容 ID"

# 用户可编辑的阅读状态字段名称。
READ_STATUS_FIELD: Final[str] = "阅读状态"

# 新归档内容的默认阅读状态。
DEFAULT_READ_STATUS: Final[str] = "未读"

# 阅读状态只允许这两个清晰选项。
READ_STATUS_OPTIONS: Final[tuple[str, ...]] = (DEFAULT_READ_STATUS, "已读")


# 应用管理字段的供应商无关声明。
@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """描述一个飞书多维表格字段。"""

    # 字段显示名称。
    name: str
    # 飞书字段类型编号。
    field_type: int
    # 单选或多选的初始选项。
    options: tuple[str, ...] = ()
    # 数字显示格式。
    formatter: str | None = None
    # 日期时间显示格式。
    date_formatter: str | None = None


# 系统管理字段按用户阅读顺序排列；首字段会成为飞书主字段。
FIELD_DEFINITIONS: Final[tuple[FieldDefinition, ...]] = (
    FieldDefinition(TITLE_FIELD, 1),
    FieldDefinition(CONTENT_ID_FIELD, 1),
    FieldDefinition("原始链接", 15),
    FieldDefinition("规范链接", 15),
    FieldDefinition("平台", 3, ("微信公众号", "掘金", "GitHub", "小红书", "抖音", "其他网页")),
    FieldDefinition("平台内容 ID", 1),
    FieldDefinition("内容类型", 3, ("文章", "图文", "视频", "未知")),
    FieldDefinition("作者", 1),
    FieldDefinition("原发布时间", 5, date_formatter="yyyy/MM/dd HH:mm"),
    FieldDefinition("收藏时间", 5, date_formatter="yyyy/MM/dd HH:mm"),
    FieldDefinition(READ_STATUS_FIELD, 3, READ_STATUS_OPTIONS),
    FieldDefinition("一级分类", 3, DEFAULT_CATEGORIES),
    FieldDefinition("分类置信度", 2, formatter="0.0000"),
    FieldDefinition("标签", 4),
    FieldDefinition("一句话摘要", 1),
    FieldDefinition("详细摘要", 1),
    FieldDefinition("关键观点", 1),
    FieldDefinition("完整正文/转录", 1),
    FieldDefinition("全文保存方式", 3, ("多维表格字段", "飞书文档", "仅元数据")),
    FieldDefinition("飞书全文文档", 15),
    FieldDefinition("内容质量", 3, ("完整", "部分", "仅元数据")),
    FieldDefinition("处理状态", 3, ("待处理", "处理中", "已完成", "部分成功", "失败")),
    FieldDefinition("状态说明", 1),
    FieldDefinition("失败阶段", 3, ("解析", "抓取", "下载", "转录", "AI", "归档")),
    FieldDefinition("处理次数", 2, formatter="0"),
    FieldDefinition("最近处理时间", 5, date_formatter="yyyy/MM/dd HH:mm"),
    FieldDefinition("模型信息", 1),
)

# 默认工作视图只展示日常整理需要的字段。
DEFAULT_VISIBLE_FIELDS: Final[tuple[str, ...]] = (
    TITLE_FIELD,
    "原始链接",
    "平台",
    "内容类型",
    "作者",
    "原发布时间",
    "收藏时间",
    READ_STATUS_FIELD,
    "一级分类",
    "标签",
    "一句话摘要",
    "详细摘要",
    "关键观点",
    "内容质量",
    "处理状态",
    "状态说明",
    "飞书全文文档",
)

# 全文视图聚焦阅读原文和完整归档内容。
FULL_TEXT_VISIBLE_FIELDS: Final[tuple[str, ...]] = (
    TITLE_FIELD,
    "原始链接",
    READ_STATUS_FIELD,
    "完整正文/转录",
    "全文保存方式",
    "飞书全文文档",
    "内容质量",
)

# 系统信息视图集中展示幂等和诊断字段。
SYSTEM_VISIBLE_FIELDS: Final[tuple[str, ...]] = (
    TITLE_FIELD,
    CONTENT_ID_FIELD,
    "规范链接",
    "平台内容 ID",
    "分类置信度",
    "全文保存方式",
    "处理状态",
    "状态说明",
    "失败阶段",
    "处理次数",
    "最近处理时间",
    "模型信息",
)


# 系统视图声明只负责名称和字段可见性，保留用户后续调整布局的自由。
@dataclass(frozen=True, slots=True)
class ViewDefinition:
    """描述一个系统管理的表格视图。"""

    # 视图名称。
    name: str
    # 视图中保持可见的字段名称。
    visible_fields: tuple[str, ...]
    # 可选的筛选字段名称。
    filter_field_name: str | None = None
    # 筛选值；单选字段使用选项名称，数字字段使用文本数字。
    filter_values: tuple[str, ...] = ()
    # 飞书筛选操作符。
    filter_operator: str = "is"
    # 是否需要把显示名称解析为远端单选项 ID。
    resolve_option_ids: bool = True


# Schema v2 的默认视图集合。
VIEW_DEFINITIONS: Final[tuple[ViewDefinition, ...]] = (
    ViewDefinition(DEFAULT_VIEW_NAME, DEFAULT_VISIBLE_FIELDS),
    ViewDefinition("未读", DEFAULT_VISIBLE_FIELDS, READ_STATUS_FIELD, (DEFAULT_READ_STATUS,)),
    ViewDefinition("按分类浏览", DEFAULT_VISIBLE_FIELDS),
    ViewDefinition("视频内容", DEFAULT_VISIBLE_FIELDS, "内容类型", ("视频",)),
    ViewDefinition(
        "待处理与处理中",
        DEFAULT_VISIBLE_FIELDS,
        "处理状态",
        ("待处理", "处理中"),
    ),
    ViewDefinition("失败待重试", DEFAULT_VISIBLE_FIELDS, "处理状态", ("失败",)),
    ViewDefinition(
        "低置信度",
        DEFAULT_VISIBLE_FIELDS,
        "分类置信度",
        ("0.6",),
        "isLess",
        False,
    ),
    ViewDefinition("全文", FULL_TEXT_VISIBLE_FIELDS),
    ViewDefinition("系统信息", SYSTEM_VISIBLE_FIELDS),
)

# 飞书内容质量选项使用面向用户的中文文案。
CONTENT_QUALITY_LABELS: Final[dict[str, str]] = {
    "full": "完整",
    "partial": "部分",
    "metadata_only": "仅元数据",
}


# 飞书 API 业务错误，保留 code 和脱敏后的 msg 便于定位权限问题。
class FeishuApiError(RuntimeError):
    """表示 HTTP 成功但飞书业务调用失败。"""

    # 保存稳定业务错误信息。
    def __init__(self, code: int, message: str) -> None:
        """初始化飞书错误。"""

        self.code = code
        self.message = message
        super().__init__(f"飞书 API 失败(code={code}): {message}")


# 同一个适配器同时实现归档和分类目录，保证两者读取同一工作区。
class FeishuBitableAdapter(CategoryCatalogPort, RecordArchivePort):
    """系统创建并维护“知归”多维表格。"""

    # 保存配置、绑定仓储和可注入客户端。
    def __init__(
        self,
        settings: FeishuSettings,
        binding_store: WorkspaceBindingStorePort,
        model_info: str,
        client: httpx.Client | None = None,
    ) -> None:
        """初始化多维表格适配器。"""

        self._settings = settings
        self._binding_store = binding_store
        self._model_info = model_info
        self._client = client or httpx.Client(timeout=httpx.Timeout(30.0))
        # 租户令牌只缓存在内存，不写日志或数据库。
        self._tenant_token: str | None = None
        # 令牌提前一分钟刷新，避免请求途中到期。
        self._token_expires_at = 0.0

    # 从飞书“一级分类”字段实时读取默认和用户自定义选项。
    def list_categories(self) -> tuple[str, ...]:
        """返回当前允许的一级分类。"""

        # 工作区初始化是分类目录可用的前置条件。
        binding = self.ensure_workspace()
        # 远端字段集合。
        fields = self._list_fields(binding)
        # 一级分类字段。
        category_field = next(
            (field for field in fields if field.get("field_name") == "一级分类"),
            None,
        )
        if category_field is None:
            raise RuntimeError("飞书多维表格缺少一级分类字段")
        # 单选字段属性。
        property_data = category_field.get("property") or {}
        # 当前单选项。
        option_items = property_data.get("options") or []
        # 保序去重后的选项名称。
        categories = tuple(
            dict.fromkeys(
                str(option.get("name", "")).strip()
                for option in option_items
                if str(option.get("name", "")).strip()
            )
        )
        if not categories:
            raise RuntimeError("飞书一级分类字段没有可用选项")
        return categories

    # 幂等创建工作区并补齐应用管理字段。
    def ensure_workspace(self) -> WorkspaceBinding:
        """返回可用的系统多维表格绑定。"""

        # 优先复用数据库中的稳定外部标识。
        binding = self._binding_store.get("feishu_bitable")
        if binding is None:
            binding = self._create_workspace()
            self._binding_store.put(binding)
        # 每次启动/使用都只补齐缺失字段，不删除或重命名用户字段。
        migrated_binding = self._ensure_schema(binding)
        if migrated_binding != binding:
            self._binding_store.put(migrated_binding)
        return migrated_binding

    # 将首次私聊用户设为多维表格管理协作者。
    def grant_full_access(self, open_id: str) -> None:
        """授予指定飞书用户完整访问权限。"""

        if not open_id:
            raise ValueError("飞书 open_id 不能为空")
        # 当前可用工作区。
        binding = self.ensure_workspace()
        # 当前协作者集合。
        member_data = self._request_json(
            "GET",
            f"/open-apis/drive/v1/permissions/{binding.workspace_id}/members",
            params={"type": "bitable"},
        )
        # 同一 open_id 的现有协作者。
        existing_member = next(
            (
                item
                for item in member_data.get("items") or []
                if str(item.get("member_id", "")) == open_id
            ),
            None,
        )
        if existing_member is not None:
            if existing_member.get("perm") == "full_access":
                return
            self._request_json(
                "PUT",
                f"/open-apis/drive/v1/permissions/{binding.workspace_id}/members/{open_id}",
                params={"type": "bitable", "need_notification": "false"},
                json={
                    "member_type": "openid",
                    "member_id": open_id,
                    "perm": "full_access",
                },
            )
            return
        self._request_json(
            "POST",
            f"/open-apis/drive/v1/permissions/{binding.workspace_id}/members",
            params={"type": "bitable", "need_notification": "false"},
            json={
                "member_type": "openid",
                "member_id": open_id,
                "perm": "full_access",
            },
        )

    # 检查数据库保存的飞书记录引用是否仍然有效。
    def archive_exists(self, archive: ArchiveResult) -> bool:
        """远端记录被用户删除后返回假，以便流水线重新归档。"""

        # 归档提供方对应的本地工作区绑定。
        binding = self._binding_store.get(archive.provider)
        if binding is None or binding.workspace_id != archive.workspace_id:
            return False
        return self._record_id_exists(binding, archive.record_id)

    # 按内容 ID 幂等写入归档记录。
    def upsert(
        self,
        content_id: str,
        content_hash: str,
        content: ExtractedContent,
        analysis: AnalysisResult,
    ) -> ArchiveResult:
        """创建记录；已存在时返回原记录。"""

        # 当前可用工作区。
        binding = self.ensure_workspace()
        # 防止数据库提交失败后的重试重复创建外部记录。
        existing_record_id = self._find_record_id(binding, content_id)
        if existing_record_id is not None:
            return self._archive_result(binding, existing_record_id)
        # 内容指纹只保存在内部 PostgreSQL，不再暴露为用户表格字段。
        del content_hash
        # 当前 UTC 时间。
        collected_at = datetime.now(UTC)
        # 飞书记录字段映射使用字段真实类型，不再把日期和数字降级成文本。
        fields = self._record_fields(binding, content_id, content, analysis, collected_at)
        # 新增记录响应。
        data = self._request_json(
            "POST",
            f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/{binding.table_id}/records",
            json={"fields": fields},
        )
        # 新增后的记录对象。
        record = data.get("record") or {}
        # 记录唯一标识。
        record_id = str(record.get("record_id", ""))
        if not record_id:
            raise RuntimeError("飞书新增记录响应缺少 record_id")
        return self._archive_result(binding, record_id)

    # 将领域结果映射为飞书强类型字段值。
    def _record_fields(
        self,
        binding: WorkspaceBinding,
        content_id: str,
        content: ExtractedContent,
        analysis: AnalysisResult,
        collected_at: datetime,
    ) -> dict[str, Any]:
        """构造一条 Schema v2 归档记录。"""

        # 提取与分析警告合并后保序去重。
        warnings = tuple(dict.fromkeys((*content.warnings, *analysis.warnings)))
        # 没有供应商警告时仍明确说明启发式降级。
        status_notes = warnings or (("AI 结果使用启发式降级生成",) if analysis.degraded else ())
        # 非完整证据或启发式分析都属于可见的部分成功。
        processing_status = (
            "部分成功"
            if analysis.degraded or analysis.content_quality.value != "full"
            else "已完成"
        )
        # 仅元数据内容不声称已保存完整正文。
        full_text_storage = (
            "仅元数据" if analysis.content_quality.value == "metadata_only" else "多维表格字段"
        )
        # 基础字段映射。
        fields: dict[str, Any] = {
            binding.primary_field_name: content.title[:1000],
            CONTENT_ID_FIELD: content_id,
            "原始链接": self._url_value(content.source_url, "查看原文"),
            "规范链接": self._url_value(content.canonical_url, "规范链接"),
            "平台": content.platform,
            "内容类型": {
                "article": "文章",
                "image_text": "图文",
                "video": "视频",
            }[content.content_type.value],
            "作者": content.author or "",
            "收藏时间": self._datetime_milliseconds(collected_at),
            READ_STATUS_FIELD: DEFAULT_READ_STATUS,
            "一级分类": analysis.primary_category,
            "分类置信度": analysis.category_confidence,
            "标签": list(analysis.tags),
            "一句话摘要": analysis.one_sentence_summary,
            "详细摘要": analysis.detailed_summary,
            "关键观点": "\n".join(
                f"{index}. {point}" for index, point in enumerate(analysis.key_points, 1)
            ),
            "完整正文/转录": content.body_text,
            "全文保存方式": full_text_storage,
            "内容质量": CONTENT_QUALITY_LABELS[analysis.content_quality.value],
            "处理状态": processing_status,
            "状态说明": "\n".join(status_notes),
            "处理次数": 1,
            "最近处理时间": self._datetime_milliseconds(collected_at),
            "模型信息": self._model_info,
        }
        if content.platform_content_id:
            fields["平台内容 ID"] = content.platform_content_id
        if content.published_at:
            fields["原发布时间"] = self._datetime_milliseconds(content.published_at)
        return fields

    # 将 URL 转成飞书 URL 字段要求的对象格式。
    @staticmethod
    def _url_value(url: str, text: str) -> dict[str, str]:
        """构造飞书 URL 字段值。"""

        return {"link": url, "text": text}

    # 将日期时间转成飞书要求的 Unix 毫秒时间戳。
    @staticmethod
    def _datetime_milliseconds(value: datetime) -> int:
        """返回 UTC Unix 毫秒时间戳。"""

        # 无时区值按 UTC 解释，避免受宿主机本地时区影响。
        normalized = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return int(normalized.timestamp() * 1000)

    # 创建多维表格，并用强类型字段一次创建干净的内容主表。
    def _create_workspace(self) -> WorkspaceBinding:
        """创建“知归”多维表格。"""

        # 创建应用响应数据。
        data = self._request_json(
            "POST",
            "/open-apis/bitable/v1/apps",
            json={"name": "知归"},
        )
        # 新建多维表格对象。
        app_data = data.get("app") or {}
        # 多维表格 app_token。
        app_token = str(app_data.get("app_token", ""))
        if not app_token:
            raise RuntimeError("飞书创建多维表格响应缺少 app_token")
        # 默认表 ID 在不同 API 版本中可能不随创建响应返回。
        default_table_id = str(app_data.get("default_table_id", ""))
        if not default_table_id:
            # 默认数据表列表。
            table_data = self._request_json(
                "GET",
                f"/open-apis/bitable/v1/apps/{app_token}/tables",
                params={"page_size": "100"},
            )
            # 第一张默认表。
            tables = table_data.get("items") or []
            if not tables:
                raise RuntimeError("飞书新建多维表格没有默认数据表")
            default_table_id = str(tables[0].get("table_id", ""))
        # Schema v2 表结构由创建接口原子写入，不保留飞书自带的空白字段。
        table_data = self._request_json(
            "POST",
            f"/open-apis/bitable/v1/apps/{app_token}/tables",
            json={
                "table": {
                    "name": TABLE_NAME,
                    "default_view_name": DEFAULT_VIEW_NAME,
                    "fields": [
                        self._field_request_body(definition) for definition in FIELD_DEFINITIONS
                    ],
                }
            },
        )
        # 新内容表 ID。
        table_id = str(table_data.get("table_id", ""))
        if not table_id:
            raise RuntimeError("飞书创建内容库响应缺少 table_id")
        if default_table_id and default_table_id != table_id:
            # 只有自定义表创建成功后才删除飞书默认空表。
            self._request_json(
                "DELETE",
                f"/open-apis/bitable/v1/apps/{app_token}/tables/{default_table_id}",
            )
        # 工作区可访问地址。
        workspace_url = str(app_data.get("url", "")) or f"https://feishu.cn/base/{app_token}"
        # 新工作区绑定。
        binding = WorkspaceBinding(
            provider="feishu_bitable",
            workspace_id=app_token,
            table_id=table_id,
            primary_field_name=TITLE_FIELD,
            workspace_url=workspace_url,
            schema_version=0,
        )
        # 远端回读能尽早发现供应商未接受主字段声明的问题。
        fields = self._list_fields(binding)
        # 实际主字段。
        primary_field = next((field for field in fields if field.get("is_primary")), None)
        # 实际主字段名称。
        primary_name = str((primary_field or {}).get("field_name", ""))
        if primary_name != TITLE_FIELD:
            raise RuntimeError("飞书内容库主字段不是标题")
        return binding

    # 只新增缺失字段，保护用户添加的分类和自定义字段。
    def _ensure_schema(self, binding: WorkspaceBinding) -> WorkspaceBinding:
        """迁移多维表格到当前兼容 Schema。"""

        # 当前字段集合。
        existing_fields = self._list_fields(binding)
        # 字段名索引。
        existing_by_name = {str(field.get("field_name", "")): field for field in existing_fields}
        for definition in FIELD_DEFINITIONS:
            # 同名远端字段。
            existing_field = existing_by_name.get(definition.name)
            if existing_field is None:
                self._create_field(binding, self._field_request_body(definition))
                continue
            # 错误类型不能在有数据时静默转换，否则可能损坏用户数据。
            existing_type = int(existing_field.get("type", 0))
            if existing_type != definition.field_type:
                raise RuntimeError(
                    f"飞书字段 {definition.name} 类型不兼容: "
                    f"期望 {definition.field_type}，实际 {existing_type}"
                )
            if definition.name == "平台":
                self._ensure_field_options(binding, existing_field, definition.options)
        # 补齐视图并统一隐藏技术字段。
        self._ensure_views(binding)
        return WorkspaceBinding(
            provider=binding.provider,
            workspace_id=binding.workspace_id,
            table_id=binding.table_id,
            primary_field_name=TITLE_FIELD,
            workspace_url=binding.workspace_url,
            schema_version=SCHEMA_VERSION,
        )

    # 补齐系统必需的单选项，同时保留用户已有选项。
    def _ensure_field_options(
        self,
        binding: WorkspaceBinding,
        field: dict[str, Any],
        required_options: tuple[str, ...],
    ) -> None:
        """幂等补齐指定字段的必需选项。"""

        # 远端字段属性。
        property_data = field.get("property") or {}
        # 远端现有选项对象。
        existing_options = [
            dict(option)
            for option in property_data.get("options") or []
            if isinstance(option, dict)
        ]
        # 现有选项名称集合。
        existing_names = {
            str(option.get("name", "")).strip()
            for option in existing_options
            if str(option.get("name", "")).strip()
        }
        # 待新增的系统必需选项。
        missing_options = [name for name in required_options if name not in existing_names]
        if not missing_options:
            return
        # 远端字段 ID。
        field_id = str(field.get("field_id", ""))
        if not field_id:
            raise RuntimeError(f"飞书字段 {field.get('field_name', '')} 缺少 field_id")
        # 更新时保留用户已有选项和其远端 ID。
        merged_options = [*existing_options, *({"name": name} for name in missing_options)]
        self._request_json(
            "PUT",
            f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/"
            f"{binding.table_id}/fields/{field_id}",
            json={
                "field_name": str(field.get("field_name", "")),
                "type": int(field.get("type", 0)),
                "property": {"options": merged_options},
            },
        )

    # 把字段声明转为飞书创建字段/建表共用的请求体。
    @staticmethod
    def _field_request_body(definition: FieldDefinition) -> dict[str, Any]:
        """构造字段请求体。"""

        # 字段基础属性。
        body: dict[str, Any] = {
            "field_name": definition.name,
            "type": definition.field_type,
        }
        # 只有需要时才传 property，避免空属性触发供应商校验错误。
        property_data: dict[str, Any] = {}
        if definition.options:
            property_data["options"] = [{"name": option_name} for option_name in definition.options]
        if definition.formatter:
            property_data["formatter"] = definition.formatter
        if definition.date_formatter:
            property_data["date_formatter"] = definition.date_formatter
        if property_data:
            body["property"] = property_data
        return body

    # 幂等补齐系统视图，并管理字段可见性与公开 API 支持的筛选。
    def _ensure_views(self, binding: WorkspaceBinding) -> None:
        """补齐并配置 Schema v2 视图。"""

        # 远端字段集合。
        fields = self._list_fields(binding)
        # 字段名称到远端字段对象的索引。
        fields_by_name = {
            str(field.get("field_name", "")): field
            for field in fields
            if str(field.get("field_id", ""))
        }
        # 远端视图名称到视图 ID 的索引。
        views = {
            str(view.get("view_name", "")): str(view.get("view_id", ""))
            for view in self._list_views(binding)
            if str(view.get("view_id", ""))
        }
        for definition in VIEW_DEFINITIONS:
            # 已存在或刚创建的视图 ID。
            view_id = views.get(definition.name)
            if not view_id:
                view_id = self._create_view(binding, definition.name)
                views[definition.name] = view_id
            # 当前视图应隐藏的技术字段 ID。
            hidden_fields = [
                str(field.get("field_id", ""))
                for field_name, field in fields_by_name.items()
                if field_name not in definition.visible_fields
            ]
            # 视图属性包含字段可见性和可选筛选。
            property_data: dict[str, Any] = {"hidden_fields": hidden_fields}
            # 单选筛选由远端选项 ID 构造，避免依赖显示名称作为 API 值。
            filter_info = self._view_filter_info(definition, fields_by_name)
            if filter_info is not None:
                property_data["filter_info"] = filter_info
            self._request_json(
                "PATCH",
                f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/"
                f"{binding.table_id}/views/{view_id}",
                json={
                    "view_name": definition.name,
                    "property": property_data,
                },
            )

    # 把单选筛选声明转换为飞书视图筛选条件。
    @staticmethod
    def _view_filter_info(
        definition: ViewDefinition,
        fields_by_name: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """构造视图筛选属性。"""

        if definition.filter_field_name is None:
            return None
        # 目标筛选字段。
        field = fields_by_name.get(definition.filter_field_name)
        if field is None:
            raise RuntimeError(f"飞书视图筛选字段不存在: {definition.filter_field_name}")
        # 筛选值根据字段类型解析为选项 ID 或保持字面量。
        selected_values = list(definition.filter_values)
        if definition.resolve_option_ids:
            # 远端单选属性。
            property_data = field.get("property") or {}
            # 选项名称到远端 ID 的索引。
            option_ids = {
                str(option.get("name", "")): str(option.get("id", ""))
                for option in property_data.get("options") or []
                if str(option.get("id", ""))
            }
            selected_values = [
                option_ids.get(option_name, "") for option_name in definition.filter_values
            ]
            if not all(selected_values):
                raise RuntimeError(f"飞书视图筛选选项不存在: {definition.name}")
        # 单选字段的多个目标值必须拆成 OR 条件，供应商会忽略同一条件的第二个选项。
        conditions = [
            {
                "field_id": str(field.get("field_id", "")),
                "operator": definition.filter_operator,
                "value": json.dumps([selected_value], ensure_ascii=False),
                "field_type": int(field.get("type", 0)),
            }
            for selected_value in selected_values
        ]
        # 单条件使用 and，多条件使用 or，均符合飞书视图 API 枚举。
        conjunction = "or" if len(conditions) > 1 else "and"
        return {"conjunction": conjunction, "conditions": conditions}

    # 读取全部系统和用户视图。
    def _list_views(self, binding: WorkspaceBinding) -> list[dict[str, Any]]:
        """读取数据表视图。"""

        # 视图响应数据。
        data = self._request_json(
            "GET",
            f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/{binding.table_id}/views",
            params={"page_size": "100"},
        )
        # 仅接收对象视图。
        return [item for item in data.get("items") or [] if isinstance(item, dict)]

    # 新增一个网格视图并返回远端 ID。
    def _create_view(self, binding: WorkspaceBinding, view_name: str) -> str:
        """创建一个系统视图。"""

        # 新建视图响应数据。
        data = self._request_json(
            "POST",
            f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/{binding.table_id}/views",
            json={"view_name": view_name, "view_type": "grid"},
        )
        # 新建视图对象。
        view = data.get("view") or {}
        # 新建视图 ID。
        view_id = str(view.get("view_id", ""))
        if not view_id:
            raise RuntimeError(f"飞书创建视图 {view_name} 响应缺少 view_id")
        return view_id

    # 读取全部字段。
    def _list_fields(self, binding: WorkspaceBinding) -> list[dict[str, Any]]:
        """读取数据表字段。"""

        # 字段响应数据。
        data = self._request_json(
            "GET",
            f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/{binding.table_id}/fields",
            params={"page_size": "100"},
        )
        # 仅接收对象字段。
        return [item for item in data.get("items") or [] if isinstance(item, dict)]

    # 新增一个应用管理字段。
    def _create_field(self, binding: WorkspaceBinding, field: dict[str, Any]) -> None:
        """新增字段。"""

        self._request_json(
            "POST",
            f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/{binding.table_id}/fields",
            json=field,
        )

    # 在单页记录中查找匹配的内容 ID。
    @staticmethod
    def _matching_record_id(records: object, content_id: str) -> str | None:
        """返回当前页中匹配的记录 ID。"""

        if not isinstance(records, list):
            return None
        for record in records:
            if not isinstance(record, dict):
                continue
            # 当前记录字段。
            record_fields = record.get("fields") or {}
            if str(record_fields.get(CONTENT_ID_FIELD, "")) == content_id:
                return str(record.get("record_id", "")) or None
        return None

    # 分页扫描内容 ID，覆盖外部成功而内部事务失败后的幂等重试。
    def _find_record_id(self, binding: WorkspaceBinding, content_id: str) -> str | None:
        """按内容 ID 查找已有记录。"""

        # 下一页令牌。
        page_token: str | None = None
        while True:
            # 分页查询参数。
            params = {"page_size": "500"}
            if page_token:
                params["page_token"] = page_token
            # 当前页数据。
            data = self._request_json(
                "GET",
                f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/"
                f"{binding.table_id}/records",
                params=params,
            )
            # 当前页匹配的记录 ID。
            record_id = self._matching_record_id(data.get("items"), content_id)
            if record_id is not None:
                return record_id
            if not data.get("has_more"):
                return None
            page_token = str(data.get("page_token", "")) or None
            if page_token is None:
                return None

    # 分页检查指定记录 ID，避免依赖供应商的“记录不存在”错误码。
    def _record_id_exists(self, binding: WorkspaceBinding, record_id: str) -> bool:
        """只在当前数据表仍包含指定记录时返回真。"""

        # 下一页令牌。
        page_token: str | None = None
        while True:
            # 当前页查询参数。
            params = {"page_size": "500"}
            if page_token:
                params["page_token"] = page_token
            # 当前页记录数据。
            data = self._request_json(
                "GET",
                f"/open-apis/bitable/v1/apps/{binding.workspace_id}/tables/"
                f"{binding.table_id}/records",
                params=params,
            )
            # 当前页所有有效记录 ID。
            current_record_ids = {
                str(item.get("record_id", ""))
                for item in data.get("items") or []
                if isinstance(item, dict)
            }
            if record_id in current_record_ids:
                return True
            if not data.get("has_more"):
                return False
            page_token = str(data.get("page_token", "")) or None
            if page_token is None:
                return False

    # 构造稳定外部引用。
    @staticmethod
    def _archive_result(binding: WorkspaceBinding, record_id: str) -> ArchiveResult:
        """生成用户记录链接。"""

        # 工作区 URL 结构。
        url_parts = urlsplit(binding.workspace_url)
        # 合并而非覆盖飞书原有查询参数。
        query = dict(parse_qsl(url_parts.query, keep_blank_values=True))
        query.update({"table": binding.table_id, "record": record_id})
        # 最终记录链接。
        record_url = urlunsplit(
            (url_parts.scheme, url_parts.netloc, url_parts.path, urlencode(query), "")
        )
        return ArchiveResult(
            provider=binding.provider,
            workspace_id=binding.workspace_id,
            record_id=record_id,
            record_url=record_url,
        )

    # 获取并缓存 tenant_access_token。
    def _access_token(self) -> str:
        """返回有效租户访问令牌。"""

        # 当前单调时钟值。
        now = time.monotonic()
        if self._tenant_token and now < self._token_expires_at:
            return self._tenant_token
        # 鉴权响应。
        response = self._client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self._settings.app_id,
                "app_secret": self._settings.app_secret.get_secret_value(),
            },
        )
        response.raise_for_status()
        # 鉴权载荷。
        payload = response.json()
        # 兼容鉴权接口的顶层 code。
        self._raise_for_business_error(payload)
        # 新租户令牌。
        token = str(payload.get("tenant_access_token", ""))
        if not token:
            raise RuntimeError("飞书鉴权响应缺少 tenant_access_token")
        # 令牌有效秒数。
        expire_seconds = int(payload.get("expire", 7200))
        self._tenant_token = token
        self._token_expires_at = now + max(expire_seconds - 60, 60)
        return token

    # 执行带租户身份的 JSON API 调用。
    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """返回飞书响应 data 对象。"""

        # 带 Bearer 令牌的请求头。
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        # 飞书业务响应。
        response = self._client.request(
            method,
            f"https://open.feishu.cn{path}",
            headers=headers,
            json=json,
            params=params,
        )
        response.raise_for_status()
        # 顶层响应载荷。
        payload = response.json()
        self._raise_for_business_error(payload)
        # data 节点可能为空。
        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise RuntimeError("飞书 API 响应 data 不是对象")
        return data

    # 将飞书非零 code 转换为稳定异常。
    @staticmethod
    def _raise_for_business_error(payload: dict[str, Any]) -> None:
        """检查飞书业务状态码。"""

        # 飞书成功码为 0。
        code = int(payload.get("code", 0))
        if code != 0:
            # 服务端消息不包含请求头或本地密钥，可安全作为诊断摘要。
            message = str(payload.get("msg", "unknown error"))[:500]
            raise FeishuApiError(code, message)
