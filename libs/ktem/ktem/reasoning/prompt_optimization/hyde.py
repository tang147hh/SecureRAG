from ktem.llms.manager import llms

from kotaemon.base import BaseComponent, Document, HumanMessage, Node, SystemMessage
from kotaemon.llms import ChatLLM, PromptTemplate

DEFAULT_HYDE_PROMPT = (
    "Generate a concise hypothetical document that would answer the user's "
    "question and contain the key terms a relevant knowledge-base passage is "
    "likely to use. Preserve all named entities, product names, dates, policy "
    "names, IDs, and constraints from the question. Do not cite sources. Do not "
    "say that information is unavailable. Only output the hypothetical document "
    "in {lang}.\n\n"
    "Question: {question}\n"
    "Hypothetical document:"
)


class HyDEQuestionPipeline(BaseComponent):
    """Generate a hypothetical document for HyDE retrieval."""

    llm: ChatLLM = Node(default_callback=lambda _: llms.get_default())
    hyde_template: str = DEFAULT_HYDE_PROMPT
    lang: str = "English"

    def run(self, question: str) -> Document:  # type: ignore
        prompt_template = PromptTemplate(self.hyde_template)
        prompt = prompt_template.populate(question=question, lang=self.lang)
        messages = [
            SystemMessage(content="You are a careful retrieval query generator."),
            HumanMessage(content=prompt),
        ]
        return self.llm(messages)
