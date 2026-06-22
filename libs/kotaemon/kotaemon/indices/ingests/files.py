"""文件摄取入口。

本模块负责把本地文件或网页类输入交给对应的 Reader 解析成 Document，
再通过 splitter 切分为适合索引/检索的文本节点。整体流程是：

1. 根据文件扩展名选择默认 Reader，必要时允许调用方覆盖。
2. 对 PDF 这类解析方式较多的格式，根据 `pdf_mode` 动态替换 Reader。
3. 使用 DirectoryReader 统一调度具体 Reader，得到原始 Document 列表。
4. 将 Document 切分成较小节点，再交给可选的 doc_parsers 做二次处理。
"""

from pathlib import Path
from typing import Type

from decouple import config
from llama_index.core.readers.base import BaseReader
from llama_index.readers.file import PDFReader
from theflow.settings import settings as flowsettings

from kotaemon.base import BaseComponent, Document, Param
from kotaemon.indices.extractors import BaseDocParser
from kotaemon.indices.splitters import BaseSplitter, TokenSplitter
from kotaemon.loaders import (
    AdobeReader,
    AzureAIDocumentIntelligenceLoader,
    DirectoryReader,
    DoclingReader,
    HtmlReader,
    MathpixPDFReader,
    MhtmlReader,
    OCRReader,
    PaddleOCRVLReader,
    PandasExcelReader,
    PDFThumbnailReader,
    PPStructureV3Reader,
    TxtReader,
    UnstructuredReader,
    WebReader,
)

# 下面这些 Reader 是模块级单例/共享实例。
#
# 大多数 Reader 本身不保存单个文件的解析状态，做成共享实例可以避免在每次
# ingest 时重复初始化。对于需要外部服务或较重依赖的 Reader，也便于在这里统一
# 注入环境变量、缓存目录和 VLM endpoint 等配置。
web_reader = WebReader()
unstructured = UnstructuredReader()
adobe_reader = AdobeReader()
azure_reader = AzureAIDocumentIntelligenceLoader(
    # Azure Document Intelligence 的服务地址和凭证从环境变量读取；
    # 没有配置时传空字符串，后续只有真正选用该 Reader 时才会触发服务调用。
    endpoint=str(config("AZURE_DI_ENDPOINT", default="")),
    credential=str(config("AZURE_DI_CREDENTIAL", default="")),
    # Markdown/中间产物缓存目录由全局 settings 控制，未配置则不启用缓存目录。
    cache_dir=getattr(flowsettings, "KH_MARKDOWN_OUTPUT_DIR", None),
)
docling_reader = DoclingReader()
adobe_reader.vlm_endpoint = (
    azure_reader.vlm_endpoint
) = docling_reader.vlm_endpoint = getattr(flowsettings, "KH_VLM_ENDPOINT", "")

# Paddle 系列 Reader 依赖运行设备配置，默认走 GPU；如果部署环境没有 GPU，
# 可以通过 PADDLE_DEVICE=cpu 切换到 CPU。
paddle_device = str(config("PADDLE_DEVICE", default="gpu"))
paddle_struct_reader = PPStructureV3Reader(device=paddle_device)
paddle_vl_reader = PaddleOCRVLReader(device=paddle_device)


# 默认文件后缀到 Reader 的映射。
#
# DirectoryReader 会根据输入文件的扩展名查表，然后调用对应 Reader。
# 这里的 key 统一使用小写后缀，并且包含前导点；新增格式时也应保持一致。
KH_DEFAULT_FILE_EXTRACTORS: dict[str, BaseReader] = {
    # Excel：xlsx 使用 PandasExcelReader，通常能更稳定地保留表格行列结构。
    ".xlsx": PandasExcelReader(),
    # Office 旧格式/复杂格式：交给 unstructured 做通用解析。
    ".docx": unstructured,
    ".pptx": unstructured,
    ".xls": unstructured,
    ".doc": unstructured,
    # HTML/MHTML：分别使用专门 Reader，避免按普通文本粗暴读取。
    ".html": HtmlReader(),
    ".mhtml": MhtmlReader(),
    # 图片：默认交给 unstructured，它会根据安装能力尝试 OCR/版面解析。
    ".png": unstructured,
    ".jpeg": unstructured,
    ".jpg": unstructured,
    ".tiff": unstructured,
    ".tif": unstructured,
    # PDF 默认用 PDFThumbnailReader，后续会被 pdf_mode 分支覆盖。
    # 这里保留默认值，是为了让全局 extractor 表本身也具备完整映射。
    ".pdf": PDFThumbnailReader(),
    # 纯文本和 Markdown：按文本读取，后续再由 splitter 处理分块。
    ".txt": TxtReader(),
    ".md": TxtReader(),
}


class DocumentIngestor(BaseComponent):
    """将常见文件类型摄取为可索引的 Document 节点。

    支持的常见文档类型：
        - pdf
        - xlsx, xls
        - docx, doc

    参数说明：
        pdf_mode: PDF 解析模式。
            - normal: 使用 llama-index 的 PDFReader 解析文本层。
            - mathpix: 使用 MathpixPDFReader，适合公式/论文类 PDF。
            - ocr: 使用 OCRReader，适合扫描件或没有文本层的 PDF。
            - multimodal: 使用 AdobeReader，适合需要多模态能力的 PDF。
            - 其他值: 当前逻辑会回退到 MathpixPDFReader。
        doc_parsers: 文档切分后的二次解析器列表，例如补充 metadata、
            做清洗、过滤或结构化增强。
        text_splitter: 将原始 Document 切成文本节点的 splitter。
        override_file_extractors: 按文件扩展名覆盖默认 Reader 的字典。
            默认映射见 `KH_DEFAULT_FILE_EXTRACTORS`。
    """

    # PDF 的解析方式由部署场景决定：普通文本 PDF 用 normal 更轻量；
    # 扫描件用 ocr；需要公式识别时用 mathpix；需要 VLM 能力时用 multimodal。
    pdf_mode: str = "normal"  # "normal", "mathpix", "ocr", "multimodal"
    # Param(default_callback=...) 确保每个组件实例拿到独立 list，
    # 避免可变默认值在多个实例之间共享。
    doc_parsers: list[BaseDocParser] = Param(default_callback=lambda _: [])
    # 默认按 token 数切块：chunk_overlap 用于保留上下文，避免跨块语义断裂；
    # separator/backup_separators 则控制优先按段落、换行、句号、空格等边界切分。
    text_splitter: BaseSplitter = TokenSplitter.withx(
        chunk_size=1024,
        chunk_overlap=256,
        separator="\n\n",
        backup_separators=["\n", ".", " ", "\u200B"],
    )
    # 调用方可通过这个字段为某些后缀指定自定义 Reader 类。
    # 注意这里保存的是 Reader 类型，实际实例化发生在 `_get_reader` 中。
    override_file_extractors: dict[str, Type[BaseReader]] = {}

    def _get_reader(self, input_files: list[str | Path]):
        """根据文件扩展名和当前配置组装 DirectoryReader。

        DirectoryReader 是统一入口，真正的解析工作会委派给 file_extractor
        字典中的具体 Reader。这里返回的是已经绑定 input_files 和
        file_extractor 的 DirectoryReader 实例，调用它即可开始读取。
        """
        # 先复制一份默认映射，避免 override 直接污染模块级全局配置。
        file_extractors: dict[str, BaseReader] = {
            ext: reader for ext, reader in KH_DEFAULT_FILE_EXTRACTORS.items()
        }

        # 应用调用方传入的覆盖配置。
        # 约定 override_file_extractors 的 value 是 Reader 类，因此这里实例化。
        for ext, cls in self.override_file_extractors.items():
            file_extractors[ext] = cls()

        # PDF 有多种解析路径，单独根据 pdf_mode 覆盖默认的 .pdf Reader。
        # normal 依赖 PDF 文本层，最快也最轻；ocr 适合扫描件；multimodal 走
        # AdobeReader；未命中显式模式时使用 Mathpix，保持旧逻辑的兼容行为。
        if self.pdf_mode == "normal":
            file_extractors[".pdf"] = PDFReader()
        elif self.pdf_mode == "ocr":
            file_extractors[".pdf"] = OCRReader()
        elif self.pdf_mode == "multimodal":
            file_extractors[".pdf"] = AdobeReader()
        else:
            file_extractors[".pdf"] = MathpixPDFReader()

        main_reader = DirectoryReader(
            input_files=input_files,
            file_extractor=file_extractors,
        )

        return main_reader

    def run(self, file_paths: list[str | Path] | str | Path) -> list[Document]:
        """读取文件路径并返回可用于索引的 Document 节点列表。

        参数：
            file_paths: 单个文件路径，或文件路径列表。元素可以是 str 或 Path。

        返回：
            经过 Reader 解析、splitter 切分，并可选经过 doc_parsers 处理后的
            Document 列表。这里返回的 Document 通常已经是较小的索引节点。
        """
        # 统一归一化为列表，便于后续 DirectoryReader 批量处理。
        if not isinstance(file_paths, list):
            file_paths = [file_paths]

        # 读取阶段：不同后缀会走不同 Reader，输出是原始 Document 列表。
        documents = self._get_reader(input_files=file_paths)()
        print(f"Read {len(file_paths)} files into {len(documents)} documents.")

        # 切分阶段：把较大的 Document 拆成检索粒度更合适的 nodes。
        nodes = self.text_splitter(documents)
        print(f"Transform {len(documents)} documents into {len(nodes)} nodes.")

        # 记录本次摄取生成的节点数量，供上层 UI/日志/监控读取。
        self.log_progress(".num_docs", num_docs=len(nodes))

        # 二次解析阶段：按顺序执行 doc_parsers。
        # 每个 parser 都接收当前 nodes 并返回新的 nodes，因此 parser 顺序会影响结果。
        if self.doc_parsers:
            for parser in self.doc_parsers:
                nodes = parser(nodes)

        return nodes
