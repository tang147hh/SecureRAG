from textwrap import dedent

import gradio as gr
from ktem.app import BasePage


class HintPage(BasePage):
    def __init__(self, app):
        self._app = app
        self.on_building_ui()

    def on_building_ui(self):
        with gr.Accordion(label="提示", open=False):
            gr.Markdown(
                dedent(
                    """
                - 你可以选中聊天回答中的任意文字，在右侧面板中**高亮相关 citation**。
                - **Citations** 可在 PDF viewer 和原始文本中查看。
                - 你可以在**聊天设置**菜单中调整 citation 格式，并使用高级 CoT reasoning。
                - 想要**探索更多**？请查看**帮助**部分创建你的私有 Space。
            """  # noqa
                )
            )
