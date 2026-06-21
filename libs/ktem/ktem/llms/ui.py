from copy import deepcopy

import gradio as gr
import pandas as pd
import yaml
from ktem.app import BasePage
from ktem.utils.file import YAMLNoDateSafeLoader
from theflow.utils.modules import deserialize

from .manager import llms


LLM_DIRECT_FIELD_KEYS = {
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
    "deployment_name",
    "api_version",
    "openai_api_version",
}


def format_description(cls):
    params = cls.describe()["params"]
    params_lines = ["| 名称 | 类型 | 描述 |", "| --- | --- | --- |"]
    for key, value in params.items():
        if isinstance(value["auto_callback"], str):
            continue
        params_lines.append(f"| {key} | {value['type']} | {value['help']} |")
    return f"{cls.__doc__}\n\n" + "\n".join(params_lines)


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


def build_direct_llm_spec(vendor_name, api_key, base_url, model, api_version, extra_spec):
    if not vendor_name:
        raise ValueError("请选择 LLM vendor")

    vendor = llms.vendors()[vendor_name]
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
        ["model", "model_name", "azure_deployment", "deployment_name"],
        model,
    )
    _apply_first_supported(
        spec,
        desc,
        ["api_version", "openai_api_version"],
        api_version,
    )

    if vendor_name == "LCOllamaChat" and "num_ctx" not in spec:
        spec["num_ctx"] = 8192

    spec = _clean_spec(spec)
    spec["__type__"] = vendor.__module__ + "." + vendor.__qualname__
    return spec


def direct_fields_from_spec(spec):
    direct_values = {
        "api_key": "",
        "base_url": "",
        "model": "",
        "api_version": "",
    }
    direct_values["api_key"] = (
        spec.get("api_key")
        or spec.get("openai_api_key")
        or spec.get("google_api_key")
        or spec.get("cohere_api_key")
        or ""
    )
    direct_values["base_url"] = (
        spec.get("base_url")
        or spec.get("openai_api_base")
        or spec.get("azure_endpoint")
        or spec.get("endpoint_url")
        or ""
    )
    direct_values["model"] = (
        spec.get("model")
        or spec.get("model_name")
        or spec.get("azure_deployment")
        or spec.get("deployment_name")
        or ""
    )
    direct_values["api_version"] = (
        spec.get("api_version") or spec.get("openai_api_version") or ""
    )

    extra_spec = {
        key: value
        for key, value in spec.items()
        if key not in LLM_DIRECT_FIELD_KEYS and key != "__type__"
    }
    return direct_values, _dump_extra_spec(extra_spec)


class LLMManagement(BasePage):
    def __init__(self, app):
        self._app = app
        self.spec_desc_default = (
            "# Spec 说明\n\n请选择一个 LLM 查看 spec 说明。"
        )
        self.on_building_ui()

    def on_building_ui(self):
        with gr.Tab(label="查看"):
            self.llm_list = gr.DataFrame(
                headers=["名称", "vendor", "默认"],
                interactive=False,
                column_widths=[30, 40, 30],
            )

            with gr.Column(visible=False) as self._selected_panel:
                self.selected_llm_name = gr.Textbox(value="", visible=False)
                with gr.Row():
                    with gr.Column():
                        self.edit_default = gr.Checkbox(
                            label="设为默认",
                            info=(
                                "将此 LLM 设为默认。如果未设置默认值，"
                                "将随机使用一个 LLM。其他组件未指定 LLM 时，"
                                "会默认使用此 LLM。"
                            ),
                        )
                        self.edit_name = gr.Textbox(
                            label="名称",
                            info="编辑以重命名此 LLM。",
                        )
                        self.edit_vendor = gr.Dropdown(
                            label="厂商",
                            interactive=False,
                        )
                        self.edit_model = gr.Textbox(
                            label="模型",
                            placeholder="例如：gpt-4o-mini / qwen-plus",
                        )
                        self.edit_api_key = gr.Textbox(
                            label="API key",
                            type="password",
                            placeholder="填写厂商提供的 API key",
                        )
                        self.edit_base_url = gr.Textbox(
                            label="URL",
                            placeholder="例如：https://api.openai.com/v1",
                        )
                        self.edit_api_version = gr.Textbox(
                            label="API version",
                            placeholder="Azure 等厂商需要时填写",
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
                        label="LLM 名称",
                        info=(
                            "必须唯一。该名称用于识别 LLM。"
                        ),
                    )
                    self.llm_choices = gr.Dropdown(
                        label="厂商",
                        info=(
                            "选择模型服务厂商或兼容接口类型。"
                        ),
                    )
                    self.model = gr.Textbox(
                        label="模型",
                        placeholder="例如：gpt-4o-mini / qwen-plus",
                    )
                    self.api_key = gr.Textbox(
                        label="API key",
                        type="password",
                        placeholder="填写厂商提供的 API key",
                    )
                    self.base_url = gr.Textbox(
                        label="URL",
                        placeholder="例如：https://api.openai.com/v1",
                    )
                    self.api_version = gr.Textbox(
                        label="API version",
                        placeholder="Azure 等厂商需要时填写",
                    )
                    self.spec = gr.Textbox(
                        label="高级 YAML",
                        info="仅填写上方字段之外的额外参数。",
                        lines=6,
                    )
                    self.default = gr.Checkbox(
                        label="设为默认",
                        info=(
                            "将此 LLM 设为默认。整个应用会默认使用此 LLM。"
                        ),
                    )
                    self.btn_new = gr.Button("添加 LLM", variant="primary")

                with gr.Column(scale=3):
                    self.spec_desc = gr.Markdown(self.spec_desc_default)

    def _on_app_created(self):
        """Called when the app is created"""
        self._app.app.load(
            self.list_llms,
            inputs=[],
            outputs=[self.llm_list],
        )
        self._app.app.load(
            lambda: gr.update(choices=list(llms.vendors().keys())),
            outputs=[self.llm_choices],
        )
        self._app.app.load(
            lambda: gr.update(choices=list(llms.vendors().keys())),
            outputs=[self.edit_vendor],
        )

    def on_llm_vendor_change(self, vendor):
        vendor = llms.vendors()[vendor]

        extra_params: dict = {}
        desc = vendor.describe()
        for key, value in desc["params"].items():
            if key in LLM_DIRECT_FIELD_KEYS:
                continue
            if value.get("required", False):
                extra_params[key] = None

        return _dump_extra_spec(extra_params), format_description(vendor)

    def on_register_events(self):
        self.llm_choices.select(
            self.on_llm_vendor_change,
            inputs=[self.llm_choices],
            outputs=[self.spec, self.spec_desc],
        )
        self.btn_new.click(
            self.create_llm,
            inputs=[
                self.name,
                self.llm_choices,
                self.api_key,
                self.base_url,
                self.model,
                self.api_version,
                self.spec,
                self.default,
            ],
            outputs=[],
        ).success(self.list_llms, inputs=[], outputs=[self.llm_list]).success(
            lambda: ("", None, "", "", "", "", "", False, self.spec_desc_default),
            outputs=[
                self.name,
                self.llm_choices,
                self.api_key,
                self.base_url,
                self.model,
                self.api_version,
                self.spec,
                self.default,
                self.spec_desc,
            ],
        )
        self.llm_list.select(
            self.select_llm,
            inputs=self.llm_list,
            outputs=[self.selected_llm_name],
            show_progress="hidden",
        )
        self.selected_llm_name.change(
            self.on_selected_llm_change,
            inputs=[self.selected_llm_name],
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
            self.delete_llm,
            inputs=[self.selected_llm_name],
            outputs=[self.selected_llm_name],
            show_progress="hidden",
        ).then(
            self.list_llms,
            inputs=[],
            outputs=[self.llm_list],
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
            self.save_llm,
            inputs=[
                self.selected_llm_name,
                self.edit_name,
                self.edit_vendor,
                self.edit_default,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model,
                self.edit_api_version,
                self.edit_spec,
            ],
            outputs=[self.selected_llm_name],
            show_progress="hidden",
        ).then(
            self.list_llms,
            inputs=[],
            outputs=[self.llm_list],
        )
        self.btn_close.click(
            lambda: "",
            outputs=[self.selected_llm_name],
        )

        self.btn_test_connection.click(
            self.check_connection,
            inputs=[
                self.selected_llm_name,
                self.edit_vendor,
                self.edit_api_key,
                self.edit_base_url,
                self.edit_model,
                self.edit_api_version,
                self.edit_spec,
            ],
            outputs=[self.connection_logs],
        )

    def create_llm(
        self, name, choices, api_key, base_url, model, api_version, spec, default
    ):
        try:
            name = name.strip()
            spec = build_direct_llm_spec(
                choices,
                api_key,
                base_url,
                model,
                api_version,
                spec,
            )

            llms.add(name, spec=spec, default=default)
            gr.Info(f"LLM '{name}' 创建成功")
        except ValueError as e:
            raise gr.Error(str(e))
        except Exception as e:
            raise gr.Error(f"创建 LLM '{name}' 失败：{e}")

    def list_llms(self):
        """List the LLMs"""
        items = []
        for item in llms.info().values():
            record = {}
            record["name"] = item["name"]
            record["vendor"] = item["spec"].get("__type__", "-").split(".")[-1]
            record["default"] = item["default"]
            items.append(record)

        if items:
            llm_list = pd.DataFrame.from_records(items)
        else:
            llm_list = pd.DataFrame.from_records(
                [{"name": "-", "vendor": "-", "default": "-"}]
            )

        return llm_list

    def select_llm(self, llm_list, ev: gr.SelectData):
        if ev.value == "-" and ev.index[0] == 0:
            gr.Info("未加载 LLM，请先添加 LLM")
            return ""

        if not ev.selected:
            return ""

        return llm_list["name"][ev.index[0]]

    def on_selected_llm_change(self, selected_llm_name):
        if selected_llm_name == "":
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

            info = deepcopy(llms.info()[selected_llm_name])
            vendor_str = info["spec"].pop("__type__", "-").split(".")[-1]
            vendor = llms.vendors()[vendor_str]
            direct_values, extra_spec = direct_fields_from_spec(info["spec"])

            edit_name = selected_llm_name
            edit_vendor = vendor_str
            edit_api_key = direct_values["api_key"]
            edit_base_url = direct_values["base_url"]
            edit_model = direct_values["model"]
            edit_api_version = direct_values["api_version"]
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
        selected_llm_name: str,
        vendor,
        api_key,
        base_url,
        model,
        api_version,
        extra_spec,
    ):
        log_content: str = ""

        try:
            log_content += f"- 正在测试 model：{selected_llm_name}<br>"
            yield log_content

            spec = build_direct_llm_spec(
                vendor,
                api_key,
                base_url,
                model,
                api_version,
                extra_spec,
            )

            llm = deserialize(spec, safe=False)

            if llm is None:
                raise Exception(f"找不到 model：{selected_llm_name}")

            log_content += "- 正在发送消息 `Hi`<br>"
            yield log_content
            respond = llm("Hi")

            log_content += (
                f"<mark style='background: green; color: white'>- 连接成功。"
                f"响应：\n {respond}</mark><br>"
            )
            yield log_content

            gr.Info(f"LLM {selected_llm_name} 连接成功")
        except Exception as e:
            log_content += (
                f"<mark style='color: yellow; background: red'>- 连接失败。"
                f"错误信息：\n {e}</mark>"
            )
            yield log_content

        return log_content

    def save_llm(
        self,
        selected_llm_name,
        edit_name,
        vendor,
        default,
        api_key,
        base_url,
        model,
        api_version,
        spec,
    ):
        try:
            new_name = edit_name.strip()
            spec = build_direct_llm_spec(
                vendor,
                api_key,
                base_url,
                model,
                api_version,
                spec,
            )
            llms.update(
                selected_llm_name, spec=spec, default=default, new_name=new_name
            )
            final_name = (
                new_name if new_name != selected_llm_name else selected_llm_name
            )
            gr.Info(f"LLM '{final_name}' 保存成功")
            return final_name
        except ValueError as e:
            raise gr.Error(str(e))
        except Exception as e:
            raise gr.Error(f"保存 LLM '{selected_llm_name}' 失败：{e}")

    def delete_llm(self, selected_llm_name):
        try:
            llms.delete(selected_llm_name)
        except Exception as e:
            gr.Error(f"删除 LLM {selected_llm_name} 失败：{e}")
            return selected_llm_name

        return ""
