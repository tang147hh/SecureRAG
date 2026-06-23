import gradio as gr
from ktem.app import BasePage
from theflow.settings import settings as flowsettings

KH_DEMO_MODE = getattr(flowsettings, "KH_DEMO_MODE", False)

if not KH_DEMO_MODE:
    PLACEHOLDER_TEXT = (
        "这是新会话的开始。\n"
        "可以先上传文件或 web URL，或直接选择已有目录开始提问。"
    )
else:
    PLACEHOLDER_TEXT = (
        "欢迎使用 Kotaemon Demo。"
        "可以先浏览预加载会话快速上手。\n"
        "查看提示部分获取更多技巧。"
    )

DEMO_CHAT_HISTORY = [
    (
        None,
        "文档未提及“111”相关的内容。请提供更具体的问题描述。",
    ),
    (
        "确认优惠券滥用后平台可以采取哪些措施？",
        None,
    ),
    (
        None,
        (
            "确认优惠券滥用后，平台可采取以下措施：\n\n"
            "- 冻结优惠券。\n"
            "- 取消异常订单。\n"
            "- 限制账号参与活动。\n"
            "- 收回已发放积分。\n"
            "- 暂停账号部分功能。\n"
            "- 对严重违规账号进行封禁。"
        ),
    ),
]

DEMO_CITATION_PANEL = """
<section class="packy-reference-panel">
  <div class="packy-reference-head">
    <h3>信息面板</h3>
  </div>
  <button class="packy-document-select" type="button">
    <span class="packy-file-icon" aria-hidden="true"></span>
    <span>bluewhale_ecommerce_ops_kb.md</span>
    <span class="packy-chevron" aria-hidden="true">⌄</span>
  </button>
  <p class="packy-hit-summary">
    异常订单进入风控队列后，可能触发人工审核、延迟发货、取消订单或限制优惠券使用。
  </p>
  <article class="packy-reference-section">
    <h4>14.2 账号安全</h4>
    <p>用户出现以下情况时，客服应引导用户进行账号安全验证：</p>
    <ul>
      <li>用户反馈账号被盗。</li>
      <li>非本人下单。</li>
      <li>收货地址被篡改。</li>
      <li>收款券或积分异常消失。</li>
      <li>登录设备异常。</li>
      <li>绑定手机号被修改。</li>
    </ul>
    <p>
      账号安全验证可采用短信验证码、人脸验证、支付验证或人工身份核验。
      客服不得索要用户支付密码、银行卡完整卡号或短信验证码。
    </p>
  </article>
  <article class="packy-reference-section">
    <h4>14.3 优惠券滥用处理</h4>
    <p>风控合规负责人识别优惠券滥用，常见优惠券滥用行为包括：</p>
    <ul>
      <li>批量注册新账号领取新人券。</li>
      <li>使用脚本抢券。</li>
      <li>转卖平台优惠券。</li>
      <li>通过虚假交易套取补贴券。</li>
      <li>恶意拆单以重复使用优惠。</li>
    </ul>
    <p>确认优惠券滥用后，平台可采取以下措施：</p>
  </article>
  <section class="packy-citation-card">
    <h4>确认优惠券滥用后，平台可采取以下措施：</h4>
    <ul>
      <li>冻结优惠券。</li>
      <li>取消异常订单。</li>
      <li>限制账号参与活动。</li>
      <li>收回已发放积分。</li>
      <li>暂停账号部分功能。</li>
      <li>对严重违规账号进行封禁。</li>
    </ul>
  </section>
</section>
"""


class ChatPanel(BasePage):
    def __init__(self, app):
        self._app = app
        self.on_building_ui()

    def on_building_ui(self):
        self.chatbot = gr.Chatbot(
            label=self._app.app_name,
            placeholder=PLACEHOLDER_TEXT,
            value=DEMO_CHAT_HISTORY,
            show_label=False,
            elem_id="main-chat-bot",
            elem_classes=["chat-surface"],
            show_copy_button=True,
            likeable=True,
            bubble_full_width=False,
        )
        self.citation_panel = gr.HTML(
            value="",
            elem_id="chat-citation-panel",
            show_label=False,
        )
        with gr.Row():
            self.text_input = gr.MultimodalTextbox(
                interactive=True,
                scale=20,
                file_count="multiple",
                placeholder=(
                    "输入消息，使用 @WebSearch，或用 @filename 标记文件"
                ),
                container=False,
                show_label=False,
                elem_id="chat-input",
                elem_classes=["chat-composer"],
            )

    def submit_msg(self, chat_input, chat_history):
        """Submit a message to the chatbot"""
        return "", chat_history + [(chat_input, None)]
