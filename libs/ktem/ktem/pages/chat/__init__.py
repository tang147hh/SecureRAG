import asyncio
import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Optional

import gradio as gr
from decouple import config
from ktem.app import BasePage
from ktem.components import reasonings
from ktem.db.models import Conversation, engine
from ktem.index.file import FileIndex
from ktem.index.file.ui import File
from ktem.reasoning.prompt_optimization.mindmap import MINDMAP_HTML_EXPORT_TEMPLATE
from ktem.reasoning.prompt_optimization.suggest_conversation_name import (
    SuggestConvNamePipeline,
)
from ktem.reasoning.prompt_optimization.suggest_followup_chat import (
    SuggestFollowupQuesPipeline,
)
from plotly.io import from_json
from sqlmodel import Session, select
from theflow.settings import settings as flowsettings
from theflow.utils.modules import import_dotted_string

from kotaemon.base import Document
from kotaemon.indices.ingests.files import KH_DEFAULT_FILE_EXTRACTORS
from kotaemon.indices.qa.utils import strip_think_tag

from ...utils import (
    SUPPORTED_LANGUAGE_MAP,
    format_mentions_for_display,
    get_mentions_regex,
    get_urls,
    prepare_llm_query,
)
from ...utils.commands import WEB_SEARCH_COMMAND
from ...utils.hf_papers import get_recommended_papers
from ...utils.rate_limit import check_rate_limit
from .chat_panel import ChatPanel
from .chat_panel import DEMO_CITATION_PANEL
from .chat_suggestion import ChatSuggestion
from .common import STATE
from .control import ConversationControl
from .demo_hint import HintPage
from .paper_list import PaperListPage
from .report import ReportIssue

KH_DEMO_MODE = getattr(flowsettings, "KH_DEMO_MODE", False)
KH_SSO_ENABLED = getattr(flowsettings, "KH_SSO_ENABLED", False)
KH_WEB_SEARCH_BACKEND = getattr(flowsettings, "KH_WEB_SEARCH_BACKEND", None)
WebSearch = None
if KH_WEB_SEARCH_BACKEND:
    try:
        WebSearch = import_dotted_string(KH_WEB_SEARCH_BACKEND, safe=False)
    except (ImportError, AttributeError) as e:
        print(f"Error importing {KH_WEB_SEARCH_BACKEND}: {e}")

REASONING_LIMITS = 2 if KH_DEMO_MODE else 10
DEFAULT_SETTING = "(默认)"
INFO_PANEL_SCALES = {True: 8, False: 4}
DEFAULT_QUESTION = (
    "这份文档的摘要是什么？"
    if not KH_DEMO_MODE
    else "这篇 paper 的摘要是什么？"
)
DEFAULT_PROMPT_TEMPLATE_NAME = "默认 RAG"
DEFAULT_PROMPT_TEMPLATE_TEXT = (
    "You are a careful RAG assistant. Answer directly in {lang}. Use the provided "
    "material when it is available, but do not mention the material, context, "
    "knowledge base, or documents in the opening phrase. If the material is "
    "insufficient, say what is missing and avoid inventing facts.\n\n"
    "Context:\n{context}\n\nQuestion: {question}\nAnswer:"
)
DEFAULT_PROMPT_TEMPLATES = {
    DEFAULT_PROMPT_TEMPLATE_NAME: DEFAULT_PROMPT_TEMPLATE_TEXT,
    "严格引用": (
        "Answer directly in {lang}. Base the answer strictly on the material below, "
        "but do not start by mentioning the context, knowledge base, or documents. "
        "When the context does not contain the answer, say that the documents do "
        "not provide enough information.\n\nContext:\n{context}\n\nQuestion: "
        "{question}\nAnswer:"
    ),
}

ANSWER_OPENING_BOILERPLATE_RE = re.compile(
    r"^\s*(?:"
    r"(?:根据|依据|基于|按照)(?:提供的|给定的|上述|以上|相关)?"
    r"(?:知识库|知识|上下文|语境|资料|材料|文档|文件|内容|信息)"
    r"(?:可知|来看|显示|内容|信息|中的信息|中的内容|提供的信息|提供的内容)?"
    r"|从(?:提供的|给定的|上述|以上|相关)?"
    r"(?:知识库|知识|上下文|语境|资料|材料|文档|文件|内容|信息)(?:来看|可知)"
    r"|according to (?:the )?(?:provided )?"
    r"(?:context|knowledge base|documents?|materials?|information)"
    r"|based on (?:the )?(?:provided )?"
    r"(?:context|knowledge base|documents?|materials?|information)"
    r"|from (?:the )?(?:provided )?"
    r"(?:context|knowledge base|documents?|materials?|information)"
    r")\s*(?:[,，。:：；;]\s*)*",
    re.IGNORECASE,
)

chat_input_focus_js = """
function() {
    if (window.khChatFocusComposer) {
        window.khChatFocusComposer();
        return;
    }
    let chatInput = document.querySelector("#chat-input textarea");
    if (chatInput) {
        chatInput.focus();
    }
}
"""

chat_submit_done_js = """
function() {
    const setBusy = window.khChatSetBusy || globalThis.khChatSetBusy;
    if (setBusy) {
        setBusy(false);
    } else {
        document.body.classList.remove("kh-chat-busy");
        document.querySelector("#chat-area")?.classList.remove("is-chat-submitting");
        document.querySelector("#chat-input")?.classList.remove("is-submitting");
    }
    const focusComposer = window.khChatFocusComposer || globalThis.khChatFocusComposer;
    if (focusComposer) {
        focusComposer();
    }
}
"""

quick_urls_submit_js = """
function() {
    let urlInput = document.querySelector("#quick-url-demo textarea");
    console.log("URL input:", urlInput);
    urlInput.dispatchEvent(new KeyboardEvent('keypress', {'key': 'Enter'}));
}
"""

recommended_papers_js = """
function() {
    // Get all links and attach click event
    var links = document.querySelectorAll("#related-papers a");

    function submitPaper(event) {
        event.preventDefault();
        var target = event.currentTarget;
        var url = target.getAttribute("href");
        console.log("URL:", url);

        let newChatButton = document.querySelector("#new-conv-button");
        newChatButton.click();

        setTimeout(() => {
            let urlInput = document.querySelector("#quick-url-demo textarea");
            // Fill the URL input
            urlInput.value = url;
            urlInput.dispatchEvent(new Event("input", { bubbles: true }));
            urlInput.dispatchEvent(new KeyboardEvent('keypress', {'key': 'Enter'}));
            }, 500
        );
    }

    for (var i = 0; i < links.length; i++) {
        links[i].onclick = submitPaper;
    }
}
"""

clear_bot_message_selection_js = """
function() {
    var bot_messages = document.querySelectorAll(
        "div#main-chat-bot div.message-row.bot-row"
    );
    bot_messages.forEach(message => {
        message.classList.remove("text_selection");
    });
}
"""

pdfview_js = """
function() {
    setTimeout(fullTextSearch(), 100);

    // Get all links and attach click event
    var links = document.getElementsByClassName("pdf-link");
    for (var i = 0; i < links.length; i++) {
        links[i].onclick = openModal;
    }

    // Get all citation links and attach click event
    var links = document.querySelectorAll("a.citation");
    for (var i = 0; i < links.length; i++) {
        links[i].onclick = scrollToCitation;
    }

    var markmap_div = document.querySelector("div.markmap");
    var mindmap_el_script = document.querySelector('div.markmap script');

    if (mindmap_el_script) {
        markmap_div_html = markmap_div.outerHTML;
    }

    // render the mindmap if the script tag is present
    if (mindmap_el_script) {
        markmap.autoLoader.renderAll();
    }

    setTimeout(() => {
        var mindmap_el = document.querySelector('svg.markmap');

        var text_nodes = document.querySelectorAll("svg.markmap div");
        for (var i = 0; i < text_nodes.length; i++) {
            text_nodes[i].onclick = fillChatInput;
        }

        if (mindmap_el) {
            function on_svg_export(event) {
                html = "{html_template}";
                html = html.replace("{markmap_div}", markmap_div_html);
                spawnDocument(html, {window: "width=1000,height=1000"});
            }

            var link = document.getElementById("mindmap-toggle");
            if (link) {
                link.onclick = function(event) {
                    event.preventDefault(); // Prevent the default link behavior
                    var div = document.querySelector("div.markmap");
                    if (div) {
                        var currentHeight = div.style.height;
                        if (currentHeight === '400px' || (currentHeight === '')) {
                            div.style.height = '650px';
                        } else {
                            div.style.height = '400px'
                        }
                    }
                };
            }

            if (markmap_div_html) {
                var link = document.getElementById("mindmap-export");
                if (link) {
                    link.addEventListener('click', on_svg_export);
                }
            }
        }
    }, 250);

    return [links.length]
}
""".replace(
    "{html_template}",
    MINDMAP_HTML_EXPORT_TEMPLATE.replace("\n", "").replace('"', '\\"'),
)

fetch_api_key_js = """
function(_, __) {
    api_key = getStorage('google_api_key', '');
    console.log('session API key:', api_key);
    return [api_key, _];
}
"""


class ChatPage(BasePage):
    def __init__(self, app):
        self._app = app
        self._indices_input = []

        self.on_building_ui()

        self._preview_links = gr.State(value=None)
        self._reasoning_type = gr.State(value=None)
        self._conversation_renamed = gr.State(value=False)
        self._use_suggestion = gr.State(
            value=getattr(flowsettings, "KH_FEATURE_CHAT_SUGGESTION", False)
        )
        self._info_panel_expanded = gr.State(value=True)
        self._command_state = gr.State(value=None)
        self._user_api_key = gr.Text(value="", visible=False)

    def on_building_ui(self):
        with gr.Row(elem_id="packy-chat-shell"):
            self.state_chat = gr.State(STATE)
            self.state_retrieval_history = gr.State([])
            self.state_plot_history = gr.State([])
            self.state_plot_panel = gr.State(None)
            self.first_selector_choices = gr.State(None)

            with gr.Column(scale=1, elem_id="conv-settings-panel") as self.conv_column:
                with gr.Accordion(
                    label="会话",
                    open=True,
                    elem_id="conversation-control-expand",
                ):
                    self.chat_control = ConversationControl(self._app)

                with gr.Column(visible=False, elem_id="archived-index-selectors"):
                    for index_id, index in enumerate(self._app.index_manager.indices):
                        index.selector = None
                        index_ui = index.get_selector_component_ui()
                        if not index_ui:
                            # the index doesn't have a selector UI component
                            continue

                        index_ui.unrender()
                        index_ui.render()
                        gr_index = index_ui.as_gradio_component()

                        # get the file selector choices for the first index
                        if index_id == 0:
                            self.first_selector_choices = index_ui.selector_choices
                            self.first_indexing_file_fn = None
                            self.first_indexing_url_fn = None

                        if gr_index:
                            if isinstance(gr_index, list):
                                index.selector = tuple(
                                    range(
                                        len(self._indices_input),
                                        len(self._indices_input) + len(gr_index),
                                    )
                                )
                                index.default_selector = index_ui.default()
                                self._indices_input.extend(gr_index)
                            else:
                                index.selector = len(self._indices_input)
                                index.default_selector = index_ui.default()
                                self._indices_input.append(gr_index)
                        setattr(self, f"_index_{index.id}", index_ui)

                self.chat_suggestion = ChatSuggestion(self._app)

                if len(self._app.index_manager.indices) > 0:
                    quick_upload_label = (
                        "快速上传" if not KH_DEMO_MODE else "或输入新的 paper URL"
                    )

                    with gr.Accordion(
                        label=quick_upload_label,
                        open=True,
                        elem_id="quick-upload-expand",
                    ) as _:
                        self.quick_file_upload_status = gr.Markdown()
                        if not KH_DEMO_MODE:
                            self.quick_file_upload = File(
                                file_types=list(KH_DEFAULT_FILE_EXTRACTORS.keys()),
                                file_count="multiple",
                                container=True,
                                show_label=False,
                                elem_id="quick-file",
                            )
                        self.quick_urls = gr.Textbox(
                            placeholder=(
                                "或粘贴 URLs"
                                if not KH_DEMO_MODE
                                else "粘贴 Arxiv URLs\n(https://arxiv.org/abs/xxx)"
                            ),
                            lines=1,
                            container=False,
                            show_label=False,
                            elem_id=(
                                "quick-url" if not KH_DEMO_MODE else "quick-url-demo"
                            ),
                        )

                with gr.Accordion(
                    label="聊天设置",
                    elem_id="chat-settings-expand",
                    open=True,
                    visible=not KH_DEMO_MODE,
                ) as self.chat_settings:
                    with gr.Column():
                        reasoning_setting = (
                            self._app.default_settings.reasoning.settings["use"]
                        )
                        model_setting = self._app.default_settings.reasoning.options[
                            "simple"
                        ].settings["llm"]
                        language_setting = (
                            self._app.default_settings.reasoning.settings["lang"]
                        )
                        citation_setting = self._app.default_settings.reasoning.options[
                            "simple"
                        ].settings["highlight_citation"]

                        self.reasoning_type = gr.Dropdown(
                            choices=reasoning_setting.choices[:REASONING_LIMITS],
                            value=reasoning_setting.value,
                            label="Reasoning 方法",
                            container=True,
                        )
                        self.model_type = gr.Dropdown(
                            choices=model_setting.choices,
                            value=model_setting.value,
                            label="Model",
                            container=True,
                            visible=not KH_DEMO_MODE and not KH_SSO_ENABLED,
                        )
                        self.language = gr.Dropdown(
                            choices=language_setting.choices,
                            value=language_setting.value,
                            label="语言",
                            container=True,
                        )

                        self.citation = gr.Dropdown(
                            choices=citation_setting.choices,
                            value=citation_setting.value,
                            label="引用高亮",
                            container=True,
                            interactive=True,
                            elem_id="citation-dropdown",
                        )

                        if not config("USE_LOW_LLM_REQUESTS", default=False, cast=bool):
                            self.use_mindmap = gr.State(value=True)
                            self.use_mindmap_check = gr.Checkbox(
                                label="Mindmap（开）",
                                container=True,
                                elem_id="use-mindmap-checkbox",
                                value=True,
                            )
                        else:
                            self.use_mindmap = gr.State(value=False)
                            self.use_mindmap_check = gr.Checkbox(
                                label="Mindmap（关）",
                                container=True,
                                elem_id="use-mindmap-checkbox",
                                value=False,
                            )

                    with gr.Accordion("检索参数", open=True):
                        with gr.Row():
                            self.retrieval_top_k = gr.Slider(
                                minimum=1,
                                maximum=30,
                                step=1,
                                value=10,
                                label="top_k",
                                info="最终传给回答模型的片段数量。",
                            )
                            self.first_round_multiplier = gr.Slider(
                                minimum=1,
                                maximum=20,
                                step=1,
                                value=10,
                                label="召回倍数",
                                info="第一轮候选数量 = top_k x 召回倍数。",
                            )
                        with gr.Row():
                            self.retrieval_mode = gr.Radio(
                                choices=[
                                    ("Hybrid", "hybrid"),
                                    ("Vector", "vector"),
                                    ("Text", "text"),
                                ],
                                value="hybrid",
                                label="召回方式",
                            )
                            self.retrieval_enhancement = gr.Radio(
                                choices=[
                                    ("None", "none"),
                                    ("Query Rewrite", "rewrite"),
                                    ("HyDE", "hyde"),
                                    ("RAG-Fusion", "fusion"),
                                ],
                                value="none",
                                label="检索增强",
                            )
                            self.use_reranking = gr.Checkbox(
                                value=True,
                                label="启用 rerank",
                                container=True,
                            )
                        with gr.Row():
                            self.use_llm_reranking = gr.Checkbox(
                                value=False,
                                label="LLM 相关性评分",
                                container=True,
                            )
                            self.use_mmr = gr.Checkbox(
                                value=False,
                                label="MMR 多样性召回",
                                container=True,
                            )
                            self.prioritize_table = gr.Checkbox(
                                value=False,
                                label="补充同页表格",
                                container=True,
                            )

                    with gr.Accordion("提示词模板", open=True):
                        self.prompt_templates_state = gr.State(
                            value=deepcopy(DEFAULT_PROMPT_TEMPLATES)
                        )
                        with gr.Row():
                            self.prompt_template_select = gr.Dropdown(
                                label="模板",
                                choices=list(DEFAULT_PROMPT_TEMPLATES.keys()),
                                value=DEFAULT_PROMPT_TEMPLATE_NAME,
                                interactive=True,
                            )
                            self.prompt_template_name = gr.Textbox(
                                label="模板名称",
                                value=DEFAULT_PROMPT_TEMPLATE_NAME,
                                lines=1,
                                max_lines=1,
                            )
                        self.prompt_template_text = gr.Textbox(
                            label="QA Prompt",
                            value=DEFAULT_PROMPT_TEMPLATE_TEXT,
                            lines=8,
                            max_lines=18,
                            info="可使用 {context}、{question}、{lang}。",
                        )
                        with gr.Row():
                            self.prompt_template_save = gr.Button(
                                "保存模板",
                                variant="primary",
                            )
                            self.prompt_template_delete = gr.Button(
                                "删除模板",
                                variant="secondary",
                            )

                if not KH_DEMO_MODE:
                    with gr.Column(visible=False, elem_id="archived-feedback-panel"):
                        self.report_issue = ReportIssue(self._app)
                else:
                    with gr.Accordion(label="相关 papers", open=False):
                        self.related_papers = gr.Markdown(elem_id="related-papers")

                    self.hint_page = HintPage(self._app)

            with gr.Column(scale=6, elem_id="chat-area"):
                if KH_DEMO_MODE:
                    self.paper_list = PaperListPage(self._app)

                self.chat_panel = ChatPanel(self._app)

            with gr.Column(
                scale=INFO_PANEL_SCALES[False], elem_id="chat-info-panel"
            ) as self.info_column:
                with gr.Accordion(
                    label="信息面板", open=True, elem_id="info-expand"
                ):
                    self.modal = gr.HTML("<div id='pdf-modal'></div>")
                    self.plot_panel = gr.Plot(visible=False)
                    self.info_panel = gr.HTML(
                        value=DEMO_CITATION_PANEL,
                        elem_id="html-info-panel",
                    )

        self.followup_questions = self.chat_suggestion.examples
        self.followup_questions_ui = self.chat_suggestion.accordion

    def _json_to_plot(self, json_dict: dict | None):
        if json_dict:
            plot = from_json(json_dict)
            plot = gr.update(visible=True, value=plot)
        else:
            plot = gr.update(visible=False)
        return plot

    def _prompt_store_path(self):
        return flowsettings.KH_USER_DATA_DIR / "prompt_templates.json"

    def _chat_runtime_settings_path(self):
        return flowsettings.KH_USER_DATA_DIR / "chat_runtime_settings.json"

    def _read_chat_runtime_settings(self) -> dict:
        store_path = self._chat_runtime_settings_path()
        if not store_path.exists():
            return {}
        try:
            with store_path.open(encoding="utf-8") as fi:
                return json.load(fi)
        except (OSError, json.JSONDecodeError) as e:
            print(f"Failed to read chat runtime settings: {e}")
            return {}

    def _write_chat_runtime_settings(self, data: dict):
        store_path = self._chat_runtime_settings_path()
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=store_path.parent,
        ) as fo:
            json.dump(data, fo, ensure_ascii=False, indent=2)
            temp_path = fo.name
        os.replace(temp_path, store_path)

    def _load_prompt_template_map(self, user_id) -> dict[str, str]:
        user_key = str(user_id or "default")
        store_path = self._prompt_store_path()
        templates = deepcopy(DEFAULT_PROMPT_TEMPLATES)
        if store_path.exists():
            try:
                with store_path.open(encoding="utf-8") as fi:
                    data = json.load(fi)
                templates.update(data.get(user_key, {}))
            except (OSError, json.JSONDecodeError) as e:
                print(f"Failed to load prompt templates: {e}")
        return templates

    def _write_prompt_template_map(self, user_id, templates: dict[str, str]):
        user_key = str(user_id or "default")
        store_path = self._prompt_store_path()
        data = {}
        if store_path.exists():
            try:
                with store_path.open(encoding="utf-8") as fi:
                    data = json.load(fi)
            except (OSError, json.JSONDecodeError) as e:
                print(f"Failed to read prompt template store: {e}")
        data[user_key] = templates
        with store_path.open("w", encoding="utf-8") as fo:
            json.dump(data, fo, ensure_ascii=False, indent=2)

    def load_prompt_templates(self, user_id):
        templates = self._load_prompt_template_map(user_id)
        selected_name = next(iter(templates), DEFAULT_PROMPT_TEMPLATE_NAME)
        selected_text = templates.get(selected_name, DEFAULT_PROMPT_TEMPLATE_TEXT)
        choices = list(templates.keys())
        return (
            templates,
            gr.update(choices=choices, value=selected_name),
            selected_name,
            selected_text,
        )

    def select_prompt_template(self, templates, selected_name):
        templates = templates or deepcopy(DEFAULT_PROMPT_TEMPLATES)
        return selected_name, templates.get(selected_name, "")

    def save_prompt_template(self, templates, name, text, user_id):
        name = (name or "").strip()
        text = (text or "").strip()
        if not name:
            raise gr.Error("模板名称不能为空")
        if "{context}" not in text or "{question}" not in text or "{lang}" not in text:
            raise gr.Error("QA Prompt 必须包含 {context}、{question} 和 {lang}")

        templates = templates or deepcopy(DEFAULT_PROMPT_TEMPLATES)
        templates[name] = text
        self._write_prompt_template_map(user_id, templates)
        gr.Info(f"提示词模板 {name} 已保存")
        return (
            templates,
            gr.update(choices=list(templates.keys()), value=name),
            name,
            text,
        )

    def delete_prompt_template(self, templates, selected_name, user_id):
        templates = templates or deepcopy(DEFAULT_PROMPT_TEMPLATES)
        if selected_name == DEFAULT_PROMPT_TEMPLATE_NAME:
            raise gr.Error("默认模板不能删除")
        if selected_name in templates:
            templates.pop(selected_name)
            self._write_prompt_template_map(user_id, templates)
            gr.Info(f"提示词模板 {selected_name} 已删除")
        selected_name = next(iter(templates), DEFAULT_PROMPT_TEMPLATE_NAME)
        return (
            templates,
            gr.update(choices=list(templates.keys()), value=selected_name),
            selected_name,
            templates.get(selected_name, DEFAULT_PROMPT_TEMPLATE_TEXT),
        )

    def load_chat_runtime_settings(self, user_id):
        user_key = str(user_id or "default")
        settings = self._read_chat_runtime_settings().get(user_key, {})
        templates = self._load_prompt_template_map(user_id)
        selected_template = settings.get(
            "prompt_template_select",
            next(iter(templates), DEFAULT_PROMPT_TEMPLATE_NAME),
        )
        if selected_template not in templates:
            selected_template = next(iter(templates), DEFAULT_PROMPT_TEMPLATE_NAME)
        use_mindmap = settings.get("use_mindmap", True)

        return (
            gr.update(value=settings.get("reasoning_type") or "simple"),
            gr.update(value=settings.get("model_type") or ""),
            gr.update(value=settings.get("language", "zh")),
            gr.update(value=settings.get("citation", "highlight")),
            gr.update(value=use_mindmap),
            gr.update(
                value=use_mindmap,
                label="Mindmap" + ("（开）" if use_mindmap else "（关）"),
            ),
            gr.update(value=settings.get("retrieval_top_k", 10)),
            gr.update(value=settings.get("first_round_multiplier", 10)),
            gr.update(value=settings.get("retrieval_mode", "hybrid")),
            gr.update(value=settings.get("retrieval_enhancement", "none")),
            gr.update(value=settings.get("use_reranking", True)),
            gr.update(value=settings.get("use_llm_reranking", False)),
            gr.update(value=settings.get("use_mmr", False)),
            gr.update(value=settings.get("prioritize_table", False)),
            templates,
            gr.update(choices=list(templates.keys()), value=selected_template),
            selected_template,
            templates.get(selected_template, DEFAULT_PROMPT_TEMPLATE_TEXT),
        )

    def save_chat_runtime_settings(
        self,
        user_id,
        reasoning_type,
        model_type,
        language,
        citation,
        use_mindmap,
        retrieval_top_k,
        first_round_multiplier,
        retrieval_mode,
        retrieval_enhancement,
        use_reranking,
        use_llm_reranking,
        use_mmr,
        prioritize_table,
        prompt_template_select,
        graph_enabled=False,
        graph_provider="lightrag",
        graph_search_type="local",
    ):
        data = self._read_chat_runtime_settings()
        user_key = str(user_id or "default")
        data[user_key] = {
            "reasoning_type": reasoning_type,
            "model_type": "" if model_type in (DEFAULT_SETTING, None) else model_type,
            "language": language,
            "citation": citation,
            "use_mindmap": bool(use_mindmap),
            "retrieval_top_k": retrieval_top_k,
            "first_round_multiplier": first_round_multiplier,
            "retrieval_mode": retrieval_mode,
            "retrieval_enhancement": retrieval_enhancement or "none",
            "use_reranking": bool(use_reranking),
            "use_llm_reranking": bool(use_llm_reranking),
            "use_mmr": bool(use_mmr),
            "prioritize_table": bool(prioritize_table),
            "prompt_template_select": prompt_template_select or DEFAULT_PROMPT_TEMPLATE_NAME,
            "graph_enabled": bool(graph_enabled),
            "graph_provider": graph_provider or "lightrag",
            "graph_search_type": graph_search_type or "local",
        }
        self._write_chat_runtime_settings(data)

    def format_answer_with_refs(self, text, refs, placeholder):
        return self.strip_answer_opening_boilerplate(text) or placeholder

    def format_citation_panel(self, refs):
        if not refs:
            return ""
        return (
            "<section class='packy-reference-panel packy-live-reference-panel'>"
            "<div class='packy-reference-head'><h3>信息面板</h3></div>"
            "<section class='chat-reference-box'>"
            "<details open>"
            "<summary>引用依据</summary>"
            f"<div class='chat-reference-content'>{refs}</div>"
            "</details>"
            "</section>"
            "</section>"
        )

    @staticmethod
    def strip_answer_opening_boilerplate(text: str) -> str:
        previous = None
        stripped = text or ""
        while previous != stripped:
            previous = stripped
            stripped = ANSWER_OPENING_BOILERPLATE_RE.sub("", stripped, count=1)
        return stripped

    def render_diagnostics_panel(self, diagnostics: dict) -> str:
        retrieval = diagnostics.get("retrieval", {})
        answer = diagnostics.get("answer", {})
        tokens = diagnostics.get("tokens", {})

        def fmt_int(value):
            return "未返回" if value in (None, -1) else f"{int(value):,}"

        def fmt_percent(value):
            return "0%" if value is None else f"{value * 100:.0f}%"

        def fmt_score(value):
            return "未计算" if value is None else f"{value:.2f}"

        return (
            "<section class='chat-diagnostics'>"
            "<h4>回答诊断</h4>"
            "<div class='chat-diagnostic-grid'>"
            "<div><span>召回文档</span><b>{retrieved}</b></div>"
            "<div><span>引用覆盖率</span><b>{coverage}</b></div>"
            "<div><span>引用数量</span><b>{citations}</b></div>"
            "<div><span>平均相关分</span><b>{avg_score}</b></div>"
            "<div><span>Prompt tokens</span><b>{prompt_tokens}</b></div>"
            "<div><span>Completion tokens</span><b>{completion_tokens}</b></div>"
            "<div><span>Total tokens</span><b>{total_tokens}</b></div>"
            "<div><span>回答置信度</span><b>{qa_score}</b></div>"
            "</div>"
            "<p>召回率按“被引用文档数 / 召回文档数”计算。</p>"
            "</section>"
        ).format(
            retrieved=fmt_int(retrieval.get("retrieved_count")),
            coverage=fmt_percent(retrieval.get("citation_coverage")),
            citations=fmt_int(retrieval.get("citation_count")),
            avg_score=fmt_score(retrieval.get("avg_relevance_score")),
            prompt_tokens=fmt_int(tokens.get("prompt_tokens")),
            completion_tokens=fmt_int(tokens.get("completion_tokens")),
            total_tokens=fmt_int(tokens.get("total_tokens")),
            qa_score=fmt_score(answer.get("qa_score")),
        )

    def append_info_content(self, current: str, content) -> str:
        if content is None:
            return ""
        if isinstance(content, dict) and content.get("type") == "diagnostics":
            return current + self.render_diagnostics_panel(content)
        return current + str(content)

    def on_register_events(self):
        # first index paper recommendation
        if KH_DEMO_MODE and len(self._indices_input) > 0:
            self._indices_input[1].change(
                self.get_recommendations,
                inputs=[self.first_selector_choices, self._indices_input[1]],
                outputs=[self.related_papers],
            ).then(
                fn=None,
                inputs=None,
                outputs=None,
                js=recommended_papers_js,
            )

        chat_event = (
            gr.on(
                triggers=[
                    self.chat_panel.text_input.submit,
                ],
                fn=self.submit_msg,
                inputs=[
                    self.chat_panel.text_input,
                    self.chat_panel.chatbot,
                    self._app.user_id,
                    self._app.settings_state,
                    self.chat_control.conversation_id,
                    self.chat_control.conversation_rn,
                    self.first_selector_choices,
                ],
                outputs=[
                    self.chat_panel.text_input,
                    self.chat_panel.chatbot,
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                    # file selector from the first index
                    self._indices_input[0],
                    self._indices_input[1],
                    self._command_state,
                ],
                concurrency_limit=20,
                show_progress="hidden",
                js="""
                function(
                    chatInput,
                    chatHistory,
                    userId,
                    settings,
                    conversationId,
                    conversationName,
                    firstSelectorChoices
                ) {
                    const hasText = Boolean(chatInput && chatInput.text && chatInput.text.trim());
                    const hasFiles = Boolean(chatInput && chatInput.files && chatInput.files.length);
                    const busy = hasText || hasFiles;
                    const setBusy = window.khChatSetBusy || globalThis.khChatSetBusy;
                    if (setBusy) {
                        setBusy(busy);
                    } else {
                        document.body.classList.toggle("kh-chat-busy", busy);
                        document.querySelector("#chat-area")?.classList.toggle("is-chat-submitting", busy);
                        document.querySelector("#chat-input")?.classList.toggle("is-submitting", busy);
                    }
                    return [
                        chatInput,
                        chatHistory,
                        userId,
                        settings,
                        conversationId,
                        conversationName,
                        firstSelectorChoices
                    ];
                }
                """,
            )
            .success(
                fn=self.chat_fn,
                inputs=[
                    self.chat_control.conversation_id,
                    self.chat_panel.chatbot,
                    self._app.settings_state,
                    self.reasoning_type,
                    self.model_type,
                    self.use_mindmap,
                    self.citation,
                    self.language,
                    self.retrieval_top_k,
                    self.first_round_multiplier,
                    self.retrieval_mode,
                    self.retrieval_enhancement,
                    self.use_reranking,
                    self.use_llm_reranking,
                    self.use_mmr,
                    self.prioritize_table,
                    self.prompt_template_text,
                    self.state_chat,
                    self._command_state,
                    self._app.user_id,
                ]
                + self._indices_input,
                outputs=[
                    self.chat_panel.chatbot,
                    self.chat_panel.citation_panel,
                    self.info_panel,
                    self.plot_panel,
                    self.state_plot_panel,
                    self.state_chat,
                ],
                concurrency_limit=20,
                show_progress="minimal",
            )
            .then(
                fn=lambda: True,
                inputs=None,
                outputs=[self._preview_links],
                js=pdfview_js,
            )
            .success(
                fn=self.check_and_suggest_name_conv,
                inputs=self.chat_panel.chatbot,
                outputs=[
                    self.chat_control.conversation_rn,
                    self._conversation_renamed,
                ],
            )
            .success(
                self.chat_control.rename_conv,
                inputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation_rn,
                    self._conversation_renamed,
                    self._app.user_id,
                ],
                outputs=[
                    self.chat_control.conversation,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                ],
                show_progress="hidden",
            )
        )

        chat_runtime_inputs = [
            self._app.user_id,
            self.reasoning_type,
            self.model_type,
            self.language,
            self.citation,
            self.use_mindmap_check,
            self.retrieval_top_k,
            self.first_round_multiplier,
            self.retrieval_mode,
            self.retrieval_enhancement,
            self.use_reranking,
            self.use_llm_reranking,
            self.use_mmr,
            self.prioritize_table,
            self.prompt_template_select,
        ]

        self.prompt_template_select.change(
            self.select_prompt_template,
            inputs=[self.prompt_templates_state, self.prompt_template_select],
            outputs=[self.prompt_template_name, self.prompt_template_text],
            show_progress="hidden",
        )
        self.prompt_template_save.click(
            self.save_prompt_template,
            inputs=[
                self.prompt_templates_state,
                self.prompt_template_name,
                self.prompt_template_text,
                self._app.user_id,
            ],
            outputs=[
                self.prompt_templates_state,
                self.prompt_template_select,
                self.prompt_template_name,
                self.prompt_template_text,
            ],
            show_progress="hidden",
        ).then(
            self.save_chat_runtime_settings,
            inputs=chat_runtime_inputs,
            outputs=None,
            show_progress="hidden",
        )
        self.prompt_template_delete.click(
            self.delete_prompt_template,
            inputs=[
                self.prompt_templates_state,
                self.prompt_template_select,
                self._app.user_id,
            ],
            outputs=[
                self.prompt_templates_state,
                self.prompt_template_select,
                self.prompt_template_name,
                self.prompt_template_text,
            ],
            show_progress="hidden",
        ).then(
            self.save_chat_runtime_settings,
            inputs=chat_runtime_inputs,
            outputs=None,
            show_progress="hidden",
        )

        for component in [
            self.reasoning_type,
            self.model_type,
            self.language,
            self.citation,
            self.use_mindmap_check,
            self.retrieval_top_k,
            self.first_round_multiplier,
            self.retrieval_mode,
            self.retrieval_enhancement,
            self.use_reranking,
            self.use_llm_reranking,
            self.use_mmr,
            self.prioritize_table,
            self.prompt_template_select,
        ]:
            component.change(
                self.save_chat_runtime_settings,
                inputs=chat_runtime_inputs,
                outputs=None,
                show_progress="hidden",
            )

        onSuggestChatEvent = {
            "fn": self.suggest_chat_conv,
            "inputs": [
                self._app.settings_state,
                self.language,
                self.chat_panel.chatbot,
                self._use_suggestion,
            ],
            "outputs": [
                self.followup_questions_ui,
                self.followup_questions,
            ],
            "show_progress": "hidden",
        }
        # chat suggestion toggle
        chat_event = chat_event.success(**onSuggestChatEvent)

        # final data persist
        if not KH_DEMO_MODE:
            chat_event = chat_event.then(
                fn=self.persist_data_source,
                inputs=[
                    self.chat_control.conversation_id,
                    self._app.user_id,
                    self.chat_panel.citation_panel,
                    self.state_plot_panel,
                    self.state_retrieval_history,
                    self.state_plot_history,
                    self.chat_panel.chatbot,
                    self.state_chat,
                ]
                + self._indices_input,
                outputs=[
                    self.state_retrieval_history,
                    self.state_plot_history,
                ],
                concurrency_limit=20,
            )

        chat_event.then(
            fn=None,
            inputs=None,
            outputs=None,
            js=chat_submit_done_js,
        )

        self.chat_control.btn_info_expand.click(
            fn=None,
            inputs=None,
            outputs=None,
            js="function() {toggleInfoColumn();}",
        )
        self.chat_control.btn_chat_expand.click(
            fn=None, inputs=None, js="function() {toggleChatColumn();}"
        )

        if KH_DEMO_MODE:
            self.chat_control.btn_demo_logout.click(
                fn=None,
                js=self.chat_control.logout_js,
            )
            self.chat_control.btn_new.click(
                fn=lambda: self.chat_control.select_conv("", None),
                outputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                    self.chat_panel.chatbot,
                    self.followup_questions,
                    self.chat_panel.citation_panel,
                    self.state_plot_panel,
                    self.state_retrieval_history,
                    self.state_plot_history,
                    self.chat_control.cb_is_public,
                    self.state_chat,
                ]
                + self._indices_input,
            ).then(
                lambda: (gr.update(visible=False), gr.update(visible=True)),
                outputs=[self.paper_list.accordion, self.chat_settings],
            ).then(
                fn=None,
                inputs=None,
                js=chat_input_focus_js,
            )

        if not KH_DEMO_MODE:
            self.chat_control.btn_new_top.click(
                fn=None,
                inputs=None,
                outputs=None,
                js="""
                function() {
                    document.querySelector("#new-conv-button")?.click();
                }
                """,
                show_progress="hidden",
            )
            self.chat_control.btn_new.click(
                self.chat_control.new_conv,
                inputs=self._app.user_id,
                outputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                ],
                show_progress="hidden",
            ).then(
                self.chat_control.select_conv,
                inputs=[self.chat_control.conversation, self._app.user_id],
                outputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                    self.chat_panel.chatbot,
                    self.followup_questions,
                    self.chat_panel.citation_panel,
                    self.state_plot_panel,
                    self.state_retrieval_history,
                    self.state_plot_history,
                    self.chat_control.cb_is_public,
                    self.state_chat,
                ]
                + self._indices_input,
                show_progress="hidden",
            ).then(
                fn=self._json_to_plot,
                inputs=self.state_plot_panel,
                outputs=self.plot_panel,
            ).then(
                fn=None,
                inputs=None,
                js=chat_input_focus_js,
            )

            self.chat_control.btn_del.click(
                lambda id: self.toggle_delete(id),
                inputs=[self.chat_control.conversation_id],
                outputs=[
                    self.chat_control._new_delete,
                    self.chat_control._delete_confirm,
                ],
            )
            self.chat_control.btn_del_conf.click(
                self.chat_control.delete_conv,
                inputs=[self.chat_control.conversation_id, self._app.user_id],
                outputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                ],
                show_progress="hidden",
            ).then(
                self.chat_control.select_conv,
                inputs=[self.chat_control.conversation, self._app.user_id],
                outputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                    self.chat_panel.chatbot,
                    self.followup_questions,
                    self.chat_panel.citation_panel,
                    self.state_plot_panel,
                    self.state_retrieval_history,
                    self.state_plot_history,
                    self.chat_control.cb_is_public,
                    self.state_chat,
                ]
                + self._indices_input,
                show_progress="hidden",
            ).then(
                fn=self._json_to_plot,
                inputs=self.state_plot_panel,
                outputs=self.plot_panel,
            ).then(
                lambda: self.toggle_delete(""),
                outputs=[
                    self.chat_control._new_delete,
                    self.chat_control._delete_confirm,
                ],
            )
            self.chat_control.btn_del_cnl.click(
                lambda: self.toggle_delete(""),
                outputs=[
                    self.chat_control._new_delete,
                    self.chat_control._delete_confirm,
                ],
            )
            self.chat_control.btn_conversation_rn.click(
                lambda: gr.update(visible=True),
                outputs=[
                    self.chat_control.conversation_rn,
                ],
            )
            self.chat_control.conversation_rn.submit(
                self.chat_control.rename_conv,
                inputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation_rn,
                    gr.State(value=True),
                    self._app.user_id,
                ],
                outputs=[
                    self.chat_control.conversation,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                ],
                show_progress="hidden",
            )

        onConvSelect = (
            self.chat_control.conversation.select(
                self.chat_control.select_conv,
                inputs=[self.chat_control.conversation, self._app.user_id],
                outputs=[
                    self.chat_control.conversation_id,
                    self.chat_control.conversation,
                    self.chat_control.conversation_rn,
                    self.chat_panel.chatbot,
                    self.followup_questions,
                    self.chat_panel.citation_panel,
                    self.state_plot_panel,
                    self.state_retrieval_history,
                    self.state_plot_history,
                    self.chat_control.cb_is_public,
                    self.state_chat,
                ]
                + self._indices_input,
                show_progress="hidden",
            )
            .then(
                fn=self._json_to_plot,
                inputs=self.state_plot_panel,
                outputs=self.plot_panel,
            )
            .then(
                lambda: self.toggle_delete(""),
                outputs=[
                    self.chat_control._new_delete,
                    self.chat_control._delete_confirm,
                ],
            )
        )

        if KH_DEMO_MODE:
            onConvSelect = onConvSelect.then(
                lambda: (gr.update(visible=False), gr.update(visible=True)),
                outputs=[self.paper_list.accordion, self.chat_settings],
            )

        onConvSelect = (
            onConvSelect.then(
                fn=lambda: True,
                js=clear_bot_message_selection_js,
            )
            .then(
                fn=lambda: True,
                inputs=None,
                outputs=[self._preview_links],
                js=pdfview_js,
            )
            .then(fn=None, inputs=None, outputs=None, js=chat_input_focus_js)
        )

        if not KH_DEMO_MODE:
            # evidence display on message selection
            self.chat_panel.chatbot.select(
                self.message_selected,
                inputs=[
                    self.state_retrieval_history,
                    self.state_plot_history,
                ],
                outputs=[
                    self.chat_panel.citation_panel,
                    self.state_plot_panel,
                ],
            ).then(
                fn=self._json_to_plot,
                inputs=self.state_plot_panel,
                outputs=self.plot_panel,
            ).then(
                fn=lambda: True,
                inputs=None,
                outputs=[self._preview_links],
                js=pdfview_js,
            )

        self.chat_control.cb_is_public.change(
            self.on_set_public_conversation,
            inputs=[self.chat_control.cb_is_public, self.chat_control.conversation],
            outputs=None,
            show_progress="hidden",
        )

        if not KH_DEMO_MODE:
            # user feedback events
            self.chat_panel.chatbot.like(
                fn=self.is_liked,
                inputs=[self.chat_control.conversation_id],
                outputs=None,
            )
            self.report_issue.report_btn.click(
                self.report_issue.report,
                inputs=[
                    self.report_issue.correctness,
                    self.report_issue.issues,
                    self.report_issue.more_detail,
                    self.chat_control.conversation_id,
                    self.chat_panel.chatbot,
                    self._app.settings_state,
                    self._app.user_id,
                    self.chat_panel.citation_panel,
                    self.state_chat,
                ]
                + self._indices_input,
                outputs=None,
            )

        self.reasoning_type.change(
            self.reasoning_changed,
            inputs=[self.reasoning_type],
            outputs=[self._reasoning_type],
        )
        self.use_mindmap_check.change(
            lambda x: (x, gr.update(label="Mindmap" + ("（开）" if x else "（关）"))),
            inputs=[self.use_mindmap_check],
            outputs=[self.use_mindmap, self.use_mindmap_check],
            show_progress="hidden",
        )

        def toggle_chat_suggestion(current_state):
            return current_state, gr.update(visible=current_state)

        def raise_error_on_state(state):
            if not state:
                raise ValueError("聊天建议已关闭")

        self.chat_control.cb_suggest_chat.change(
            fn=toggle_chat_suggestion,
            inputs=[self.chat_control.cb_suggest_chat],
            outputs=[self._use_suggestion, self.followup_questions_ui],
            show_progress="hidden",
        ).then(
            fn=raise_error_on_state,
            inputs=[self._use_suggestion],
            show_progress="hidden",
        ).success(
            **onSuggestChatEvent
        )
        self.chat_control.conversation_id.change(
            lambda: gr.update(visible=False),
            outputs=self.plot_panel,
        )

        self.followup_questions.select(
            self.chat_suggestion.select_example,
            outputs=[self.chat_panel.text_input],
            show_progress="hidden",
        ).then(
            fn=None,
            inputs=None,
            outputs=None,
            js=chat_input_focus_js,
        )

        if KH_DEMO_MODE:
            self.paper_list.examples.select(
                self.paper_list.select_example,
                inputs=[self.paper_list.papers_state],
                outputs=[self.quick_urls],
                show_progress="hidden",
            ).then(
                lambda: (gr.update(visible=False), gr.update(visible=True)),
                outputs=[self.paper_list.accordion, self.chat_settings],
            ).then(
                fn=None,
                inputs=None,
                outputs=None,
                js=quick_urls_submit_js,
            )

    def submit_msg(
        self,
        chat_input,
        chat_history,
        user_id,
        settings,
        conv_id,
        conv_name,
        first_selector_choices,
        request: gr.Request,
    ):
        """Submit a message to the chatbot"""
        if KH_DEMO_MODE:
            sso_user_id = check_rate_limit("chat", request)
            print("User ID:", sso_user_id)

        if not chat_input:
            raise ValueError("输入为空")

        chat_input_text = chat_input.get("text", "")
        file_ids = []
        display_file_names = []
        used_command = None

        first_selector_choices_map = {
            item[0]: item[1] for item in first_selector_choices
        }

        # get all file names with pattern @"filename" in input_str
        mentions, chat_input_text = get_mentions_regex(chat_input_text)

        # check if web search command is in file_names
        if WEB_SEARCH_COMMAND in mentions:
            used_command = WEB_SEARCH_COMMAND

        # get all file names in input_str
        file_names = [
            mention for mention in mentions if mention not in (WEB_SEARCH_COMMAND,)
        ]
        if file_names:
            indexed_file_ids = [
                first_selector_choices_map.get(file_name) for file_name in file_names
            ]
            file_ids.extend(
                [file_id for file_id in indexed_file_ids if file_id is not None]
            )
            display_file_names.extend(file_names)

        # get all urls in input_str
        urls, chat_input_text = get_urls(chat_input_text)
        if urls and self.first_indexing_url_fn:
            print("Detected URLs", urls)
            indexed_url_ids = self.first_indexing_url_fn(
                "\n".join(urls),
                True,
                settings,
                user_id,
                request=None,
            )
            file_ids.extend(indexed_url_ids)

            # Add new file ids to the first selector choices for display
            first_selector_choices.extend(zip(urls, indexed_url_ids))

        uploaded_files = self._get_chat_uploaded_file_paths(chat_input.get("files", []))
        if uploaded_files:
            if not self.first_indexing_file_fn:
                raise gr.Error("文件索引器尚未准备好，请稍后重试。")

            print("Detected chat uploads", uploaded_files)
            indexed_upload_ids = self.first_indexing_file_fn(
                uploaded_files,
                False,
                settings,
                user_id,
            )
            indexed_uploads = [
                (file_path, file_id)
                for file_path, file_id in zip(uploaded_files, indexed_upload_ids)
                if file_id is not None
            ]
            file_ids.extend(file_id for _, file_id in indexed_uploads)

            uploaded_file_names = [
                self._get_chat_uploaded_file_name(file_path)
                for file_path, _ in indexed_uploads
            ]
            display_file_names.extend(uploaded_file_names)
            first_selector_choices.extend(
                (file_name, file_id)
                for file_name, (_, file_id) in zip(uploaded_file_names, indexed_uploads)
            )

        display_chat_input_text = self._build_display_chat_input(
            chat_input.get("text", ""),
            display_file_names,
        )

        # if file_ids is not empty and chat_input_text is empty
        # set the input to summary
        if not chat_input_text and file_ids:
            chat_input_text = DEFAULT_QUESTION

        # if start of conversation and no query is specified
        if not chat_input_text and not chat_history:
            chat_input_text = DEFAULT_QUESTION

        if file_ids:
            selector_output = [
                "select",
                gr.update(value=file_ids, choices=first_selector_choices),
            ]
        else:
            selector_output = [gr.update(), gr.update()]

        # check if regen mode is active
        if chat_input_text:
            chat_history = chat_history + [(display_chat_input_text, None)]
        else:
            if not chat_history:
                raise gr.Error("聊天为空")

        if not conv_id:
            if not KH_DEMO_MODE:
                id_, update = self.chat_control.new_conv(user_id)
                with Session(engine) as session:
                    statement = select(Conversation).where(Conversation.id == id_)
                    name = session.exec(statement).one().name
                    new_conv_id = id_
                    conv_update = update
                    new_conv_name = name
            else:
                new_conv_id, new_conv_name, conv_update = None, None, gr.update()
        else:
            new_conv_id = conv_id
            conv_update = gr.update()
            new_conv_name = conv_name

        return (
            [
                {},
                chat_history,
                new_conv_id,
                conv_update,
                new_conv_name,
            ]
            + selector_output
            + [used_command]
        )

    @staticmethod
    def _get_chat_uploaded_file_paths(files) -> list[str]:
        file_paths = []
        for file in files or []:
            if isinstance(file, (str, os.PathLike)):
                file_paths.append(str(file))
            elif isinstance(file, dict):
                path = file.get("path") or file.get("name")
                if path:
                    file_paths.append(str(path))
            else:
                path = getattr(file, "path", None) or getattr(file, "name", None)
                if path:
                    file_paths.append(str(path))
        return file_paths

    @staticmethod
    def _get_chat_uploaded_file_name(file_path: str) -> str:
        return Path(str(file_path)).name

    @staticmethod
    def _build_display_chat_input(chat_input_text: str, file_names: list[str]) -> str:
        display_text = format_mentions_for_display(chat_input_text)
        for file_name in file_names:
            file_mention = f'@"{file_name}"'
            if (
                file_mention not in chat_input_text
                and f"@{file_name}" not in chat_input_text
            ):
                display_text = f"{display_text} {file_mention}".strip()
        return format_mentions_for_display(display_text)

    def get_recommendations(self, first_selector_choices, file_ids):
        first_selector_choices_map = {
            item[1]: item[0] for item in first_selector_choices
        }
        file_names = [first_selector_choices_map[file_id] for file_id in file_ids]
        if not file_names:
            return ""

        first_file_name = file_names[0].split(".")[0].replace("_", " ")
        return get_recommended_papers(first_file_name)

    def toggle_delete(self, conv_id):
        if conv_id:
            return gr.update(visible=False), gr.update(visible=True)
        else:
            return gr.update(visible=True), gr.update(visible=False)

    def on_set_public_conversation(self, is_public, convo_id):
        if not convo_id:
            gr.Warning("未选择会话")
            return

        with Session(engine) as session:
            statement = select(Conversation).where(Conversation.id == convo_id)

            result = session.exec(statement).one()
            name = result.name

            if result.is_public != is_public:
                # Only trigger updating when user
                # select different value from the current
                result.is_public = is_public
                session.add(result)
                session.commit()

                gr.Info(
                    f"会话：{name} 已设为{'公开' if is_public else '私有'}。"
                )

    def on_subscribe_public_events(self):
        if self._app.f_user_management:
            self._app.subscribe_event(
                name="onSignIn",
                definition={
                    "fn": self.chat_control.reload_conv,
                    "inputs": [self._app.user_id],
                    "outputs": [self.chat_control.conversation],
                    "show_progress": "hidden",
                },
            )

            self._app.subscribe_event(
                name="onSignOut",
                definition={
                    "fn": lambda: self.chat_control.select_conv("", None),
                    "outputs": [
                        self.chat_control.conversation_id,
                        self.chat_control.conversation,
                        self.chat_control.conversation_rn,
                        self.chat_panel.chatbot,
                        self.followup_questions,
                        self.chat_panel.citation_panel,
                        self.state_plot_panel,
                        self.state_retrieval_history,
                        self.state_plot_history,
                        self.chat_control.cb_is_public,
                        self.state_chat,
                    ]
                    + self._indices_input,
                    "show_progress": "hidden",
                },
            )
            if not KH_DEMO_MODE:
                for event_name in ["onSignIn", "onSignOut"]:
                    self._app.subscribe_event(
                        name=event_name,
                        definition={
                            "fn": self.load_chat_runtime_settings,
                            "inputs": [self._app.user_id],
                            "outputs": [
                                self.reasoning_type,
                                self.model_type,
                                self.language,
                                self.citation,
                                self.use_mindmap,
                                self.use_mindmap_check,
                                self.retrieval_top_k,
                                self.first_round_multiplier,
                                self.retrieval_mode,
                                self.retrieval_enhancement,
                                self.use_reranking,
                                self.use_llm_reranking,
                                self.use_mmr,
                                self.prioritize_table,
                                self.prompt_templates_state,
                                self.prompt_template_select,
                                self.prompt_template_name,
                                self.prompt_template_text,
                            ],
                            "show_progress": "hidden",
                        },
                    )

    def _on_app_created(self):
        if KH_DEMO_MODE:
            self._app.app.load(
                fn=lambda x: x,
                inputs=[self._user_api_key],
                outputs=[self._user_api_key],
                js=fetch_api_key_js,
            ).then(
                fn=self.chat_control.toggle_demo_login_visibility,
                inputs=[self._user_api_key],
                outputs=[
                    self.chat_control.cb_suggest_chat,
                    self.chat_control.btn_new,
                    self.chat_control.btn_demo_logout,
                    self.chat_control.btn_demo_login,
                ],
            ).then(
                fn=None,
                inputs=None,
                js=chat_input_focus_js,
            )
        elif not KH_DEMO_MODE:
            self._app.app.load(
                self.load_chat_runtime_settings,
                inputs=[self._app.user_id],
                outputs=[
                    self.reasoning_type,
                    self.model_type,
                    self.language,
                    self.citation,
                    self.use_mindmap,
                    self.use_mindmap_check,
                    self.retrieval_top_k,
                    self.first_round_multiplier,
                    self.retrieval_mode,
                    self.retrieval_enhancement,
                    self.use_reranking,
                    self.use_llm_reranking,
                    self.use_mmr,
                    self.prioritize_table,
                    self.prompt_templates_state,
                    self.prompt_template_select,
                    self.prompt_template_name,
                    self.prompt_template_text,
                ],
            )

    def persist_data_source(
        self,
        convo_id,
        user_id,
        retrieval_msg,
        plot_data,
        retrival_history,
        plot_history,
        messages,
        state,
        *selecteds,
    ):
        """Update the data source"""
        if not convo_id:
            gr.Warning("未选择会话")
            return

        # if not regen, then append the new message
        if not state["app"].get("regen", False):
            retrival_history = retrival_history + [retrieval_msg]
            plot_history = plot_history + [None]
        else:
            if retrival_history:
                print("Updating retrieval history (regen=True)")
                retrival_history[-1] = retrieval_msg
                plot_history[-1] = None

        # reset regen state
        state["app"]["regen"] = False

        selecteds_ = {}
        for index in self._app.index_manager.indices:
            if index.selector is None:
                continue
            if isinstance(index.selector, int):
                selecteds_[str(index.id)] = selecteds[index.selector]
            else:
                selecteds_[str(index.id)] = [selecteds[i] for i in index.selector]

        with Session(engine) as session:
            statement = select(Conversation).where(Conversation.id == convo_id)
            result = session.exec(statement).one()

            data_source = result.data_source
            old_selecteds = data_source.get("selected", {})
            is_owner = result.user == user_id

            # Write down to db
            result.data_source = {
                "selected": selecteds_ if is_owner else old_selecteds,
                "messages": messages,
                "retrieval_messages": retrival_history,
                "plot_history": plot_history,
                "state": state,
                "likes": deepcopy(data_source.get("likes", [])),
            }
            session.add(result)
            session.commit()

        return retrival_history, plot_history

    def reasoning_changed(self, reasoning_type):
        if reasoning_type != DEFAULT_SETTING:
            # override app settings state (temporary)
            gr.Info("Reasoning 类型已切换为 `{}`".format(reasoning_type))
        return reasoning_type

    def is_liked(self, convo_id, liked: gr.LikeData):
        with Session(engine) as session:
            statement = select(Conversation).where(Conversation.id == convo_id)
            result = session.exec(statement).one()

            data_source = deepcopy(result.data_source)
            likes = data_source.get("likes", [])
            likes.append([liked.index, liked.value, liked.liked])
            data_source["likes"] = likes

            result.data_source = data_source
            session.add(result)
            session.commit()

    def message_selected(self, retrieval_history, plot_history, msg: gr.SelectData):
        index = msg.index[0]
        try:
            retrieval_content = retrieval_history[index]
        except IndexError:
            retrieval_content = gr.update()

        return retrieval_content, None

    def create_pipeline(
        self,
        settings: dict,
        session_reasoning_type: str,
        session_llm: str,
        session_use_mindmap: bool | str,
        session_use_citation: str,
        session_language: str,
        session_top_k: int | float,
        session_first_round_multiplier: int | float,
        session_retrieval_mode: str,
        session_retrieval_enhancement: str,
        session_use_reranking: bool,
        session_use_llm_reranking: bool,
        session_use_mmr: bool,
        session_prioritize_table: bool,
        session_prompt_template: str,
        state: dict,
        command_state: str | None,
        user_id: int,
        *selecteds,
        session_graph_enabled: bool = False,
        session_graph_provider: str = "lightrag",
        session_graph_search_type: str = "local",
    ):
        """Create the pipeline from settings

        Args:
            settings: the settings of the app
            state: the state of the app
            selected: the list of file ids that will be served as context. If None, then
                consider using all files

        Returns:
            - the pipeline objects
        """
        # override reasoning_mode by temporary chat page state
        print(
            "Session reasoning type",
            session_reasoning_type,
            "use mindmap",
            session_use_mindmap,
            "use citation",
            session_use_citation,
            "language",
            session_language,
        )
        print("Session LLM", session_llm)
        reasoning_mode = (
            settings["reasoning.use"]
            if session_reasoning_type in (DEFAULT_SETTING, None)
            else session_reasoning_type
        )
        reasoning_cls = reasonings[reasoning_mode]
        print("Reasoning class", reasoning_cls)
        reasoning_id = reasoning_cls.get_info()["id"]

        settings = deepcopy(settings)
        llm_setting_key = f"reasoning.options.{reasoning_id}.llm"
        if llm_setting_key in settings and session_llm not in (
            DEFAULT_SETTING,
            None,
            "",
        ):
            settings[llm_setting_key] = session_llm

        if session_use_mindmap not in (DEFAULT_SETTING, None):
            settings["reasoning.options.simple.create_mindmap"] = session_use_mindmap

        if session_use_citation not in (DEFAULT_SETTING, None):
            settings[
                "reasoning.options.simple.highlight_citation"
            ] = session_use_citation

        if session_language not in (DEFAULT_SETTING, None):
            settings["reasoning.lang"] = session_language

        if session_prompt_template:
            settings["reasoning.options.simple.qa_prompt"] = session_prompt_template
            if f"reasoning.options.{reasoning_id}.qa_prompt" in settings:
                settings[f"reasoning.options.{reasoning_id}.qa_prompt"] = (
                    session_prompt_template
                )

        settings["reasoning.retrieval_enhancement"] = (
            session_retrieval_enhancement
            if session_retrieval_enhancement in {"none", "rewrite", "hyde", "fusion"}
            else "none"
        )

        try:
            session_top_k = int(session_top_k)
            session_first_round_multiplier = int(session_first_round_multiplier)
        except (TypeError, ValueError):
            session_top_k = 10
            session_first_round_multiplier = 10

        for index in self._app.index_manager.indices:
            if index.__class__ is not FileIndex:
                continue
            prefix = f"index.options.{index.id}."
            settings[prefix + "num_retrieval"] = session_top_k
            settings[prefix + "first_round_top_k_mult"] = (
                session_first_round_multiplier
            )
            settings[prefix + "retrieval_mode"] = session_retrieval_mode or "hybrid"
            settings[prefix + "use_reranking"] = bool(session_use_reranking)
            settings[prefix + "use_llm_reranking"] = bool(session_use_llm_reranking)
            settings[prefix + "mmr"] = bool(session_use_mmr)
            settings[prefix + "prioritize_table"] = bool(session_prioritize_table)
            settings[prefix + "graph_enabled"] = bool(session_graph_enabled)
            settings[prefix + "graph_provider"] = session_graph_provider or "lightrag"
            settings[prefix + "graph_search_type"] = session_graph_search_type or "local"

        # get retrievers
        retrievers = []

        if command_state == WEB_SEARCH_COMMAND:
            # set retriever for web search
            if not WebSearch:
                raise ValueError("Web search back-end is not available.")

            web_search = WebSearch()
            retrievers.append(web_search)
        else:
            for index in self._app.index_manager.indices:
                if index.__class__ is not FileIndex:
                    continue
                if isinstance(index.selector, int):
                    index_selected = selecteds[index.selector]
                elif isinstance(index.selector, tuple):
                    index_selected = tuple(selecteds[i] for i in index.selector)
                else:
                    index_selected = None
                iretrievers = index.get_retriever_pipelines(
                    settings, user_id, index_selected
                )
                retrievers += iretrievers

        # prepare states
        reasoning_state = {
            "app": deepcopy(state["app"]),
            "pipeline": deepcopy(state.get(reasoning_id, {})),
        }

        pipeline = reasoning_cls.get_pipeline(settings, reasoning_state, retrievers)

        return pipeline, reasoning_state

    def _has_selected_files(self, user_id: int, *selecteds) -> bool:
        """Return True if any index file selector has documents selected."""
        for index in self._app.index_manager.indices:
            if index.selector is None:
                continue
            index_ui = getattr(self, f"_index_{index.id}", None)
            if index_ui is None or not hasattr(index_ui, "get_selected_ids"):
                continue
            if isinstance(index.selector, int):
                components = (selecteds[index.selector],)
            else:
                components = tuple(selecteds[i] for i in index.selector)
            if index_ui.get_selected_ids(components):
                return True
        return False

    def chat_fn(
        self,
        conversation_id,
        chat_history,
        settings,
        reasoning_type,
        llm_type,
        use_mind_map,
        use_citation,
        language,
        retrieval_top_k,
        first_round_multiplier,
        retrieval_mode,
        retrieval_enhancement,
        use_reranking,
        use_llm_reranking,
        use_mmr,
        prioritize_table,
        prompt_template,
        chat_state,
        command_state,
        user_id,
        *selecteds,
    ):
        """Chat function"""
        display_input, chat_output = chat_history[-1]
        chat_history = chat_history[:-1]

        # if chat_input is empty, assume regen mode
        if chat_output:
            chat_state["app"]["regen"] = True

        llm_query = prepare_llm_query(
            display_input,
            has_selected_files=self._has_selected_files(user_id, *selecteds),
            default_question=DEFAULT_QUESTION,
        )

        queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()

        # construct the pipeline
        pipeline, reasoning_state = self.create_pipeline(
            settings,
            reasoning_type,
            llm_type,
            use_mind_map,
            use_citation,
            language,
            retrieval_top_k,
            first_round_multiplier,
            retrieval_mode,
            retrieval_enhancement,
            use_reranking,
            use_llm_reranking,
            use_mmr,
            prioritize_table,
            prompt_template,
            chat_state,
            command_state,
            user_id,
            *selecteds,
        )
        print("Reasoning state", reasoning_state)
        pipeline.set_output_queue(queue)

        text = ""
        refs = ""
        info_panel_content = ""
        plot, plot_gr = None, gr.update(visible=False)
        citation_panel_content = ""
        msg_placeholder = getattr(
            flowsettings, "KH_CHAT_MSG_PLACEHOLDER", "Thinking ..."
        )
        print(msg_placeholder)
        yield (
            chat_history
            + [(display_input, self.format_answer_with_refs(text, refs, msg_placeholder))],
            citation_panel_content,
            info_panel_content,
            plot_gr,
            plot,
            chat_state,
        )

        try:
            for response in pipeline.stream(
                llm_query,
                conversation_id,
                chat_history,
            ):

                if not isinstance(response, Document):
                    continue

                if response.channel is None:
                    continue

                if response.channel == "chat":
                    if response.content is None:
                        text = ""
                    else:
                        text += response.content

                if response.channel == "info":
                    info_panel_content = self.append_info_content(
                        info_panel_content, response.content
                    )

                if response.channel == "citation":
                    refs = self.append_info_content(refs, response.content)
                    citation_panel_content = self.format_citation_panel(refs)

                if response.channel == "plot":
                    plot, plot_gr = None, gr.update(visible=False)

                chat_state[pipeline.get_info()["id"]] = reasoning_state["pipeline"]

                yield (
                    chat_history
                    + [
                        (
                            display_input,
                            self.format_answer_with_refs(text, refs, msg_placeholder),
                        )
                    ],
                    citation_panel_content,
                    info_panel_content,
                    plot_gr,
                    plot,
                    chat_state,
                )
        except ValueError as e:
            print(e)

        if not text:
            empty_msg = getattr(
                flowsettings, "KH_CHAT_EMPTY_MSG_PLACEHOLDER", "(Sorry, I don't know)"
            )
            print(f"Generate nothing: {empty_msg}")
            yield (
                chat_history
                + [(display_input, self.format_answer_with_refs(text, refs, empty_msg))],
                citation_panel_content,
                info_panel_content,
                plot_gr,
                plot,
                chat_state,
            )

    def check_and_suggest_name_conv(self, chat_history):
        suggest_pipeline = SuggestConvNamePipeline()
        new_name = gr.update()
        renamed = False

        # check if this is a newly created conversation
        if len(chat_history) == 1:
            suggested_name = suggest_pipeline(chat_history).text
            suggested_name = strip_think_tag(suggested_name)
            suggested_name = suggested_name.replace('"', "").replace("'", "")[:40]
            new_name = gr.update(value=suggested_name)
            renamed = True

        return new_name, renamed

    def suggest_chat_conv(
        self,
        settings,
        session_language,
        chat_history,
        use_suggestion,
    ):
        target_language = (
            session_language
            if session_language not in (DEFAULT_SETTING, None)
            else settings["reasoning.lang"]
        )
        if use_suggestion:
            suggest_pipeline = SuggestFollowupQuesPipeline()
            suggest_pipeline.lang = SUPPORTED_LANGUAGE_MAP.get(
                target_language, "English"
            )
            suggested_questions = [[each] for each in ChatSuggestion.CHAT_SAMPLES]

            if len(chat_history) >= 1:
                suggested_resp = suggest_pipeline(chat_history).text
                if ques_res := re.search(
                    r"\[(.*?)\]", re.sub("\n", "", suggested_resp)
                ):
                    ques_res_str = ques_res.group()
                    try:
                        suggested_questions = json.loads(ques_res_str)
                        suggested_questions = [[x] for x in suggested_questions]
                    except Exception:
                        pass

            return gr.update(visible=True), suggested_questions

        return gr.update(visible=False), gr.update()
