from __future__ import annotations

import re

DEFAULT_APPLICATION_STATE = {"regen": False}
STATE = {
    "app": DEFAULT_APPLICATION_STATE,
}

DEFAULT_SETTING = "(默认)"
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
