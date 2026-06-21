from copy import deepcopy

import gradio as gr
import pandas as pd
import yaml
from ktem.app import BasePage
from ktem.utils.file import YAMLNoDateSafeLoader
from theflow.utils.modules import deserialize

from .manager import embedding_models_manager


def format_description(cls):
    params = cls.describe()["params"]
    params_lines = ["| 名称 | 类型 | 描述 |", "| --- | --- | --- |"]
    for key, value in params.items():
        if isinstance(value["auto_callback"], str):
            continue
        params_lines.append(f"| {key} | {value['type']} | {value['help']} |")
    return f"{cls.__doc__}\n\n" + "\n".join(params_lines)


EMBEDDING_DIRECT_FIELD_KEYS = {
    "api_key",
    "openai_api_key",
    "google_api_key",
    "cohere_api_key",
    "base_url",
    "openai_api_base",
    "azure_endpoint",
    "endpoint_url",
    "model",
    "model_name",
    "azure_deployment",
    "deployment",
    "api_version",
    "openai_api_version",
    "organization",
    "timeout",
    "request_timeout",
    "max_retries",
    "dimensions",
    "context_length",
    "batch_size",
    "parallel",
    "user_agent",
    "normalize",
    "truncate",
    "azure_ad_token",
    "azure_ad_token_provider",
}


def _load_yaml_spec(spec_text):
    spec = yaml.load(spec_text or "", Loader=YAMLNoDateSafeLoader)
    return spec or {}


def _dump_extra_spec(spec):
    if not spec:
        return ""
    return yaml.dump(spec, allow_unicode=True)


def _clean_spec(spec):
    return {key: value for key, value in spec.items() if value not in ("", None)}


def _apply_first_supported(spec, desc, fields, value):
    if value in ("", None):
        return

    params = desc["params"]
    for field in fields:
        if field in params:
            spec[field] = value
            return


def _get_first_supported_default(desc, fields, fallback=None):
    params = desc["params"]
    for field in fields:
        if field in params:
            return params[field].get("default", fallback)
    return fallback


def _get_direct_defaults(desc):
    return {
        "api_key": _get_first_supported_default(
            desc,
            ["api_key", "openai_api_key", "google_api_key", "cohere_api_key"],
            "",
        )
        or "",
        "base_url": _get_first_supported_default(
            desc,
            ["base_url", "openai_api_base", "azure_endpoint", "endpoint_url"],
            "",
        )
        or "",
        "model": _get_first_supported_default(
            desc,
            ["model", "model_name", "azure_deployment", "deployment"],
            "",
        )
        or "",
        "api_version": _get_first_supported_default(
            desc, ["api_version", "openai_api_version"], ""
        )
        or "",
        "organization": _get_first_supported_default(desc, ["organization"], "") or "",
        "timeout": _get_first_supported_default(
            desc, ["timeout", "request_timeout"], None
        ),
        "max_retries": _get_first_supported_default(desc, ["max_retries"], None),
        "dimensions": _get_first_supported_default(desc, ["dimensions"], None),
        "context_length": _get_first_supported_default(desc, ["context_length"], None),
        "batch_size": _get_first_supported_default(desc, ["batch_size"], None),
        "parallel": _get_first_supported_default(desc, ["parallel"], None),
        "user_agent": _get_first_supported_default(desc, ["user_agent"], "") or "",
        "normalize": bool(_get_first_supported_default(desc, ["normalize"], False)),
        "truncate": bool(_get_first_supported_default(desc, ["truncate"], False)),
        "azure_ad_token": _get_first_supported_default(
            desc, ["azure_ad_token"], ""
        )
        or "",
        "azure_ad_token_provider": _get_first_supported_default(
            desc, ["azure_ad_token_provider"], ""
        )
        or "",
    }


def build_direct_embedding_spec(
    vendor_name,
    api_key,
    base_url,
    model,
    api_version,
    organization,
    timeout,
    max_retries,
    dimensions,
    context_length,
    batch_size,
    parallel,
    user_agent,
    normalize,
    truncate,
    azure_ad_token,
    azure_ad_token_provider,
    extra_spec,
):
    if not vendor_name:
        raise ValueError("请选择 Embedding vendor")

    vendor = embedding_models_manager.vendors()[vendor_name]
    desc = vendor.describe()
    spec = _load_yaml_spec(extra_spec)

    if not isinstance(spec, dict):
        raise ValueError("高级 YAML 必须是对象格式")

    _apply_first_supported(
        spec,
        desc,
        ["api_key", "openai_api_key", "google_api_key", "cohere_api_key"],
        api_key,
    )
    _apply_first_supported(
        spec,
        desc,
        ["base_url", "openai_api_base", "azure_endpoint", "endpoint_url"],
        base_url,
    )
    _apply_first_supported(
        spec,
        desc,
        ["model", "model_name", "azure_deployment", "deployment"],
        model,
    )
    _apply_first_supported(
        spec,
        desc,
        ["api_version", "openai_api_version"],
        api_version,
    )
    _apply_first_supported(spec, desc, ["organization"], organization)
    _apply_first_supported(spec, desc, ["timeout", "request_timeout"], timeout)
    _apply_first_supported(spec, desc, ["max_retries"], max_retries)
    _apply_first_supported(spec, desc, ["dimensions"], dimensions)
    _apply_first_supported(spec, desc, ["context_length"], context_length)
    _apply_first_supported(spec, desc, ["batch_size"], batch_size)
    _apply_first_supported(spec, desc, ["parallel"], parallel)
    _apply_first_supported(spec, desc, ["user_agent"], user_agent)
    _apply_first_supported(spec, desc, ["normalize"], normalize)
    _apply_first_supported(spec, desc, ["truncate"], truncate)
    _apply_first_supported(spec, desc, ["azure_ad_token"], azure_ad_token)
    _apply_first_supported(
        spec, desc, ["azure_ad_token_provider"], azure_ad_token_provider
    )

    spec = _clean_spec(spec)
    spec["__type__"] = vendor.__module__ + "." + vendor.__qualname__
    return spec


def direct_fields_from_spec(spec):
    values = {
        "api_key": (
            spec.get("api_key")
            or spec.get("openai_api_key")
            or spec.get("google_api_key")
            or spec.get("cohere_api_key")
            or ""
        ),
        "base_url": (
            spec.get("base_url")
            or spec.get("openai_api_base")
            or spec.get("azure_endpoint")
            or spec.get("endpoint_url")
            or ""
        ),
        "model": (
            spec.get("model")
            or spec.get("model_name")
            or spec.get("azure_deployment")
            or spec.get("deployment")
            or ""
        ),
        "api_version": spec.get("api_version") or spec.get("openai_api_version") or "",
        "organization": spec.get("organization") or "",
        "timeout": spec.get("timeout") or spec.get("request_timeout"),
        "max_retries": spec.get("max_retries"),
        "dimensions": spec.get("dimensions"),
        "context_length": spec.get("context_length"),
        "batch_size": spec.get("batch_size"),
        "parallel": spec.get("parallel"),
        "user_agent": spec.get("user_agent") or "",
        "normalize": spec.get("normalize", False),
        "truncate": spec.get("truncate", False),
        "azure_ad_token": spec.get("azure_ad_token") or "",
        "azure_ad_token_provider": spec.get("azure_ad_token_provider") or "",
    }
    extra_spec = {
        key: value
        for key, value in spec.items()
        if key not in EMBEDDING_DIRECT_FIELD_KEYS and key != "__type__"
    }
    return values, _dump_extra_spec(extra_spec)


class EmbeddingManagement(BasePage):
    def __init__(self, app):
        self._app = app
        self.spec_desc_default = (
            "# Spec 说明\n\n请选择一个 model 查看 spec 说明。"
        )
        self.on_building_ui()

    def on_building_ui(self):
        with gr.Tab(label="查看"):
            self.emb_list = gr.DataFrame(
                headers=["名称", "vendor", "默认"],
                interactive=False,
                column_widths=[30, 40, 30],
            )

            with gr.Column(visible=False) as self._selected_panel:
                self.selected_emb_name = gr.Textbox(value="", visible=False)
                with gr.Row():
                    with gr.Column():
                        self.edit_default = gr.Checkbox(
                            label="设为默认",
                            info=(
                                "将此 Embedding model 设为默认。其他组件未指定 "
                                "Embedding 时，会默认使用此 Embedding。"
                            ),
                        )
                        self.edit_name = gr.Textbox(
                            label="名称",
                            info="编辑以重命名此 Embedding model。",
                        )
                        self.edit_vendor = gr.Dropdown(
                            label="厂商",
                            interactive=False,
                        )
                        self.edit_model = gr.Textbox(label="模型")
                        self.edit_api_key = gr.Textbox(label="API key", type="password")
                        self.edit_base_url = gr.Textbox(label="URL")
                        self.edit_api_version = gr.Textbox(label="API version")
                        self.edit_organization = gr.Textbox(label="Organization")
                        self.edit_timeout = gr.Number(label="Timeout")
                        self.edit_max_retries = gr.Number(label="Max retries")
                        self.edit_dimensions = gr.Number(label="Dimensions")
                        self.edit_context_length = gr.Number(label="Context length")
                        self.edit_batch_size = gr.Number(label="Batch size")
                        self.edit_parallel = gr.Number(label="Parallel")
                        self.edit_user_agent = gr.Textbox(label="User agent")
                        self.edit_normalize = gr.Checkbox(label="Normalize")
                        self.edit_truncate = gr.Checkbox(label="Truncate")
                        self.edit_azure_ad_token = gr.Textbox(
                            label="Azure AD token",
                            type="password",
                        )
                        self.edit_azure_ad_token_provider = gr.Textbox(
                            label="Azure AD token provider"
                        )
                        self.edit_spec = gr.Textbox(
                            label="高级 YAML",
                            info="仅填写上方字段之外的额外参数。",
                            lines=6,
                        )

                        with gr.Accordion(
                            label="测试连接", visible=False, open=False
                        ) as self._check_connection_panel:
                            with gr.Row():
                                with gr.Column(scale=1):
                                    self.btn_test_connection = gr.Button("测试")
                                with gr.Column(scale=4):
                                    self.connection_logs = gr.HTML("日志")

                        with gr.Row(visible=False) as self._selected_panel_btn:
                            with gr.Column():
                                self.btn_edit_save = gr.Button(
                                    "保存", min_width=10, variant="primary"
                                )
                            with gr.Column():
                                self.btn_delete = gr.Button(
                                    "删除", min_width=10, variant="stop"
                                )
                                with gr.Row():
                                    self.btn_delete_yes = gr.Button(
                                        "确认删除",
                                        variant="stop",
                                        visible=False,
                                        min_width=10,
                                    )
                                    self.btn_delete_no = gr.Button(
                                        "取消", visible=False, min_width=10
                                    )
                            with gr.Column():
                                self.btn_close = gr.Button("关闭", min_width=10)

                    with gr.Column():
                        self.edit_spec_desc = gr.Markdown("# Spec 说明")

        with gr.Tab(label="添加"):
            with gr.Row():
                with gr.Column(scale=2):
                    self.name = gr.Textbox(
                        label="名称",
                        info=(
                            "必须唯一且不能为空。该名称用于识别 embedding model。"
                        ),
                    )
                    self.emb_choices = gr.Dropdown(
                        label="厂商",
                        info=(
                            "选择 Embedding model 的 vendor。"
                        ),
                    )
                    self.model = gr.Textbox(label="模型")
                    self.api_key = gr.Textbox(label="API key", type="password")
                    self.base_url = gr.Textbox(label="URL")
                    self.api_version = gr.Textbox(label="API version")
                    self.organization = gr.Textbox(label="Organization")
                    self.timeout = gr.Number(label="Timeout")
                    self.max_retries = gr.Number(label="Max retries")
                    self.dimensions = gr.Number(label="Dimensions")
                    self.context_length = gr.Number(label="Context length")
                    self.batch_size = gr.Number(label="Batch size")
                    self.parallel = gr.Number(label="Parallel")
                    self.user_agent = gr.Textbox(label="User agent")
                    self.normalize = gr.Checkbox(label="Normalize")
                    self.truncate = gr.Checkbox(label="Truncate")
                    self.azure_ad_token = gr.Textbox(
                        label="Azure AD token",
                        type="password",
                    )
                    self.azure_ad_token_provider = gr.Textbox(
                        label="Azure AD token provider"
                    )
                    self.spec = gr.Textbox(
                        label="高级 YAML",
                        info="仅填写上方字段之外的额外参数。",
                        lines=6,
                    )
                    self.default = gr.Checkbox(
                        label="设为默认",
                        info=(
                            "将此 Embedding model 设为默认。其他组件未指定 "
                            "Embedding 时，会默认使用此 Embedding。"
                        ),
                    )
                    self.btn_new = gr.Button("添加", variant="primary")

                with gr.Column(scale=3):
                    self.spec_desc = gr.Markdown(self.spec_desc_default)

    def _on_app_created(self):
        """Called when the app is created"""
        self._app.app.load(
            self.list_embeddings,
            inputs=[],
            outputs=[self.emb_list],
        )
        self._app.app.load(
            lambda: gr.update(choices=list(embedding_models_manager.vendors().keys())),
            outputs=[self.emb_choices],
        )
        self._app.app.load(
            lambda: gr.update(choices=list(embedding_models_manager.vendors().keys())),
            outputs=[self.edit_vendor],
        )

    def on_emb_vendor_change(self, vendor):
        vendor = embedding_models_manager.vendors()[vendor]

        required: dict = {}
        desc = vendor.describe()
        for key, value in desc["params"].items():
            if key in EMBEDDING_DIRECT_FIELD_KEYS:
                continue
            if value.get("required", False):
                required[key] = value.get("default", None)

        direct_defaults = _get_direct_defaults(desc)
        return (
            direct_defaults["api_key"],
            direct_defaults["base_url"],
            direct_defaults["model"],
            direct_defaults["api_version"],
            direct_defaults["organization"],
            direct_defaults["timeout"],
            direct_defaults["max_retries"],
            direct_defaults["dimensions"],
            direct_defaults["context_length"],
            direct_defaults["batch_size"],
            direct_defaults["parallel"],
            direct_defaults["user_agent"],
            direct_defaults["normalize"],
            direct_defaults["truncate"],
            direct_defaults["azure_ad_token"],
            direct_defaults["azure_ad_token_provider"],
            _dump_extra_spec(required),
            format_description(vendor),
        )

    def on_register_events(self):
        self.emb_choices.select(
            self.on_emb_vendor_change,
            inputs=[self.emb_choices],
            outputs=[
                self.api_key,
                self.base_url,
                self.model,
                self.api_version,
                self.organization,
                self.timeout,
                self.max_retries,
                self.dimensions,
                self.context_length,
                self.batch_size,
                self.parallel,
                self.user_agent,
                self.normalize,
                self.truncate,
                self.azure_ad_token,
                self.azure_ad_token_provider,
                self.spec,
                self.spec_desc,
            ],
        )
        self.btn_new.click(
            self.create_emb,
            inputs=[
                self.name,
                self.emb_choices,
                self.api_key,
                self.base_url,
                self.model,
                self.api_version,
                self.organization,
                self.timeout,
                self.max_retries,
                self.dimensions,
                self.context_length,
                self.batch_size,
                self.parallel,
                self.user_agent,
                self.normalize,
                self.truncate,
                self.azure_ad_token,
                self.azure_ad_token_provider,
                self.spec,
                self.default,
            ],
            outputs=None,
        ).success(self.list_embeddings, inputs=[], outputs=[self.emb_list]).success(
            lambda: (
                "",
                None,
                "",
                "",
                "",
                "",
                "",
                None,
                None,
                None,
                None,
                None,
                None,
                "",
                False,
                False,
                "",
                "",
                "",
                False,
                self.spec_desc_default,
            ),
            outputs=[
                self.name,
                self.emb_choices,
                self.api_key,
                self.base_url,
                self.model,
                self.api_version,
                self.organization,
                self.timeout,
                self.max_retries,
                self.dimensions,
                self.context_length,
                self.batch_size,
                self.parallel,
                self.user_agent,
                self.normalize,
                self.truncate,
                self.azure_ad_token,
                self.azure_ad_token_provider,
                self.spec,
                self.default,
                self.spec_desc,
            ],
        )
        self.emb_list.select(
            self.select_emb,
            inputs=self.emb_list,
            outputs=[self.selected_emb_name],
            show_progress="hidden",
        )
        self.selected_emb_name.change(
            self.on_selected_emb_change,
            inputs=[self.selected_emb_name],
            outputs=[
                self._selected_panel,
                self._selected_panel_btn,
                self._check_connection_panel,
                # delete section
                self.btn_delete,
                self.btn_delete_yes,
                self.btn_delete_no,
                # edit section
                self.edit_name,
                self.edit_vendor,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model,
                self.edit_api_version,
                self.edit_organization,
                self.edit_timeout,
                self.edit_max_retries,
                self.edit_dimensions,
                self.edit_context_length,
                self.edit_batch_size,
                self.edit_parallel,
                self.edit_user_agent,
                self.edit_normalize,
                self.edit_truncate,
                self.edit_azure_ad_token,
                self.edit_azure_ad_token_provider,
                self.edit_spec,
                self.edit_spec_desc,
                self.edit_default,
            ],
            show_progress="hidden",
        ).success(lambda: gr.update(value=""), outputs=[self.connection_logs])

        self.btn_delete.click(
            self.on_btn_delete_click,
            inputs=[],
            outputs=[self.btn_delete, self.btn_delete_yes, self.btn_delete_no],
            show_progress="hidden",
        )
        self.btn_delete_yes.click(
            self.delete_emb,
            inputs=[self.selected_emb_name],
            outputs=[self.selected_emb_name],
            show_progress="hidden",
        ).then(
            self.list_embeddings,
            inputs=[],
            outputs=[self.emb_list],
        )
        self.btn_delete_no.click(
            lambda: (
                gr.update(visible=True),
                gr.update(visible=False),
                gr.update(visible=False),
            ),
            inputs=[],
            outputs=[self.btn_delete, self.btn_delete_yes, self.btn_delete_no],
            show_progress="hidden",
        )
        self.btn_edit_save.click(
            self.save_emb,
            inputs=[
                self.selected_emb_name,
                self.edit_name,
                self.edit_vendor,
                self.edit_default,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model,
                self.edit_api_version,
                self.edit_organization,
                self.edit_timeout,
                self.edit_max_retries,
                self.edit_dimensions,
                self.edit_context_length,
                self.edit_batch_size,
                self.edit_parallel,
                self.edit_user_agent,
                self.edit_normalize,
                self.edit_truncate,
                self.edit_azure_ad_token,
                self.edit_azure_ad_token_provider,
                self.edit_spec,
            ],
            outputs=[self.selected_emb_name],
            show_progress="hidden",
        ).then(
            self.list_embeddings,
            inputs=[],
            outputs=[self.emb_list],
        )
        self.btn_close.click(
            lambda: "",
            outputs=[self.selected_emb_name],
        )

        self.btn_test_connection.click(
            self.check_connection,
            inputs=[
                self.selected_emb_name,
                self.edit_vendor,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model,
                self.edit_api_version,
                self.edit_organization,
                self.edit_timeout,
                self.edit_max_retries,
                self.edit_dimensions,
                self.edit_context_length,
                self.edit_batch_size,
                self.edit_parallel,
                self.edit_user_agent,
                self.edit_normalize,
                self.edit_truncate,
                self.edit_azure_ad_token,
                self.edit_azure_ad_token_provider,
                self.edit_spec,
            ],
            outputs=[self.connection_logs],
        )

    def create_emb(
        self,
        name,
        choices,
        api_key,
        base_url,
        model,
        api_version,
        organization,
        timeout,
        max_retries,
        dimensions,
        context_length,
        batch_size,
        parallel,
        user_agent,
        normalize,
        truncate,
        azure_ad_token,
        azure_ad_token_provider,
        spec,
        default,
    ):
        try:
            name = name.strip()
            spec = build_direct_embedding_spec(
                choices,
                api_key,
                base_url,
                model,
                api_version,
                organization,
                timeout,
                max_retries,
                dimensions,
                context_length,
                batch_size,
                parallel,
                user_agent,
                normalize,
                truncate,
                azure_ad_token,
                azure_ad_token_provider,
                spec,
            )

            embedding_models_manager.add(name, spec=spec, default=default)
            gr.Info(f'Embedding model "{name}" 创建成功')
        except ValueError as e:
            raise gr.Error(str(e))
        except Exception as e:
            raise gr.Error(f"创建 Embedding model '{name}' 失败：{e}")

    def list_embeddings(self):
        """List the Embedding models"""
        items = []
        for item in embedding_models_manager.info().values():
            record = {}
            record["name"] = item["name"]
            record["vendor"] = item["spec"].get("__type__", "-").split(".")[-1]
            record["default"] = item["default"]
            items.append(record)

        if items:
            emb_list = pd.DataFrame.from_records(items)
        else:
            emb_list = pd.DataFrame.from_records(
                [{"name": "-", "vendor": "-", "default": "-"}]
            )

        return emb_list

    def select_emb(self, emb_list, ev: gr.SelectData):
        if ev.value == "-" and ev.index[0] == 0:
            gr.Info("未加载 embedding model，请先添加")
            return ""

        if not ev.selected:
            return ""

        return emb_list["name"][ev.index[0]]

    def on_selected_emb_change(self, selected_emb_name):
        if selected_emb_name == "":
            _selected_panel = gr.update(visible=False)
            _selected_panel_btn = gr.update(visible=False)
            _check_connection_panel = gr.update(visible=False)
            btn_delete = gr.update(visible=True)
            btn_delete_yes = gr.update(visible=False)
            btn_delete_no = gr.update(visible=False)
            edit_name = gr.update(value="")
            edit_vendor = gr.update(value=None)
            edit_api_key = gr.update(value="")
            edit_base_url = gr.update(value="")
            edit_model = gr.update(value="")
            edit_api_version = gr.update(value="")
            edit_organization = gr.update(value="")
            edit_timeout = gr.update(value=None)
            edit_max_retries = gr.update(value=None)
            edit_dimensions = gr.update(value=None)
            edit_context_length = gr.update(value=None)
            edit_batch_size = gr.update(value=None)
            edit_parallel = gr.update(value=None)
            edit_user_agent = gr.update(value="")
            edit_normalize = gr.update(value=False)
            edit_truncate = gr.update(value=False)
            edit_azure_ad_token = gr.update(value="")
            edit_azure_ad_token_provider = gr.update(value="")
            edit_spec = gr.update(value="")
            edit_spec_desc = gr.update(value="")
            edit_default = gr.update(value=False)
        else:
            _selected_panel = gr.update(visible=True)
            _selected_panel_btn = gr.update(visible=True)
            _check_connection_panel = gr.update(visible=True, open=False)
            btn_delete = gr.update(visible=True)
            btn_delete_yes = gr.update(visible=False)
            btn_delete_no = gr.update(visible=False)

            info = deepcopy(embedding_models_manager.info()[selected_emb_name])
            vendor_str = info["spec"].pop("__type__", "-").split(".")[-1]
            vendor = embedding_models_manager.vendors()[vendor_str]
            direct_values, extra_spec = direct_fields_from_spec(info["spec"])

            edit_name = selected_emb_name
            edit_vendor = vendor_str
            edit_api_key = direct_values["api_key"]
            edit_base_url = direct_values["base_url"]
            edit_model = direct_values["model"]
            edit_api_version = direct_values["api_version"]
            edit_organization = direct_values["organization"]
            edit_timeout = direct_values["timeout"]
            edit_max_retries = direct_values["max_retries"]
            edit_dimensions = direct_values["dimensions"]
            edit_context_length = direct_values["context_length"]
            edit_batch_size = direct_values["batch_size"]
            edit_parallel = direct_values["parallel"]
            edit_user_agent = direct_values["user_agent"]
            edit_normalize = direct_values["normalize"]
            edit_truncate = direct_values["truncate"]
            edit_azure_ad_token = direct_values["azure_ad_token"]
            edit_azure_ad_token_provider = direct_values["azure_ad_token_provider"]
            edit_spec = extra_spec
            edit_spec_desc = format_description(vendor)
            edit_default = info["default"]

        return (
            _selected_panel,
            _selected_panel_btn,
            _check_connection_panel,
            btn_delete,
            btn_delete_yes,
            btn_delete_no,
            edit_name,
            edit_vendor,
            edit_api_key,
            edit_base_url,
            edit_model,
            edit_api_version,
            edit_organization,
            edit_timeout,
            edit_max_retries,
            edit_dimensions,
            edit_context_length,
            edit_batch_size,
            edit_parallel,
            edit_user_agent,
            edit_normalize,
            edit_truncate,
            edit_azure_ad_token,
            edit_azure_ad_token_provider,
            edit_spec,
            edit_spec_desc,
            edit_default,
        )

    def on_btn_delete_click(self):
        btn_delete = gr.update(visible=False)
        btn_delete_yes = gr.update(visible=True)
        btn_delete_no = gr.update(visible=True)

        return btn_delete, btn_delete_yes, btn_delete_no

    def check_connection(
        self,
        selected_emb_name,
        vendor,
        api_key,
        base_url,
        model,
        api_version,
        organization,
        timeout,
        max_retries,
        dimensions,
        context_length,
        batch_size,
        parallel,
        user_agent,
        normalize,
        truncate,
        azure_ad_token,
        azure_ad_token_provider,
        extra_spec,
    ):
        log_content: str = ""
        try:
            log_content += f"- 正在测试 model：{selected_emb_name}<br>"
            yield log_content

            spec = build_direct_embedding_spec(
                vendor,
                api_key,
                base_url,
                model,
                api_version,
                organization,
                timeout,
                max_retries,
                dimensions,
                context_length,
                batch_size,
                parallel,
                user_agent,
                normalize,
                truncate,
                azure_ad_token,
                azure_ad_token_provider,
                extra_spec,
            )
            emb = deserialize(spec, safe=False)

            if emb is None:
                raise Exception(f"找不到 model：{selected_emb_name}")

            log_content += "- 正在发送消息 `Hi`<br>"
            yield log_content
            _ = emb("Hi")

            log_content += (
                "<mark style='background: green; color: white'>- 连接成功。"
                "</mark><br>"
            )
            yield log_content

            gr.Info(f"Embedding {selected_emb_name} 连接成功")
        except Exception as e:
            print(e)
            log_content += (
                f"<mark style='color: yellow; background: red'>- 连接失败。"
                f"错误信息：\n {str(e)}</mark>"
            )
            yield log_content

        return log_content

    def save_emb(
        self,
        selected_emb_name,
        edit_name,
        vendor,
        default,
        api_key,
        base_url,
        model,
        api_version,
        organization,
        timeout,
        max_retries,
        dimensions,
        context_length,
        batch_size,
        parallel,
        user_agent,
        normalize,
        truncate,
        azure_ad_token,
        azure_ad_token_provider,
        spec,
    ):
        try:
            new_name = edit_name.strip()
            spec = build_direct_embedding_spec(
                vendor,
                api_key,
                base_url,
                model,
                api_version,
                organization,
                timeout,
                max_retries,
                dimensions,
                context_length,
                batch_size,
                parallel,
                user_agent,
                normalize,
                truncate,
                azure_ad_token,
                azure_ad_token_provider,
                spec,
            )
            embedding_models_manager.update(
                selected_emb_name, spec=spec, default=default, new_name=new_name
            )
            final_name = (
                new_name if new_name != selected_emb_name else selected_emb_name
            )
            gr.Info(f'Embedding model "{final_name}" 保存成功')
            return final_name
        except ValueError as e:
            raise gr.Error(str(e))
        except Exception as e:
            raise gr.Error(f'保存 Embedding model "{selected_emb_name}" 失败：{e}')

    def delete_emb(self, selected_emb_name):
        try:
            embedding_models_manager.delete(selected_emb_name)
        except Exception as e:
            gr.Error(f'删除 Embedding model "{selected_emb_name}" 失败：{e}')
            return selected_emb_name

        return ""
