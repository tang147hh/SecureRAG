from copy import deepcopy

import gradio as gr
import pandas as pd
import yaml
from ktem.app import BasePage
from ktem.utils.file import YAMLNoDateSafeLoader
from theflow.utils.modules import deserialize

from kotaemon.base import Document

from .manager import reranking_models_manager


def format_description(cls):
    params = cls.describe()["params"]
    params_lines = ["| 名称 | 类型 | 描述 |", "| --- | --- | --- |"]
    for key, value in params.items():
        if isinstance(value["auto_callback"], str):
            continue
        params_lines.append(f"| {key} | {value['type']} | {value['help']} |")
    return f"{cls.__doc__}\n\n" + "\n".join(params_lines)


RERANKING_DIRECT_FIELD_KEYS = {
    "api_key",
    "cohere_api_key",
    "base_url",
    "endpoint_url",
    "model_name",
    "is_truncated",
    "max_tokens",
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
            desc, ["api_key", "cohere_api_key"], ""
        )
        or "",
        "base_url": _get_first_supported_default(
            desc, ["base_url", "endpoint_url"], ""
        )
        or "",
        "model_name": _get_first_supported_default(desc, ["model_name"], "") or "",
        "is_truncated": bool(
            _get_first_supported_default(desc, ["is_truncated"], False)
        ),
        "max_tokens": _get_first_supported_default(desc, ["max_tokens"], None),
    }


def build_direct_reranking_spec(
    vendor_name,
    api_key,
    base_url,
    model_name,
    is_truncated,
    max_tokens,
    extra_spec,
):
    if not vendor_name:
        raise ValueError("请选择 Reranking vendor")

    vendor = reranking_models_manager.vendors()[vendor_name]
    desc = vendor.describe()
    spec = _load_yaml_spec(extra_spec)

    if not isinstance(spec, dict):
        raise ValueError("高级 YAML 必须是对象格式")

    _apply_first_supported(spec, desc, ["api_key", "cohere_api_key"], api_key)
    _apply_first_supported(spec, desc, ["base_url", "endpoint_url"], base_url)
    _apply_first_supported(spec, desc, ["model_name"], model_name)
    _apply_first_supported(spec, desc, ["is_truncated"], is_truncated)
    _apply_first_supported(spec, desc, ["max_tokens"], max_tokens)

    spec = _clean_spec(spec)
    spec["__type__"] = vendor.__module__ + "." + vendor.__qualname__
    return spec


def direct_fields_from_spec(spec):
    values = {
        "api_key": spec.get("api_key") or spec.get("cohere_api_key") or "",
        "base_url": spec.get("base_url") or spec.get("endpoint_url") or "",
        "model_name": spec.get("model_name") or "",
        "is_truncated": spec.get("is_truncated", False),
        "max_tokens": spec.get("max_tokens"),
    }
    extra_spec = {
        key: value
        for key, value in spec.items()
        if key not in RERANKING_DIRECT_FIELD_KEYS and key != "__type__"
    }
    return values, _dump_extra_spec(extra_spec)


class RerankingManagement(BasePage):
    def __init__(self, app):
        self._app = app
        self.spec_desc_default = (
            "# Spec 说明\n\n请选择一个 model 查看 spec 说明。"
        )
        self.on_building_ui()

    def on_building_ui(self):
        with gr.Tab(label="查看"):
            self.rerank_list = gr.DataFrame(
                headers=["名称", "vendor", "默认"],
                interactive=False,
                column_widths=[30, 40, 30],
            )

            with gr.Column(visible=False) as self._selected_panel:
                self.selected_rerank_name = gr.Textbox(value="", visible=False)
                with gr.Row():
                    with gr.Column():
                        self.edit_default = gr.Checkbox(
                            label="设为默认",
                            info=(
                                "将此 Reranking model 设为默认。其他组件未指定 "
                                "Reranking 时，会默认使用此 Reranking。"
                            ),
                        )
                        self.edit_name = gr.Textbox(
                            label="名称",
                            info="编辑以重命名此 Reranking model。",
                        )
                        self.edit_vendor = gr.Dropdown(
                            label="厂商",
                            interactive=False,
                        )
                        self.edit_model_name = gr.Textbox(label="模型")
                        self.edit_api_key = gr.Textbox(label="API key", type="password")
                        self.edit_base_url = gr.Textbox(label="URL")
                        self.edit_is_truncated = gr.Checkbox(label="Is truncated")
                        self.edit_max_tokens = gr.Number(label="Max tokens")
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
                            "必须唯一且不能为空。该名称用于识别 reranking model。"
                        ),
                    )
                    self.rerank_choices = gr.Dropdown(
                        label="厂商",
                        info=(
                            "选择 Reranking model 的 vendor。"
                        ),
                    )
                    self.model_name = gr.Textbox(label="模型")
                    self.api_key = gr.Textbox(label="API key", type="password")
                    self.base_url = gr.Textbox(label="URL")
                    self.is_truncated = gr.Checkbox(label="Is truncated")
                    self.max_tokens = gr.Number(label="Max tokens")
                    self.spec = gr.Textbox(
                        label="高级 YAML",
                        info="仅填写上方字段之外的额外参数。",
                        lines=6,
                    )
                    self.default = gr.Checkbox(
                        label="设为默认",
                        info=(
                            "将此 Reranking model 设为默认。其他组件未指定 "
                            "Reranking 时，会默认使用此 Reranking。"
                        ),
                    )
                    self.btn_new = gr.Button("添加", variant="primary")

                with gr.Column(scale=3):
                    self.spec_desc = gr.Markdown(self.spec_desc_default)

    def _on_app_created(self):
        """Called when the app is created"""
        self._app.app.load(
            self.list_rerankings,
            inputs=[],
            outputs=[self.rerank_list],
        )
        self._app.app.load(
            lambda: gr.update(choices=list(reranking_models_manager.vendors().keys())),
            outputs=[self.rerank_choices],
        )
        self._app.app.load(
            lambda: gr.update(choices=list(reranking_models_manager.vendors().keys())),
            outputs=[self.edit_vendor],
        )

    def on_rerank_vendor_change(self, vendor):
        vendor = reranking_models_manager.vendors()[vendor]

        required: dict = {}
        desc = vendor.describe()
        for key, value in desc["params"].items():
            if key in RERANKING_DIRECT_FIELD_KEYS:
                continue
            if value.get("required", False):
                required[key] = value.get("default", None)

        direct_defaults = _get_direct_defaults(desc)
        return (
            direct_defaults["api_key"],
            direct_defaults["base_url"],
            direct_defaults["model_name"],
            direct_defaults["is_truncated"],
            direct_defaults["max_tokens"],
            _dump_extra_spec(required),
            format_description(vendor),
        )

    def on_register_events(self):
        self.rerank_choices.select(
            self.on_rerank_vendor_change,
            inputs=[self.rerank_choices],
            outputs=[
                self.api_key,
                self.base_url,
                self.model_name,
                self.is_truncated,
                self.max_tokens,
                self.spec,
                self.spec_desc,
            ],
        )
        self.btn_new.click(
            self.create_rerank,
            inputs=[
                self.name,
                self.rerank_choices,
                self.api_key,
                self.base_url,
                self.model_name,
                self.is_truncated,
                self.max_tokens,
                self.spec,
                self.default,
            ],
            outputs=None,
        ).success(self.list_rerankings, inputs=[], outputs=[self.rerank_list]).success(
            lambda: (
                "",
                None,
                "",
                "",
                "",
                False,
                None,
                "",
                self.spec_desc_default,
            ),
            outputs=[
                self.name,
                self.rerank_choices,
                self.api_key,
                self.base_url,
                self.model_name,
                self.is_truncated,
                self.max_tokens,
                self.spec,
                self.default,
                self.spec_desc,
            ],
        )
        self.rerank_list.select(
            self.select_rerank,
            inputs=self.rerank_list,
            outputs=[self.selected_rerank_name],
            show_progress="hidden",
        )
        self.selected_rerank_name.change(
            self.on_selected_rerank_change,
            inputs=[self.selected_rerank_name],
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
                self.edit_model_name,
                self.edit_is_truncated,
                self.edit_max_tokens,
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
            self.delete_rerank,
            inputs=[self.selected_rerank_name],
            outputs=[self.selected_rerank_name],
            show_progress="hidden",
        ).then(
            self.list_rerankings,
            inputs=[],
            outputs=[self.rerank_list],
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
            self.save_rerank,
            inputs=[
                self.selected_rerank_name,
                self.edit_name,
                self.edit_vendor,
                self.edit_default,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model_name,
                self.edit_is_truncated,
                self.edit_max_tokens,
                self.edit_spec,
            ],
            outputs=[self.selected_rerank_name],
            show_progress="hidden",
        ).then(
            self.list_rerankings,
            inputs=[],
            outputs=[self.rerank_list],
        )
        self.btn_close.click(lambda: "", outputs=[self.selected_rerank_name])

        self.btn_test_connection.click(
            self.check_connection,
            inputs=[
                self.selected_rerank_name,
                self.edit_vendor,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model_name,
                self.edit_is_truncated,
                self.edit_max_tokens,
                self.edit_spec,
            ],
            outputs=[self.connection_logs],
        )

    def create_rerank(
        self,
        name,
        choices,
        api_key,
        base_url,
        model_name,
        is_truncated,
        max_tokens,
        spec,
        default,
    ):
        try:
            name = name.strip()
            spec = build_direct_reranking_spec(
                choices,
                api_key,
                base_url,
                model_name,
                is_truncated,
                max_tokens,
                spec,
            )

            reranking_models_manager.add(name, spec=spec, default=default)
            gr.Info(f'Reranking model "{name}" 创建成功')
        except ValueError as e:
            raise gr.Error(str(e))
        except Exception as e:
            raise gr.Error(f"创建 Reranking model '{name}' 失败：{e}")

    def list_rerankings(self):
        """List the Reranking models"""
        items = []
        for item in reranking_models_manager.info().values():
            record = {}
            record["name"] = item["name"]
            record["vendor"] = item["spec"].get("__type__", "-").split(".")[-1]
            record["default"] = item["default"]
            items.append(record)

        if items:
            rerank_list = pd.DataFrame.from_records(items)
        else:
            rerank_list = pd.DataFrame.from_records(
                [{"name": "-", "vendor": "-", "default": "-"}]
            )

        return rerank_list

    def select_rerank(self, rerank_list, ev: gr.SelectData):
        if ev.value == "-" and ev.index[0] == 0:
            gr.Info("未加载 reranking model，请先添加")
            return ""

        if not ev.selected:
            return ""

        return rerank_list["name"][ev.index[0]]

    def on_selected_rerank_change(self, selected_rerank_name):
        if selected_rerank_name == "":
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
            edit_model_name = gr.update(value="")
            edit_is_truncated = gr.update(value=False)
            edit_max_tokens = gr.update(value=None)
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

            info = deepcopy(reranking_models_manager.info()[selected_rerank_name])
            vendor_str = info["spec"].pop("__type__", "-").split(".")[-1]
            vendor = reranking_models_manager.vendors()[vendor_str]
            direct_values, extra_spec = direct_fields_from_spec(info["spec"])

            edit_name = selected_rerank_name
            edit_vendor = vendor_str
            edit_api_key = direct_values["api_key"]
            edit_base_url = direct_values["base_url"]
            edit_model_name = direct_values["model_name"]
            edit_is_truncated = direct_values["is_truncated"]
            edit_max_tokens = direct_values["max_tokens"]
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
            edit_model_name,
            edit_is_truncated,
            edit_max_tokens,
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
        selected_rerank_name,
        vendor,
        api_key,
        base_url,
        model_name,
        is_truncated,
        max_tokens,
        extra_spec,
    ):
        log_content: str = ""
        try:
            log_content += f"- 正在测试 model：{selected_rerank_name}<br>"
            yield log_content

            spec = build_direct_reranking_spec(
                vendor,
                api_key,
                base_url,
                model_name,
                is_truncated,
                max_tokens,
                extra_spec,
            )
            rerank = deserialize(spec, safe=False)

            if rerank is None:
                raise Exception(f"找不到 model：{selected_rerank_name}")

            log_content += "- 正在发送消息 ([`Hello`], `Hi`)<br>"
            yield log_content
            _ = rerank([Document(content="Hello")], "Hi")

            log_content += (
                "<mark style='background: green; color: white'>- 连接成功。"
                "</mark><br>"
            )
            yield log_content

            gr.Info(f"Reranking {selected_rerank_name} 连接成功")
        except Exception as e:
            print(e)
            log_content += (
                f"<mark style='color: yellow; background: red'>- 连接失败。"
                f"错误信息：\n {str(e)}</mark>"
            )
            yield log_content

        return log_content

    def save_rerank(
        self,
        selected_rerank_name,
        edit_name,
        vendor,
        default,
        api_key,
        base_url,
        model_name,
        is_truncated,
        max_tokens,
        spec,
    ):
        try:
            new_name = edit_name.strip()
            spec = build_direct_reranking_spec(
                vendor,
                api_key,
                base_url,
                model_name,
                is_truncated,
                max_tokens,
                spec,
            )
            reranking_models_manager.update(
                selected_rerank_name, spec=spec, default=default, new_name=new_name
            )
            final_name = (
                new_name if new_name != selected_rerank_name else selected_rerank_name
            )
            gr.Info(f'Reranking model "{final_name}" 保存成功')
            return final_name
        except ValueError as e:
            raise gr.Error(str(e))
        except Exception as e:
            raise gr.Error(
                f'保存 Reranking model "{selected_rerank_name}" 失败：{e}'
            )

    def delete_rerank(self, selected_rerank_name):
        try:
            reranking_models_manager.delete(selected_rerank_name)
        except Exception as e:
            gr.Error(f'删除 Reranking model "{selected_rerank_name}" 失败：{e}')
            return selected_rerank_name

        return ""
