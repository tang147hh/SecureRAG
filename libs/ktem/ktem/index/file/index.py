import uuid
from datetime import datetime
from typing import Any, Optional, Type

from ktem.components import filestorage_path, get_docstore, get_vectorstore
from ktem.db.engine import engine
from ktem.index.base import BaseIndex
from ktem.permissions import permission_service
from sqlalchemy import JSON, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.mutable import MutableDict
from theflow.settings import settings as flowsettings
from theflow.utils.modules import import_dotted_string
from tzlocal import get_localzone

from kotaemon.storages import BaseDocumentStore, BaseVectorStore

from .base import BaseFileIndexIndexing, BaseFileIndexRetriever


def generate_uuid():
    return str(uuid.uuid4())


class FileIndex(BaseIndex):
    """
    File index to store and allow retrieval of files

    The file index stores files in a local folder and index them for retrieval.
    This file index provides the following infrastructure to support the indexing:
        - SQL table Source: store the list of files that are indexed by the system
        - Vector store: contain the embedding of segments of the files
        - Document store: contain the text of segments of the files. Each text stored
        in this document store is associated with a vector in the vector store.
        - SQL table Index: store the relationship between (1) the source and the
        docstore, and (2) the source and the vector store.
    """

    def __init__(self, app, id: int, name: str, config: dict):
        super().__init__(app, id, name, config)

        self._indexing_pipeline_cls: Type[BaseFileIndexIndexing]
        self._retriever_pipeline_cls: list[Type[BaseFileIndexRetriever]]
        self._selector_ui_cls: Type
        self._selector_ui: Any = None
        self._index_ui_cls: Type
        self._index_ui: Any = None

        self._default_settings: dict[str, dict] = {}
        self._setting_mappings: dict[str, dict] = {}

    def _setup_resources(self):
        """Setup resources for the file index

        The resources include:
            - Database table
            - Vector store
            - Document store
            - File storage path
        """
        Base = declarative_base()

        if self.config.get("private", False):
            Source = type(
                "Source",
                (Base,),
                {
                    "__tablename__": f"index__{self.id}__source",
                    "__table_args__": (
                        UniqueConstraint("name", "user", name="_name_user_uc"),
                    ),
                    "id": Column(
                        String,
                        primary_key=True,
                        default=lambda: str(uuid.uuid4()),
                        unique=True,
                    ),
                    "name": Column(String),
                    "path": Column(String),
                    "size": Column(Integer, default=0),
                    "date_created": Column(
                        DateTime(timezone=True), default=datetime.now(get_localzone())
                    ),
                    "user": Column(String, default=""),
                    "note": Column(
                        MutableDict.as_mutable(JSON),  # type: ignore
                        default={},
                    ),
                },
            )
        else:
            Source = type(
                "Source",
                (Base,),
                {
                    "__tablename__": f"index__{self.id}__source",
                    "id": Column(
                        String,
                        primary_key=True,
                        default=lambda: str(uuid.uuid4()),
                        unique=True,
                    ),
                    "name": Column(String, unique=True),
                    "path": Column(String),
                    "size": Column(Integer, default=0),
                    "date_created": Column(
                        DateTime(timezone=True), default=datetime.now(get_localzone())
                    ),
                    "user": Column(String, default=""),
                    "note": Column(
                        MutableDict.as_mutable(JSON),  # type: ignore
                        default={},
                    ),
                },
            )
        Index = type(
            "IndexTable",
            (Base,),
            {
                "__tablename__": f"index__{self.id}__index",
                "id": Column(Integer, primary_key=True, autoincrement=True),
                "source_id": Column(String),
                "target_id": Column(String),
                "relation_type": Column(String),
                "user": Column(String, default=""),
            },
        )
        FileGroup = type(
            "FileGroupTable",
            (Base,),
            {
                "__tablename__": f"index__{self.id}__group",
                "__table_args__": (
                    UniqueConstraint("name", "user", name="_name_user_uc"),
                ),
                "id": Column(
                    String,
                    primary_key=True,
                    default=lambda: str(uuid.uuid4()),
                    unique=True,
                ),
                "date_created": Column(
                    DateTime(timezone=True), default=datetime.now(get_localzone())
                ),
                "name": Column(String),
                "user": Column(String, default=""),
                "data": Column(
                    MutableDict.as_mutable(JSON),  # type: ignore
                    default={"files": []},
                ),
            },
        )

        self._vs: BaseVectorStore = get_vectorstore(f"index_{self.id}")
        self._docstore: BaseDocumentStore = get_docstore(f"index_{self.id}")
        self._fs_path = filestorage_path / f"index_{self.id}"
        self._resources = {
            "Source": Source,
            "Index": Index,
            "FileGroup": FileGroup,
            "VectorStore": self._vs,
            "DocStore": self._docstore,
            "FileStoragePath": self._fs_path,
            "PermissionService": permission_service,
        }

    def _setup_indexing_cls(self):
        """Retrieve the indexing class for the file index

        There is only one indexing class.

        The indexing class will is retrieved from the following order. Stop at the
        first order found:
            - `FILE_INDEX_PIPELINE` in self.config
            - `FILE_INDEX_{id}_PIPELINE` in the flowsettings
            - `FILE_INDEX_PIPELINE` in the flowsettings
            - The default .pipelines.IndexDocumentPipeline
        """
        if "FILE_INDEX_PIPELINE" in self.config:
            self._indexing_pipeline_cls = import_dotted_string(
                self.config["FILE_INDEX_PIPELINE"], safe=False
            )
            return

        if hasattr(flowsettings, f"FILE_INDEX_{self.id}_PIPELINE"):
            self._indexing_pipeline_cls = import_dotted_string(
                getattr(flowsettings, f"FILE_INDEX_{self.id}_PIPELINE"), safe=False
            )
            return

        if self.config.get("GRAPH_RAG_ENABLED"):
            from .graph.product import ProductGraphIndexingPipeline

            self._indexing_pipeline_cls = ProductGraphIndexingPipeline
            return

        if hasattr(flowsettings, "FILE_INDEX_PIPELINE"):
            self._indexing_pipeline_cls = import_dotted_string(
                getattr(flowsettings, "FILE_INDEX_PIPELINE"), safe=False
            )
            return

        from .pipelines import IndexDocumentPipeline

        self._indexing_pipeline_cls = IndexDocumentPipeline

    def _setup_retriever_cls(self):
        """Retrieve the retriever classes for the file index

        There can be multiple retriever classes.

        The retriever classes will is retrieved from the following order. Stop at the
        first order found:
            - `FILE_INDEX_RETRIEVER_PIPELINES` in self.config
            - `FILE_INDEX_{id}_RETRIEVER_PIPELINES` in the flowsettings
            - `FILE_INDEX_RETRIEVER_PIPELINES` in the flowsettings
            - The default .pipelines.DocumentRetrievalPipeline
        """
        if "FILE_INDEX_RETRIEVER_PIPELINES" in self.config:
            self._retriever_pipeline_cls = [
                import_dotted_string(each, safe=False)
                for each in self.config["FILE_INDEX_RETRIEVER_PIPELINES"]
            ]
            return

        if hasattr(flowsettings, f"FILE_INDEX_{self.id}_RETRIEVER_PIPELINES"):
            self._retriever_pipeline_cls = [
                import_dotted_string(each, safe=False)
                for each in getattr(
                    flowsettings, f"FILE_INDEX_{self.id}_RETRIEVER_PIPELINES"
                )
            ]
            return

        if hasattr(flowsettings, "FILE_INDEX_RETRIEVER_PIPELINES"):
            self._retriever_pipeline_cls = [
                import_dotted_string(each, safe=False)
                for each in getattr(flowsettings, "FILE_INDEX_RETRIEVER_PIPELINES")
            ]
            return

        from .pipelines import DocumentRetrievalPipeline

        self._retriever_pipeline_cls = [DocumentRetrievalPipeline]

    def _setup_file_selector_ui_cls(self):
        """Retrieve the file selector UI for the file index

        There can be multiple retriever classes.

        The retriever classes will is retrieved from the following order. Stop at the
        first order found:
            - `FILE_INDEX_SELECTOR_UI` in self.config
            - `FILE_INDEX_{id}_SELECTOR_UI` in the flowsettings
            - `FILE_INDEX_SELECTOR_UI` in the flowsettings
            - None. ReactRuntime injects a headless selector.
        """
        if "FILE_INDEX_SELECTOR_UI" in self.config:
            self._selector_ui_cls = import_dotted_string(
                self.config["FILE_INDEX_SELECTOR_UI"], safe=False
            )
            return

        if hasattr(flowsettings, f"FILE_INDEX_{self.id}_SELECTOR_UI"):
            self._selector_ui_cls = import_dotted_string(
                getattr(flowsettings, f"FILE_INDEX_{self.id}_SELECTOR_UI"),
                safe=False,
            )
            return

        if hasattr(flowsettings, "FILE_INDEX_SELECTOR_UI"):
            self._selector_ui_cls = import_dotted_string(
                getattr(flowsettings, "FILE_INDEX_SELECTOR_UI"), safe=False
            )
            return

        self._selector_ui_cls = None

    def _setup_file_index_ui_cls(self):
        """Retrieve the Index UI class

        There can be multiple retriever classes.

        The retriever classes will is retrieved from the following order. Stop at the
        first order found:
            - `FILE_INDEX_UI` in self.config
            - `FILE_INDEX_{id}_UI` in the flowsettings
            - `FILE_INDEX_UI` in the flowsettings
            - None. The React frontend manages files through REST APIs.
        """
        if "FILE_INDEX_UI" in self.config:
            self._index_ui_cls = import_dotted_string(
                self.config["FILE_INDEX_UI"], safe=False
            )
            return

        if hasattr(flowsettings, f"FILE_INDEX_{self.id}_UI"):
            self._index_ui_cls = import_dotted_string(
                getattr(flowsettings, f"FILE_INDEX_{self.id}_UI"),
                safe=False,
            )
            return

        if hasattr(flowsettings, "FILE_INDEX_UI"):
            self._index_ui_cls = import_dotted_string(
                getattr(flowsettings, "FILE_INDEX_UI"), safe=False
            )
            return

        self._index_ui_cls = None

    def on_create(self):
        """Create the index for the first time

        For the file index, this will:
            1. Postprocess the config
            2. Create the index and the source table if not already exists
            3. Create the vectorstore
            4. Create the docstore
        """
        # default user's value
        config = {}
        for key, value in self.get_admin_settings().items():
            config[key] = value["value"]

        # user's modification
        config.update(self.config)

        self.config = config

        # create the resources
        self._setup_resources()
        self._resources["Source"].metadata.create_all(engine)  # type: ignore
        self._resources["Index"].metadata.create_all(engine)  # type: ignore
        self._resources["FileGroup"].metadata.create_all(engine)  # type: ignore
        self._fs_path.mkdir(parents=True, exist_ok=True)

    def on_delete(self):
        """Clean up the index when the user delete it"""
        import shutil

        self._setup_resources()
        self._resources["Source"].__table__.drop(engine)  # type: ignore
        self._resources["Index"].__table__.drop(engine)  # type: ignore
        self._resources["FileGroup"].__table__.drop(engine)  # type: ignore
        self._vs.drop()
        self._docstore.drop()
        shutil.rmtree(self._fs_path)

    def on_start(self):
        """Setup the classes and hooks"""
        if "GRAPH_RAG_ENABLED" not in self.config:
            self.config["GRAPH_RAG_ENABLED"] = True
        if "GRAPH_RAG_PROVIDER" not in self.config:
            self.config["GRAPH_RAG_PROVIDER"] = getattr(
                flowsettings, "GRAPH_RAG_PROVIDER", "lightrag"
            )
        self._setup_resources()
        self._setup_indexing_cls()
        self._setup_retriever_cls()
        self._setup_file_index_ui_cls()
        self._setup_file_selector_ui_cls()

    def get_selector_component_ui(self):
        if self._selector_ui_cls is None:
            return self._selector_ui
        if self._selector_ui is None:
            self._selector_ui = self._selector_ui_cls(self._app, self)
        return self._selector_ui

    def get_index_page_ui(self):
        if self._index_ui_cls is None:
            return self._index_ui
        if self._index_ui is None:
            self._index_ui = self._index_ui_cls(self._app, self)
        return self._index_ui

    def get_user_settings(self):
        if self._default_settings:
            return self._default_settings

        settings = {}
        settings.update(self._indexing_pipeline_cls.get_user_settings())
        for cls in self._retriever_pipeline_cls:
            settings.update(cls.get_user_settings())

        self._default_settings = settings
        return settings

    @classmethod
    def get_admin_settings(cls):
        from ktem.embeddings.manager import embedding_models_manager

        embedding_default = "default"
        embedding_choices = list(embedding_models_manager.options().keys())

        return {
            "embedding": {
                "name": "Embedding 模型",
                "value": embedding_default,
                "component": "dropdown",
                "choices": embedding_choices,
                "info": "文档入库和检索使用的 Embedding 模型。",
            },
            "supported_file_types": {
                "name": "支持的文件类型",
                "value": ".pdf, .txt",
                "component": "text",
                "info": "可上传的文件类型，用逗号分隔。",
            },
            "max_file_size": {
                "name": "最大文件大小 (MB)",
                "value": 1000,
                "component": "number",
                "info": "文件最大大小。设为 0 可禁用限制。",
            },
            "max_number_of_files": {
                "name": "最大文件数",
                "value": 0,
                "component": "number",
                "info": (
                    "系统中可入库的文件总数。"
                    "设为 0 可禁用限制。"
                ),
            },
            "private": {
                "name": "设为私有",
                "value": False,
                "component": "radio",
                "choices": [("是", True), ("否", False)],
                "info": "如果设为私有，文件将不能跨用户访问。",
            },
        }

    def get_indexing_pipeline(self, settings, user_id) -> BaseFileIndexIndexing:
        """Define the interface of the indexing pipeline"""

        prefix = f"index.options.{self.id}."
        stripped_settings = {}
        for key, value in settings.items():
            if key.startswith(prefix):
                stripped_settings[key[len(prefix) :]] = value

        obj = self._indexing_pipeline_cls.get_pipeline(stripped_settings, self.config)
        obj.Source = self._resources["Source"]
        obj.Index = self._resources["Index"]
        obj.VS = self._vs
        obj.DS = self._docstore
        obj.FSPath = self._fs_path
        obj.PermissionService = self._resources["PermissionService"]
        obj.index_id = self.id
        obj.user_id = user_id
        obj.private = self.config.get("private", False)
        obj.chunk_size = stripped_settings.get("chunk_size", self.config.get("chunk_size", 0))
        obj.chunk_overlap = stripped_settings.get(
            "chunk_overlap",
            self.config.get("chunk_overlap", 0),
        )

        return obj

    def get_retriever_pipelines(
        self, settings: dict, user_id: int, selected: Any = None
    ) -> list["BaseFileIndexRetriever"]:
        # retrieval settings
        prefix = f"index.options.{self.id}."
        stripped_settings = {}
        for key, value in settings.items():
            if key.startswith(prefix):
                stripped_settings[key[len(prefix) :]] = value

        # transform selected id
        if hasattr(self._selector_ui, "get_selected_ids_for_user"):
            selected_ids: Optional[list[str]] = self._selector_ui.get_selected_ids_for_user(
                selected, user_id
            )
        else:
            selected_ids = self._selector_ui.get_selected_ids(selected)
        selected_ids = self._resources["PermissionService"].filter_source_ids(
            self, selected_ids or [], user_id
        )
        print(f"File index {self.id} selected source ids: {selected_ids}")

        retrievers = []
        for cls in self._retriever_pipeline_cls:
            obj = cls.get_pipeline(stripped_settings, self.config, selected_ids)
            if obj is None:
                continue
            obj.Source = self._resources["Source"]
            obj.Index = self._resources["Index"]
            obj.VS = self._vs
            obj.DS = self._docstore
            obj.FSPath = self._fs_path
            obj.PermissionService = self._resources["PermissionService"]
            obj.index_id = self.id
            obj.private = self.config.get("private", False)
            obj.user_id = user_id
            retrievers.append(obj)

        if bool(stripped_settings.get("graph_enabled", False)) and selected_ids:
            from .graph.product import ProductGraphRetriever, graph_provider_name

            retrievers.append(
                ProductGraphRetriever(
                    index_id=self.id,
                    Source=self._resources["Source"],
                    Index=self._resources["Index"],
                    VS=self._vs,
                    DS=self._docstore,
                    FSPath=self._fs_path,
                    file_ids=selected_ids,
                    user_id=str(user_id),
                    PermissionService=self._resources["PermissionService"],
                    private=self.config.get("private", False),
                    provider=graph_provider_name(self.config),
                    search_type=stripped_settings.get("graph_search_type", "local"),
                )
            )

        return retrievers
