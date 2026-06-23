function run() {
  const APP_VERSION = "KH_APP_VERSION";
  const root = document.documentElement;

  document.body.classList.add("dark", "packy-enterprise-rag");
  root.dataset.theme = "dark";

  const rafThrottle = (fn) => {
    let frame = null;
    return (...args) => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = null;
        fn(...args);
      });
    };
  };

  const qs = (selector, scope = document) => scope.querySelector(selector);
  const qsa = (selector, scope = document) => Array.from(scope.querySelectorAll(selector));

  function addFavicon() {
    if (qs('link[rel="icon"]')) return;
    const favicon = document.createElement("link");
    favicon.rel = "icon";
    favicon.type = "image/svg+xml";
    favicon.href = "/favicon.ico";
    document.head.appendChild(favicon);
  }

  function buildHeader() {
    const chatTab = qs("#chat-tab");
    const tabsRoot = chatTab ? chatTab.closest(".tabs") || chatTab.parentElement : null;
    const visibleTabNavs = qsa('[role="tablist"], .tab-nav').filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    });
    const tabNav =
      (tabsRoot ? qsa('[role="tablist"], .tab-nav', tabsRoot).find((el) => el.getBoundingClientRect().width > 0) : null) ||
      visibleTabNavs[0];
    if (!tabNav || tabNav.dataset.packyHeaderReady === "true") return;

    tabNav.dataset.packyHeaderReady = "true";
    tabNav.classList.add("app-header", "glass-panel");

    qsa("button", tabNav).forEach((button) => {
      button.classList.add("nav-item");
    });

    if (!qs(".app-brand")) {
      const brand = document.createElement("div");
      brand.className = "app-brand";
      brand.innerHTML = `
        <span class="brand-icon" aria-hidden="true">
          <span></span>
        </span>
        <span class="brand-copy">
          <strong>PackyAPI</strong>
          <small>Enterprise RAG</small>
        </span>
      `;
      document.body.appendChild(brand);
    }

    if (!qs(".header-actions")) {
      const actions = document.createElement("div");
      actions.className = "header-actions";
      actions.innerHTML = `
        <button class="icon-button theme-button" type="button" aria-label="切换主题" title="切换主题">
          <span aria-hidden="true"></span>
        </button>
        <span class="system-status"><i></i>Online</span>
        <span class="version">version: ${APP_VERSION}</span>
      `;
      document.body.appendChild(actions);
    }

    qs(".theme-button")?.addEventListener("click", () => {
      document.body.classList.toggle("dark");
      root.dataset.theme = document.body.classList.contains("dark") ? "dark" : "dim";
    });
  }

  function setupPanels() {
    const shell = qs("#packy-chat-shell");
    const sidebar = qs("#conv-settings-panel");
    const chat = qs("#chat-area");
    const info = qs("#chat-info-panel");
    if (!shell || !sidebar || !chat || !info) return;

    shell.classList.add("app-body");
    sidebar.classList.add("sidebar", "panel");
    chat.classList.add("chat-panel", "panel");
    info.classList.add("info-panel", "panel");

    const oldChatExpand = qs("#chat-expand-button");
    const oldInfoExpand = qs("#info-expand-button");
    oldChatExpand?.classList.add("panel-toggle-source");
    oldInfoExpand?.classList.add("panel-toggle-source");

    function ensureOverlay() {
      let overlay = qs(".panel-backdrop");
      if (!overlay) {
        overlay = document.createElement("button");
        overlay.className = "panel-backdrop";
        overlay.type = "button";
        overlay.setAttribute("aria-label", "关闭侧栏");
        document.body.appendChild(overlay);
        overlay.addEventListener("click", () => {
          sidebar.classList.remove("is-open");
          info.classList.remove("is-open");
          document.body.classList.remove("has-panel-open");
        });
      }
      return overlay;
    }

    function setPanelOpen(panel, open) {
      panel.classList.toggle("is-open", open);
      document.body.classList.toggle(
        "has-panel-open",
        sidebar.classList.contains("is-open") || info.classList.contains("is-open")
      );
      ensureOverlay();
    }

    function makeFloatingToggle(id, className, label, onClick) {
      let button = qs(`#${id}`);
      if (!button) {
        button = document.createElement("button");
        button.id = id;
        button.type = "button";
        chat.appendChild(button);
      }
      button.className = `floating-panel-toggle ${className}`;
      button.type = "button";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
      button.innerHTML = "<span></span>";
      button.onclick = onClick;
    }

    makeFloatingToggle("mobile-sidebar-toggle", "left", "打开会话栏", () => {
      setPanelOpen(sidebar, !sidebar.classList.contains("is-open"));
    });
    makeFloatingToggle("mobile-info-toggle", "right", "打开信息面板", () => {
      setPanelOpen(info, !info.classList.contains("is-open"));
    });

    globalThis.toggleChatColumn = () => {
      sidebar.classList.toggle("is-collapsed");
      window.dispatchEvent(new Event("resize"));
    };
    globalThis.toggleInfoColumn = () => {
      info.classList.toggle("is-collapsed");
      window.dispatchEvent(new Event("resize"));
    };
  }

  function enhanceControls() {
    const convDropdown = qs("#conversation-dropdown input");
    if (convDropdown) convDropdown.placeholder = "搜索或选择会话";

    qsa("#suggest-chat-checkbox, #use-mindmap-checkbox, #is-public-checkbox").forEach((box) => {
      if (box.dataset.packySwitchReady === "true") return;
      const label = qs("label", box);
      const checkbox = qs('input[type="checkbox"]', box);
      if (!label || !checkbox) return;
      box.dataset.packySwitchReady = "true";
      box.classList.add("switch-field");
      label.classList.add("switch-label");
      if (!qs(".switch-track", label)) {
        const track = document.createElement("span");
        track.className = "switch-track";
        track.setAttribute("aria-hidden", "true");
        label.prepend(track);
      }
    });

    qsa("#conversation-control-expand, #quick-upload-expand, #chat-settings-expand, #info-expand").forEach((item) => {
      item.classList.add("section-card");
    });
  }

  function getChatTextarea() {
    return qs("#chat-input textarea");
  }

  function resizeComposer(textarea = getChatTextarea()) {
    const inputRoot = qs("#chat-input");
    if (!textarea) return;
    textarea.style.height = "auto";
    const minHeight = 48;
    const maxHeight = Math.max(128, Math.min(180, window.innerHeight * 0.24));
    const height = Math.min(Math.max(textarea.scrollHeight, minHeight), maxHeight);
    textarea.style.height = `${height}px`;
    textarea.style.overflowY = textarea.scrollHeight > height ? "auto" : "hidden";
    inputRoot?.classList.toggle("has-value", Boolean(textarea.value.trim()));
  }

  function setupComposer() {
    const inputRoot = qs("#chat-input");
    const textarea = getChatTextarea();
    if (!inputRoot || !textarea) return;

    inputRoot.classList.add("composer");
    if (textarea.dataset.packyComposerReady !== "true") {
      textarea.dataset.packyComposerReady = "true";
      textarea.rows = 1;
      textarea.autocomplete = "off";
      textarea.setAttribute("aria-label", "输入聊天消息");
      textarea.addEventListener("input", () => resizeComposer(textarea));
      textarea.addEventListener("focus", () => inputRoot.classList.add("is-focused"));
      textarea.addEventListener("blur", () => inputRoot.classList.remove("is-focused"));
    }
    resizeComposer(textarea);
  }

  function getChatScrollParent() {
    const chatRoot = qs("#main-chat-bot");
    if (!chatRoot) return null;
    const candidates = [chatRoot, ...qsa("*", chatRoot)];
    return candidates.find((node) => node.scrollHeight > node.clientHeight + 8) || chatRoot;
  }

  function isNearBottom(node) {
    if (!node) return true;
    return node.scrollHeight - node.scrollTop - node.clientHeight < 140;
  }

  function markMessages() {
    const chatRoot = qs("#main-chat-bot");
    if (!chatRoot) return;
    chatRoot.classList.add("message-list");
    qsa(".message-row", chatRoot).forEach((row) => row.classList.add("message-row-enhanced"));
    const botRows = qsa(".message-row.bot-row", chatRoot);
    botRows.forEach((row) => row.classList.remove("kh-chat-streaming"));
    if (document.body.classList.contains("kh-chat-busy") && botRows.length) {
      botRows[botRows.length - 1].classList.add("kh-chat-streaming");
    }
  }

  window.khChatSetBusy = (busy) => {
    document.body.classList.toggle("kh-chat-busy", Boolean(busy));
    qs("#chat-area")?.classList.toggle("is-chat-submitting", Boolean(busy));
    qs("#chat-input")?.classList.toggle("is-submitting", Boolean(busy));
    markMessages();
  };
  globalThis.khChatSetBusy = window.khChatSetBusy;

  window.khChatFocusComposer = () => {
    setupComposer();
    const textarea = getChatTextarea();
    if (!textarea) return;
    requestAnimationFrame(() => textarea.focus({ preventScroll: true }));
  };
  globalThis.khChatFocusComposer = window.khChatFocusComposer;

  globalThis.setStorage = (key, value) => localStorage.setItem(key, value);
  globalThis.getStorage = (key, value) => localStorage.getItem(key) || value;
  globalThis.removeFromStorage = (key) => localStorage.removeItem(key);

  globalThis.clpseFn = (id) => {
    const button = qs(`#clpse-btn-${id}`);
    if (!button) return;
    button.classList.toggle("clpse-active");
    const content = button.nextElementSibling;
    if (content) content.hidden = !content.hidden;
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  globalThis.scrollToCitation = async (event) => {
    event.preventDefault();
    await sleep(100);
    const citationId = event.currentTarget?.getAttribute("id") || event.target?.getAttribute("id");
    const citation = citationId ? qs(`mark[id="${CSS.escape(citationId)}"]`) : null;
    if (!citation) return;
    const modal = qs("#pdf-modal");
    if (modal && modal.style.display === "block") {
      const detail = citation.closest("details");
      qs(".pdf-link", detail || document)?.click();
      return;
    }
    citation.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  globalThis.fullTextSearch = () => {
    const lastBotMessage = qsa("div#main-chat-bot div.message-row.bot-row").at(-1);
    if (!lastBotMessage || lastBotMessage.classList.contains("text_selection")) return;
    lastBotMessage.classList.add("text_selection");

    const evidences = qsa("#html-info-panel details.evidence div.evidence-content");
    if (!evidences.length || typeof MiniSearch === "undefined") return;

    const segmenter = new Intl.Segmenter("en", { granularity: "sentence" });
    const allSegments = [];
    evidences.forEach((evidence) => {
      if (evidence.closest("details") && !evidence.closest("details").open) return;
      if (qs("div.markmap", evidence)) return;
      const content = evidence.textContent.replace(/[\r\n]+/g, " ");
      for (const sentence of segmenter.segment(content)) {
        const text = sentence.segment.trim();
        if (text) allSegments.push({ id: allSegments.length, text });
      }
    });
    if (!allSegments.length) return;

    const miniSearch = new MiniSearch({
      fields: ["text"],
      storeFields: ["text"],
    });
    miniSearch.addAll(allSegments);

    lastBotMessage.addEventListener("mouseup", () => {
      const selection = window.getSelection().toString().trim();
      if (!selection) return;
      const result = miniSearch.search(selection)[0];
      if (!result?.text) return;

      evidences.forEach((evidence) => {
        qsa("mark", evidence).forEach((mark) => {
          mark.replaceWith(document.createTextNode(mark.innerText));
        });
      });

      for (const evidence of evidences) {
        const paragraphs = qsa("p, li", evidence);
        const target = paragraphs.find((node) =>
          node.textContent.replace(/[\r\n]+/g, " ").includes(result.text)
        );
        if (!target) continue;
        target.innerHTML = target.textContent
          .replace(/[\r\n]+/g, " ")
          .replace(result.text, `<mark>${result.text}</mark>`);
        target.scrollIntoView({ behavior: "smooth", block: "center" });
        break;
      }
    });
  };

  globalThis.spawnDocument = (content, options = {}) => {
    const opt = { window: "", closeChild: true, childId: "_blank", ...options };
    if (!content || typeof content.toString !== "function") return null;
    const child = window.open("", opt.childId, opt.window);
    if (!child) return null;
    child.document.write(content.toString());
    if (opt.closeChild) child.document.close();
    return child;
  };

  globalThis.fillChatInput = (event) => {
    const textarea = getChatTextarea();
    if (!textarea) return;
    textarea.value = `Explain ${event.target.textContent}`;
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();
  };

  function boot() {
    addFavicon();
    buildHeader();
    setupPanels();
    enhanceControls();
    setupComposer();
    markMessages();
  }

  boot();

  const observe = rafThrottle(() => {
    const scrollParent = getChatScrollParent();
    const shouldStick = isNearBottom(scrollParent);
    boot();
    if (shouldStick && scrollParent) scrollParent.scrollTop = scrollParent.scrollHeight;
  });

  new MutationObserver(observe).observe(document.body, {
    childList: true,
    subtree: true,
    characterData: true,
    attributes: true,
    attributeFilter: ["class", "hidden", "aria-selected"],
  });
}
