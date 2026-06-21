from typing import Optional

import gradio as gr
from ktem.app import BasePage
from ktem.db.models import IssueReport, engine
from sqlmodel import Session


class ReportIssue(BasePage):
    def __init__(self, app):
        self._app = app
        self.on_building_ui()

    def on_building_ui(self):
        with gr.Accordion(label="反馈", open=False, elem_id="report-accordion"):
            self.correctness = gr.Radio(
                choices=[
                    ("回答正确", "correct"),
                    ("回答不正确", "incorrect"),
                ],
                label="正确性：",
            )
            self.issues = gr.CheckboxGroup(
                choices=[
                    ("回答内容冒犯", "offensive"),
                    ("依据不正确", "wrong-evidence"),
                ],
                label="其他问题：",
            )
            self.more_detail = gr.Textbox(
                placeholder=(
                    "更多细节（例如错在哪里、正确答案是什么等）"
                ),
                container=False,
                lines=3,
            )
            gr.Markdown(
                "这会发送当前聊天和用户设置，以便协助排查问题"
            )
            self.report_btn = gr.Button("提交反馈")

    def report(
        self,
        correctness: str,
        issues: list[str],
        more_detail: str,
        conv_id: str,
        chat_history: list,
        settings: dict,
        user_id: Optional[int],
        info_panel: str,
        chat_state: dict,
        *selecteds,
    ):
        selecteds_ = {}
        for index in self._app.index_manager.indices:
            if index.selector is not None:
                if isinstance(index.selector, int):
                    selecteds_[str(index.id)] = selecteds[index.selector]
                elif isinstance(index.selector, tuple):
                    selecteds_[str(index.id)] = [selecteds[_] for _ in index.selector]
                else:
                    print(f"Unknown selector type: {index.selector}")

        with Session(engine) as session:
            issue = IssueReport(
                issues={
                    "correctness": correctness,
                    "issues": issues,
                    "more_detail": more_detail,
                },
                chat={
                    "conv_id": conv_id,
                    "chat_history": chat_history,
                    "info_panel": info_panel,
                    "chat_state": chat_state,
                    "selecteds": selecteds_,
                },
                settings=settings,
                user=user_id,
            )
            session.add(issue)
            session.commit()
        gr.Info("感谢你的反馈")
