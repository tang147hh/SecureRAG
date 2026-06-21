import json

import gradio as gr
import requests
from decouple import config
from ktem.app import BasePage
from ktem.embeddings.manager import embedding_models_manager as embeddings
from ktem.llms.manager import llms
from ktem.rerankings.manager import reranking_models_manager as rerankers
from theflow.settings import settings as flowsettings

KH_OLLAMA_URL = getattr(flowsettings, "KH_OLLAMA_URL", "http://localhost:11434/v1/")
DEFAULT_OLLAMA_URL = KH_OLLAMA_URL.replace("v1", "api")
if DEFAULT_OLLAMA_URL.endswith("/"):
    DEFAULT_OLLAMA_URL = DEFAULT_OLLAMA_URL[:-1]


DEMO_MESSAGE = (
    "这是公共 Space。请使用右上角的 "
    '"Duplicate Space" 功能创建你自己的 Space。'
)


def pull_model(name: str, stream: bool = True):
    payload = {"name": name}
    headers = {"Content-Type": "application/json"}

    response = requests.post(
        DEFAULT_OLLAMA_URL + "/pull", json=payload, headers=headers, stream=stream
    )

    # Check if the request was successful
    response.raise_for_status()

    if stream:
        for line in response.iter_lines():
            if line:
                data = json.loads(line.decode("utf-8"))
                yield data
                if data.get("status") == "success":
                    break
    else:
        data = response.json()

    return data


class SetupPage(BasePage):

    public_events = ["onFirstSetupComplete"]

    def __init__(self, app):
        self._app = app
        self.on_building_ui()

    def on_building_ui(self):
        gr.Markdown(f"# 欢迎进行 {self._app.app_name} 首次设置!")
        self.radio_model = gr.Radio(
            [
                ("Cohere API（免费注册）- 推荐", "cohere"),
                ("Google API（免费注册）", "google"),
                ("OpenAI API（适用于 GPT-based models）", "openai"),
                ("Local LLM（用于完全私有的 RAG）", "ollama"),
            ],
            label="选择模型提供方",
            value="cohere",
            info=(
                "注意：之后可以再修改。"
                "如果不确定，请选择第一个选项，"
                "它适合大多数普通用户。"
            ),
            interactive=True,
        )

        with gr.Column(visible=False) as self.openai_option:
            gr.Markdown(
                (
                    "#### OpenAI API Key\n\n"
                    "(在 https://platform.openai.com/api-keys 创建)"
                )
            )
            self.openai_api_key = gr.Textbox(
                show_label=False, placeholder="OpenAI API Key"
            )

        with gr.Column(visible=True) as self.cohere_option:
            gr.Markdown(
                (
                    "#### Cohere API Key\n\n"
                    "(在 https://dashboard.cohere.com/api-keys "
                    "注册免费的 API Key)"
                )
            )
            self.cohere_api_key = gr.Textbox(
                show_label=False, placeholder="Cohere API Key"
            )

        with gr.Column(visible=False) as self.google_option:
            gr.Markdown(
                (
                    "#### Google API Key\n\n"
                    "(在 https://aistudio.google.com/app/apikey "
                    "注册免费的 API Key)"
                )
            )
            self.google_api_key = gr.Textbox(
                show_label=False, placeholder="Google API Key"
            )

        with gr.Column(visible=False) as self.ollama_option:
            gr.Markdown(
                (
                    "#### 设置 Ollama\n\n"
                    "请从 https://ollama.com/ 下载并安装 Ollama。"
                    "可在以下地址查看最新 models："
                    "https://ollama.com/library. "
                )
            )
            self.ollama_model_name = gr.Textbox(
                label="LLM model 名称",
                value=config("LOCAL_MODEL", default="qwen2.5:7b"),
            )
            self.ollama_emb_model_name = gr.Textbox(
                label="Embedding model 名称",
                value=config("LOCAL_MODEL_EMBEDDINGS", default="nomic-embed-text"),
            )

        self.setup_log = gr.HTML(
            show_label=False,
        )

        with gr.Row():
            self.btn_finish = gr.Button("继续", variant="primary")
            self.btn_skip = gr.Button(
                "我是高级用户，跳过此步骤。", variant="stop"
            )

    def on_register_events(self):
        onFirstSetupComplete = gr.on(
            triggers=[
                self.btn_finish.click,
                self.cohere_api_key.submit,
                self.openai_api_key.submit,
            ],
            fn=self.update_model,
            inputs=[
                self.cohere_api_key,
                self.openai_api_key,
                self.google_api_key,
                self.ollama_model_name,
                self.ollama_emb_model_name,
                self.radio_model,
            ],
            outputs=[self.setup_log],
            show_progress="hidden",
        )
        onSkipSetup = gr.on(
            triggers=[self.btn_skip.click],
            fn=lambda: None,
            inputs=[],
            show_progress="hidden",
            outputs=[self.radio_model],
        )

        for event in self._app.get_event("onFirstSetupComplete"):
            onSkipSetup = onSkipSetup.success(**event)

        onFirstSetupComplete = onFirstSetupComplete.success(
            fn=self.update_default_settings,
            inputs=[self.radio_model, self._app.settings_state],
            outputs=self._app.settings_state,
        )
        for event in self._app.get_event("onFirstSetupComplete"):
            onFirstSetupComplete = onFirstSetupComplete.success(**event)

        self.radio_model.change(
            fn=self.switch_options_view,
            inputs=[self.radio_model],
            show_progress="hidden",
            outputs=[
                self.cohere_option,
                self.openai_option,
                self.ollama_option,
                self.google_option,
            ],
        )

    def update_model(
        self,
        cohere_api_key,
        openai_api_key,
        google_api_key,
        ollama_model_name,
        ollama_emb_model_name,
        radio_model_value,
    ):
        log_content = ""
        if not radio_model_value:
            gr.Info("已跳过 models 设置。")
            yield gr.value(visible=False)
            return

        if radio_model_value == "cohere":
            if cohere_api_key:
                llms.update(
                    name="cohere",
                    spec={
                        "__type__": "kotaemon.llms.chats.LCCohereChat",
                        "model_name": "command-r-plus-08-2024",
                        "api_key": cohere_api_key,
                    },
                    default=True,
                )
                embeddings.update(
                    name="cohere",
                    spec={
                        "__type__": "kotaemon.embeddings.LCCohereEmbeddings",
                        "model": "embed-multilingual-v3.0",
                        "cohere_api_key": cohere_api_key,
                        "user_agent": "default",
                    },
                    default=True,
                )
                rerankers.update(
                    name="cohere",
                    spec={
                        "__type__": "kotaemon.rerankings.CohereReranking",
                        "model_name": "rerank-v4.0-fast",
                        "cohere_api_key": cohere_api_key,
                    },
                    default=True,
                )
        elif radio_model_value == "openai":
            if openai_api_key:
                llms.update(
                    name="openai",
                    spec={
                        "__type__": "kotaemon.llms.ChatOpenAI",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4o",
                        "api_key": openai_api_key,
                        "timeout": 20,
                    },
                    default=True,
                )
                embeddings.update(
                    name="openai",
                    spec={
                        "__type__": "kotaemon.embeddings.OpenAIEmbeddings",
                        "base_url": "https://api.openai.com/v1",
                        "model": "text-embedding-3-large",
                        "api_key": openai_api_key,
                        "timeout": 10,
                        "context_length": 8191,
                    },
                    default=True,
                )
        elif radio_model_value == "google":
            if google_api_key:
                llms.update(
                    name="google",
                    spec={
                        "__type__": "kotaemon.llms.chats.LCGeminiChat",
                        "model_name": "gemini-1.5-flash",
                        "api_key": google_api_key,
                    },
                    default=True,
                )
                embeddings.update(
                    name="google",
                    spec={
                        "__type__": "kotaemon.embeddings.LCGoogleEmbeddings",
                        "model": "models/text-embedding-004",
                        "google_api_key": google_api_key,
                    },
                    default=True,
                )
        elif radio_model_value == "ollama":
            llms.update(
                name="ollama",
                spec={
                    "__type__": "kotaemon.llms.ChatOpenAI",
                    "base_url": KH_OLLAMA_URL,
                    "model": ollama_model_name,
                    "api_key": "ollama",
                },
                default=True,
            )
            embeddings.update(
                name="ollama",
                spec={
                    "__type__": "kotaemon.embeddings.OpenAIEmbeddings",
                    "base_url": KH_OLLAMA_URL,
                    "model": ollama_emb_model_name,
                    "api_key": "ollama",
                },
                default=True,
            )

            # download required models through ollama
            llm_model_name = llms.get("ollama").model  # type: ignore
            emb_model_name = embeddings.get("ollama").model  # type: ignore

            try:
                for model_name in [emb_model_name, llm_model_name]:
                    log_content += f"- 正在从 Ollama 下载 model `{model_name}`<br>"
                    yield log_content

                    pre_download_log = log_content

                    for response in pull_model(model_name):
                        complete = response.get("completed", 0)
                        total = response.get("total", 0)
                        if complete > 0 and total > 0:
                            ratio = int(complete / total * 100)
                            log_content = (
                                pre_download_log
                                + f"- {response.get('status')}: {ratio}%<br>"
                            )
                        else:
                            if "pulling" not in response.get("status", ""):
                                log_content += f"- {response.get('status')}<br>"

                        yield log_content
            except Exception as e:
                log_content += (
                    "请确认已正确下载并安装 Ollama。"
                    f"错误信息：{str(e)}"
                )
                yield log_content
                raise gr.Error("从 Ollama 下载 model 失败。")

        # test models connection
        llm_output = emb_output = None

        # LLM model
        log_content += f"- 正在测试 LLM model：{radio_model_value}<br>"
        yield log_content

        llm = llms.get(radio_model_value)  # type: ignore
        log_content += "- 正在发送消息 `Hi`<br>"
        yield log_content
        try:
            llm_output = llm("Hi")
        except Exception as e:
            log_content += (
                f"<mark style='color: yellow; background: red'>- 连接失败。"
                f"错误信息：\n {str(e)}</mark>"
            )

        if llm_output:
            log_content += (
                "<mark style='background: green; color: white'>- 连接成功。"
                "</mark><br>"
            )
        yield log_content

        if llm_output:
            # embedding model
            log_content += f"- 正在测试 Embedding model：{radio_model_value}<br>"
            yield log_content

            emb = embeddings.get(radio_model_value)
            assert emb, f"Embedding model {radio_model_value} not found."

            log_content += "- 正在发送消息 `Hi`<br>"
            yield log_content
            try:
                emb_output = emb("Hi")
            except Exception as e:
                log_content += (
                    f"<mark style='color: yellow; background: red'>"
                    "- 连接失败。"
                    f"错误信息：\n {str(e)}</mark>"
                )

            if emb_output:
                log_content += (
                    "<mark style='background: green; color: white'>"
                    "- 连接成功。"
                    "</mark><br>"
                )
            yield log_content

        if llm_output and emb_output:
            gr.Info("models 设置完成！")
        else:
            raise gr.Error(
                "models 设置失败，请检查连接和 API Key。"
            )

    def update_default_settings(self, radio_model_value, default_settings):
        # revise default settings
        # reranking llm
        default_settings["index.options.1.reranking_llm"] = radio_model_value
        if radio_model_value == "ollama":
            default_settings["index.options.1.use_llm_reranking"] = False

        return default_settings

    def switch_options_view(self, radio_model_value):
        components_visible = [gr.update(visible=False) for _ in range(4)]

        values = ["cohere", "openai", "ollama", "google", None]
        assert radio_model_value in values, f"Invalid value {radio_model_value}"

        if radio_model_value is not None:
            idx = values.index(radio_model_value)
            components_visible[idx] = gr.update(visible=True)

        return components_visible
