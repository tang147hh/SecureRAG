import gradio as gr
from ktem.app import BasePage
from theflow.settings import settings as flowsettings


class ChatSuggestion(BasePage):
    CHAT_SAMPLES = getattr(
        flowsettings,
        "KH_FEATURE_CHAT_SUGGESTION_SAMPLES",
        [
            "总结这份文档",
            "为这份文档生成 FAQ",
            "用要点列出主要亮点",
        ],
    )

    def __init__(self, app):
        self._app = app
        self.on_building_ui()

    def on_building_ui(self):
        self.chat_samples = [[each] for each in self.CHAT_SAMPLES]
        with gr.Accordion(
            label="聊天建议",
            visible=getattr(flowsettings, "KH_FEATURE_CHAT_SUGGESTION", False),
        ) as self.accordion:
            self.default_example = gr.State(
                value=self.chat_samples,
            )
            self.examples = gr.DataFrame(
                value=self.chat_samples,
                headers=["下一个问题"],
                interactive=False,
                elem_id="chat-suggestion",
                wrap=True,
            )

    def as_gradio_component(self):
        return self.examples

    def select_example(self, ev: gr.SelectData):
        return {"text": ev.value}
