from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any

import pluggy
from ktem import extension_protocol
from ktem.components import reasonings
from ktem.exceptions import HookAlreadyDeclared, HookNotDeclared
from ktem.index import IndexManager
from ktem.index.file import FileIndex
from ktem.permissions import can_read_source
from ktem.reasoning.prompt_optimization.suggest_conversation_name import (
    SuggestConvNamePipeline,
)
from ktem.reasoning.prompt_optimization.suggest_followup_chat import (
    SuggestFollowupQuesPipeline,
)
from ktem.settings import BaseSettingGroup, SettingGroup, SettingReasoningGroup
from ktem.utils.commands import WEB_SEARCH_COMMAND
from sqlmodel import Session, select
from theflow.settings import settings
from theflow.utils.modules import import_dotted_string

from kotaemon.base import Document

from .db.models import Conversation, engine
from .react_defaults import (
    ANSWER_OPENING_BOILERPLATE_RE,
    DEFAULT_PROMPT_TEMPLATE_NAME,
    DEFAULT_PROMPT_TEMPLATE_TEXT,
    DEFAULT_PROMPT_TEMPLATES,
    DEFAULT_SETTING,
    STATE,
)

KH_APP_DATA_DIR = getattr(settings, "KH_APP_DATA_DIR", ".")
KH_WEB_SEARCH_BACKEND = getattr(settings, "KH_WEB_SEARCH_BACKEND", None)
WebSearch = None
if KH_WEB_SEARCH_BACKEND:
    try:
        WebSearch = import_dotted_string(KH_WEB_SEARCH_BACKEND, safe=False)
    except (ImportError, AttributeError) as e:
        print(f"Error importing {KH_WEB_SEARCH_BACKEND}: {e}")


class HeadlessFileSelector:
    def __init__(self, app: "ReactRuntime", index: FileIndex):
        self._app = app
        self._index = index

    @staticmethod
    def _flatten_selected_ids(selected: Any) -> list[str]:
        selected_ids = []
        for item in selected or []:
            if not item:
                continue
            if isinstance(item, list):
                selected_ids.extend(str(file_id) for file_id in item if file_id)
                continue
            if isinstance(item, str) and item.startswith("["):
                try:
                    selected_ids.extend(
                        str(file_id) for file_id in json.loads(item) if file_id
                    )
                    continue
                except json.JSONDecodeError:
                    pass
            selected_ids.append(str(item))
        return selected_ids

    def get_selected_ids(self, components: tuple[Any, ...]) -> list[str]:
        mode = components[0] if len(components) > 0 else "all"
        selected = components[1] if len(components) > 1 else []
        user_id = components[2] if len(components) > 2 else "default"
        if user_id is None or mode == "disabled":
            return []
        if mode == "select":
            return self._flatten_selected_ids(selected)

        file_ids = []
        with Session(engine) as session:
            Source = self._index._resources["Source"]
            for (source,) in session.execute(select(Source)).all():
                if can_read_source(self._index, source, user_id):
                    file_ids.append(source.id)
        return file_ids

    def get_selected_ids_for_user(
        self, components: tuple[Any, ...], user_id: str
    ) -> list[str]:
        if not components:
            return []
        if len(components) < 3:
            components = tuple(components) + (user_id,)
        else:
            components = (components[0], components[1], user_id)
        return self.get_selected_ids(components)


class ReactChatRuntime:
    def __init__(self, app: "ReactRuntime") -> None:
        self._app = app
        self.first_indexing_file_fn = self.index_files_with_default_loaders
        self.first_indexing_url_fn = self.index_urls_with_default_loaders

    def _prompt_store_path(self):
        return settings.KH_USER_DATA_DIR / "prompt_templates.json"

    def _chat_runtime_settings_path(self):
        return settings.KH_USER_DATA_DIR / "chat_runtime_settings.json"

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
        store_path.parent.mkdir(parents=True, exist_ok=True)
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
        store_path.parent.mkdir(parents=True, exist_ok=True)
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

    def save_chat_runtime_settings(
        self,
        user_id,
        reasoning_type,
        model_type,
        embedding_model,
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
        service_configs=None,
    ):
        data = self._read_chat_runtime_settings()
        user_key = str(user_id or "default")
        data[user_key] = {
            "reasoning_type": reasoning_type,
            "model_type": "" if model_type in (DEFAULT_SETTING, None) else model_type,
            "embedding_model": ""
            if embedding_model in (DEFAULT_SETTING, None)
            else embedding_model,
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
            "service_configs": service_configs or {},
            "prompt_template_select": prompt_template_select
            or DEFAULT_PROMPT_TEMPLATE_NAME,
        }
        self._write_chat_runtime_settings(data)

    @staticmethod
    def strip_answer_opening_boilerplate(text: str) -> str:
        previous = None
        stripped = text or ""
        while previous != stripped:
            previous = stripped
            stripped = ANSWER_OPENING_BOILERPLATE_RE.sub("", stripped).lstrip()
        return stripped

    def format_answer_with_refs(self, text, refs, placeholder):
        return self.strip_answer_opening_boilerplate(text) or placeholder

    def _file_index(self) -> FileIndex:
        for index in self._app.index_manager.indices:
            if isinstance(index, FileIndex):
                return index
        raise RuntimeError("File index is not ready")

    def _may_extract_zip(self, files: list[str], zip_dir: str) -> list[str]:
        zip_files = [file for file in files if file.endswith(".zip")]
        remaining_files = [file for file in files if not file.endswith(".zip")]
        shutil.rmtree(zip_dir, ignore_errors=True)
        for zip_file in zip_files:
            basename = os.path.splitext(os.path.basename(zip_file))[0]
            zip_out_dir = os.path.join(zip_dir, basename)
            os.makedirs(zip_out_dir, exist_ok=True)
            with zipfile.ZipFile(zip_file, "r") as zip_ref:
                zip_ref.extractall(zip_out_dir)

        supported = self._supported_file_types()
        for root, _dirs, names in os.walk(zip_dir):
            for name in names:
                ext = os.path.splitext(name)[1]
                if ext != ".zip" and ext in supported:
                    remaining_files.append(os.path.join(root, name))
        return remaining_files

    def _supported_file_types(self) -> list[str]:
        index = self._file_index()
        value = index.config.get("supported_file_types", "")
        return [each.strip() for each in value.split(",") if each.strip()]

    def validate_files(self, files: list[str]) -> list[str]:
        index = self._file_index()
        paths = [Path(file) for file in files]
        errors = []
        if max_file_size := index.config.get("max_file_size", 0):
            errors_max_size = [
                path.name for path in paths if path.stat().st_size > max_file_size * 1e6
            ]
            if errors_max_size:
                str_errors = ", ".join(errors_max_size)
                if len(str_errors) > 60:
                    str_errors = str_errors[:55] + "..."
                errors.append(f"超出最大文件大小 ({max_file_size} MB)：{str_errors}")

        if max_number_of_files := index.config.get("max_number_of_files", 0):
            with Session(engine) as session:
                current_num_files = session.query(index._resources["Source"].id).count()
            if len(paths) + current_num_files > max_number_of_files:
                errors.append(f"将超出最大文件数量 ({max_number_of_files})")
        return errors

    @staticmethod
    def validate_urls(urls: list[str]) -> list[str]:
        return [
            f"无效 URL `{url}`"
            for url in urls
            if not url.startswith("http") and not url.startswith("https")
        ]

    def index_fn(self, files, urls, reindex: bool, settings_dict, user_id):
        if urls:
            files = [it.strip() for it in urls.split("\n") if it.strip()]
            errors = self.validate_urls(files)
        else:
            if not files:
                return []
            files = self._may_extract_zip(files, settings.KH_ZIP_INPUT_DIR)
            errors = self.validate_files(files)
        if errors:
            raise ValueError(", ".join(errors))

        indexing_pipeline = self._file_index().get_indexing_pipeline(
            settings_dict, user_id
        )
        outputs = []
        output_stream = indexing_pipeline.stream(files, reindex=reindex)
        try:
            while True:
                next(output_stream)
        except StopIteration as e:
            results, _index_errors, _docs = e.value
            outputs = results
        return outputs

    def index_files_with_default_loaders(
        self, files, reindex: bool, settings_dict, user_id
    ) -> list[str]:
        index = self._file_index()
        ordered_ids: list[str | None] = []
        to_process_files = []
        to_process_indices = []
        for str_file_path in files:
            file_path = Path(str(str_file_path))
            exist_id = (
                index.get_indexing_pipeline(settings_dict, user_id)
                .route(file_path)
                .get_id_if_exists(file_path)
            )
            if exist_id and not reindex:
                ordered_ids.append(exist_id)
            else:
                ordered_ids.append(None)
                to_process_indices.append(len(ordered_ids) - 1)
                to_process_files.append(str_file_path)

        settings_dict = deepcopy(settings_dict)
        settings_dict[f"index.options.{index.id}.reader_mode"] = "default"
        settings_dict.setdefault(f"index.options.{index.id}.quick_index_mode", True)
        if to_process_files:
            returned_ids = self.index_fn(
                to_process_files, [], reindex, settings_dict, user_id
            )
            for idx, returned_id in zip(to_process_indices, returned_ids):
                ordered_ids[idx] = returned_id
        return [item for item in ordered_ids if item]

    def index_urls_with_default_loaders(
        self,
        urls,
        reindex: bool,
        settings_dict,
        user_id,
        request=None,
    ) -> list[str]:
        index = self._file_index()
        settings_dict = deepcopy(settings_dict)
        settings_dict[f"index.options.{index.id}.reader_mode"] = "default"
        settings_dict[f"index.options.{index.id}.quick_index_mode"] = True
        if not urls:
            return []
        return self.index_fn([], urls, reindex, settings_dict, user_id)

    def create_pipeline(
        self,
        settings_dict: dict,
        session_reasoning_type: str,
        session_llm: str,
        session_embedding: str,
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
        user_id: str,
        *selecteds,
        session_graph_enabled: bool = False,
        session_graph_provider: str = "lightrag",
        session_graph_search_type: str = "local",
    ):
        reasoning_mode = (
            settings_dict["reasoning.use"]
            if session_reasoning_type in (DEFAULT_SETTING, None)
            else session_reasoning_type
        )
        reasoning_cls = reasonings[reasoning_mode]
        reasoning_id = reasoning_cls.get_info()["id"]

        settings_dict = deepcopy(settings_dict)
        llm_setting_key = f"reasoning.options.{reasoning_id}.llm"
        if llm_setting_key in settings_dict and session_llm not in (
            DEFAULT_SETTING,
            None,
            "",
        ):
            settings_dict[llm_setting_key] = session_llm

        if session_use_mindmap not in (DEFAULT_SETTING, None):
            settings_dict["reasoning.options.simple.create_mindmap"] = (
                session_use_mindmap
            )
        if session_use_citation not in (DEFAULT_SETTING, None):
            settings_dict["reasoning.options.simple.highlight_citation"] = (
                session_use_citation
            )
        if session_language not in (DEFAULT_SETTING, None):
            settings_dict["reasoning.lang"] = session_language

        if session_prompt_template:
            settings_dict["reasoning.options.simple.qa_prompt"] = (
                session_prompt_template
            )
            if f"reasoning.options.{reasoning_id}.qa_prompt" in settings_dict:
                settings_dict[f"reasoning.options.{reasoning_id}.qa_prompt"] = (
                    session_prompt_template
                )

        settings_dict["reasoning.retrieval_enhancement"] = (
            session_retrieval_enhancement
            if session_retrieval_enhancement in {"none", "rewrite", "hyde", "fusion"}
            else "none"
        )

        embedding_override = (
            None
            if session_embedding in (DEFAULT_SETTING, None, "", "default")
            else session_embedding
        )

        try:
            session_top_k = int(session_top_k)
            session_first_round_multiplier = int(session_first_round_multiplier)
        except (TypeError, ValueError):
            session_top_k = 10
            session_first_round_multiplier = 10

        for index in self._app.index_manager.indices:
            if not isinstance(index, FileIndex):
                continue
            prefix = f"index.options.{index.id}."
            settings_dict[prefix + "num_retrieval"] = session_top_k
            settings_dict[prefix + "first_round_top_k_mult"] = (
                session_first_round_multiplier
            )
            settings_dict[prefix + "retrieval_mode"] = (
                session_retrieval_mode or "hybrid"
            )
            settings_dict[prefix + "use_reranking"] = bool(session_use_reranking)
            settings_dict[prefix + "use_llm_reranking"] = bool(session_use_llm_reranking)
            settings_dict[prefix + "mmr"] = bool(session_use_mmr)
            settings_dict[prefix + "prioritize_table"] = bool(session_prioritize_table)
            settings_dict[prefix + "graph_enabled"] = bool(session_graph_enabled)
            settings_dict[prefix + "graph_provider"] = (
                session_graph_provider or "lightrag"
            )
            settings_dict[prefix + "graph_search_type"] = (
                session_graph_search_type or "local"
            )
            if embedding_override:
                settings_dict[prefix + "embedding"] = embedding_override

        retrievers = []
        if command_state == WEB_SEARCH_COMMAND:
            if not WebSearch:
                raise ValueError("Web search back-end is not available.")
            retrievers.append(WebSearch())
        else:
            for index in self._app.index_manager.indices:
                if not isinstance(index, FileIndex):
                    continue
                if isinstance(index.selector, int):
                    index_selected = selecteds[index.selector]
                elif isinstance(index.selector, tuple):
                    index_selected = tuple(selecteds[i] for i in index.selector)
                else:
                    index_selected = None
                retrievers += index.get_retriever_pipelines(
                    settings_dict, user_id, index_selected
                )

        reasoning_state = {
            "app": deepcopy(state["app"]),
            "pipeline": deepcopy(state.get(reasoning_id, {})),
        }
        pipeline = reasoning_cls.get_pipeline(settings_dict, reasoning_state, retrievers)
        return pipeline, reasoning_state

    def check_and_suggest_name_conv(self, chat_history):
        if len(chat_history) == 1:
            suggested_name = SuggestConvNamePipeline()(chat_history).text
            suggested_name = re.sub(r"<think>.*?</think>", "", suggested_name, flags=re.S)
            return suggested_name.replace('"', "").replace("'", "")[:40], True
        return None, False

    def suggest_chat_conv(
        self,
        settings_dict,
        session_language,
        chat_history,
        use_suggestion,
    ):
        if not use_suggestion:
            return []
        target_language = (
            session_language
            if session_language not in (DEFAULT_SETTING, None)
            else settings_dict["reasoning.lang"]
        )
        suggest_pipeline = SuggestFollowupQuesPipeline()
        suggest_pipeline.lang = target_language
        suggested_resp = suggest_pipeline(chat_history).text
        if match := re.search(r"\[(.*?)\]", re.sub("\n", "", suggested_resp)):
            try:
                return json.loads(match.group())
            except Exception:
                return []
        return []


class ReactRuntime:
    def __init__(self):
        self.dev_mode = getattr(settings, "KH_MODE", "") == "dev"
        self.app_name = getattr(settings, "KH_APP_NAME", "SecureRAG")
        self.app_version = getattr(settings, "KH_APP_VERSION", "")
        self.f_user_management = getattr(settings, "KH_FEATURE_USER_MANAGEMENT", False)
        self._favicon = str(Path(__file__).parent / "assets" / "img" / "favicon.svg")
        self.default_settings = SettingGroup(
            application=BaseSettingGroup(settings=settings.SETTINGS_APP),
            reasoning=SettingReasoningGroup(settings=settings.SETTINGS_REASONING),
        )
        self._events: dict[str, list] = {}

        self.register_extensions()
        self.register_reasonings()
        self.initialize_indices()

        self.default_settings.reasoning.finalize()
        self.default_settings.index.finalize()
        self.chat_page = ReactChatRuntime(self)

    def register_extensions(self):
        self.exman = pluggy.PluginManager("ktem")
        self.exman.add_hookspecs(extension_protocol)
        self.exman.load_setuptools_entrypoints("ktem")
        extension_declarations = self.exman.hook.ktem_declare_extensions()
        for extension_declaration in extension_declarations:
            functionality = extension_declaration["functionality"]
            if "reasoning" in functionality:
                for rid, rdec in functionality["reasoning"].items():
                    unique_rid = f"{extension_declaration['id']}/{rid}"
                    self.default_settings.reasoning.options[
                        unique_rid
                    ] = BaseSettingGroup(settings=rdec["settings"])

    def register_reasonings(self):
        if getattr(settings, "KH_REASONINGS", None) is None:
            return
        for value in settings.KH_REASONINGS:
            reasoning_cls = import_dotted_string(value, safe=False)
            rid = reasoning_cls.get_info()["id"]
            reasonings[rid] = reasoning_cls
            options = reasoning_cls().get_user_settings()
            self.default_settings.reasoning.options[rid] = BaseSettingGroup(
                settings=options
            )

    def initialize_indices(self):
        self.index_manager = IndexManager(self)
        self.index_manager.on_application_startup()
        for index in self.index_manager.indices:
            if isinstance(index, FileIndex):
                index._selector_ui = HeadlessFileSelector(self, index)
                if getattr(index, "selector", None) is None:
                    index.selector = 0
                if not hasattr(index, "default_selector"):
                    index.default_selector = [("all", [], "default")]
            options = index.get_user_settings()
            self.default_settings.index.options[index.id] = BaseSettingGroup(
                settings=options
            )

    def declare_event(self, name: str):
        if name in self._events:
            raise HookAlreadyDeclared(f"Hook {name} is already declared")
        self._events[name] = []

    def subscribe_event(self, name: str, definition: dict):
        if name not in self._events:
            raise HookNotDeclared(f"Hook {name} is not declared")
        self._events[name].append(definition)

    def get_event(self, name) -> list[dict]:
        if name not in self._events:
            raise HookNotDeclared(f"Hook {name} is not declared")
        return self._events[name]

    def make(self):
        return self


__all__ = ["ReactRuntime", "STATE"]
