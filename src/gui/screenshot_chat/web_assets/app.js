const state = { data: null, atBottom: true, hasUnseenBottom: false, lastMessageIds: "", activeSessionId: "", localSendPending: false, offline: false, railCollapsed: false };
let scrollTimers = [];
let scrollFrames = [];
const renderedMessages = new Map();
const runtimeConfig = window.__SCREENSHOT_CHAT_CONFIG__ || {};

const el = {
  app: document.getElementById("app"),
  title: document.getElementById("title"),
  closeBtn: document.getElementById("closeBtn"),
  conversationRail: document.getElementById("conversationRail"),
  railCollapseBtn: document.getElementById("railCollapseBtn"),
  railSessions: document.getElementById("railSessions"),
  newChatBtn: document.getElementById("newChatBtn"),
  historyBtn: document.getElementById("historyBtn"),
  modeToggle: document.getElementById("modeToggle"),
  modelBtn: document.getElementById("modelBtn"),
  autoReadBtn: document.getElementById("autoReadBtn"),
  runtimeStatus: document.getElementById("runtimeStatus"),
  runtimeLabel: document.getElementById("runtimeLabel"),
  runtimeBanner: document.getElementById("runtimeBanner"),
  menus: document.getElementById("menus"),
  messages: document.getElementById("messages"),
  jumpBottom: document.getElementById("jumpBottom"),
  status: document.getElementById("status"),
  attachments: document.getElementById("attachments"),
  addBtn: document.getElementById("addBtn"),
  input: document.getElementById("input"),
  actionBtn: document.getElementById("actionBtn"),
};

function shellApi() {
  return window.pywebview && window.pywebview.api;
}

async function fetchJson(path, options = {}) {
  const response = await fetch(`${runtimeConfig.serverUrl}${path}`, {
    headers: { "Content-Type": "application/json", "X-STC-Token": runtimeConfig.token || "" },
    ...options,
  });
  if (response.ok === false) {
    const error = new Error(`Screenshot Chat server returned ${response.status}`);
    error.httpStatus = response.status;
    throw error;
  }
  return response.json();
}

const transport = {
  async call(name, ...args) {
    if (name === "get_state") return fetchJson("/state");
    if (name === "drain_events") {
      const cursor = encodeURIComponent(state.data?.version || "");
      return fetchJson(`/events?cursor=${cursor}`);
    }
    if (name === "hide") {
      const bridge = shellApi();
      if (bridge && bridge.hide) await bridge.hide();
      return fetchJson("/hide", { method: "POST", body: "{}" });
    }
    if (name === "import_file") {
      const bridge = shellApi();
      if (bridge && bridge.pick_file) {
        const paths = await bridge.pick_file();
        if (!paths || !paths.length) return null;
        return fetchJson("/import_file", { method: "POST", body: JSON.stringify({ paths }) });
      }
      const osInput = document.getElementById("osFileInput");
      if (osInput) osInput.click();
      return null;
    }
    if (name === "upload_files") {
      return fetchJson("/upload_files", { method: "POST", body: JSON.stringify({ files: args[0] || [] }) });
    }
    const routes = {
      send_message: ["/send_message", { text: args[0], model_name: args[1], prompt_type: args[2] }],
      stop_jobs: ["/stop_jobs", {}],
      create_session: ["/create_session", {}],
      load_session: ["/load_session", { session_id: args[0] }],
      delete_session: ["/delete_session", { session_id: args[0] }],
      import_held_snaps: ["/import_held_snaps", {}],
      import_clipboard_image: ["/import_clipboard_image", {}],
      import_pinned_snap: ["/import_pinned_snap", { entry: args[0] }],
      clear_pending_attachment: ["/clear_pending_attachment", { attachment_id: args[0] }],
      set_model: ["/set_model", { model_name: args[0] }],
      set_mode: ["/set_mode", { mode: args[0] }],
      delete_message: ["/delete_message", { message_id: args[0] }],
      set_system_prompt: ["/set_system_prompt", { prompt_type: args[0], system_prompt: args[1], label: args[2] }],
      read_message: ["/read_message", { message_id: args[0] }],
      stop_speech: ["/stop_speech", {}],
      set_auto_read: ["/set_auto_read", { enabled: Boolean(args[0]) }],
      toggle_favorite_model: ["/toggle_favorite_model", { model_name: args[0] }],
      slash_command: ["/slash_command", { command: args[0] }],
      fork_session: ["/fork_session", { title: args[0] || "" }],
      compact_session: ["/compact_session", { focus: args[0] || "" }],
      export_session: ["/export_session", {}],
      steer_run: ["/steer_run", { instruction: args[0] || "", mode: args[1] || "steer" }],
    };
    const route = routes[name];
    if (!route) return null;
    return fetchJson(route[0], { method: "POST", body: JSON.stringify(route[1]) });
  },
};

async function call(name, ...args) {
  try {
    const result = await transport.call(name, ...args);
    if (state.offline) state.offline = false;
    if (result && result.unchanged) {
      renderRuntime(state.data?.runtime_status || {});
      return result;
    }
    if (result) setState(result);
    return result;
  } catch (error) {
    if (error && error.httpStatus) {
      renderRuntime({
        state: "error",
        scope: "ui",
        label: "Action needed",
        message: "Screenshot Chat server error.",
        detail: String(error.message || error),
        retryable: true,
      });
      return null;
    }
    state.offline = true;
    renderRuntime({
      state: "offline",
      scope: "ui",
      label: "Offline",
      message: "Connection to Screenshot Chat was lost. Your draft is preserved.",
      detail: String(error && error.message || error || "Connection failed"),
      retryable: true,
    });
    return null;
  }
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderMarkdown(text) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let list = [];
  let quote = [];
  let code = null;

  function flushParagraph() {
    if (!paragraph.length) return;
    html.push(`<p>${inlineMarkdown(escapeHtml(paragraph.join("\n"))).replaceAll("\n", "<br>")}</p>`);
    paragraph = [];
  }

  function flushList() {
    if (!list.length) return;
    html.push(`<ul>${list.map(item => `<li>${inlineMarkdown(escapeHtml(item))}</li>`).join("")}</ul>`);
    list = [];
  }

  function flushQuote() {
    if (!quote.length) return;
    html.push(`<blockquote>${inlineMarkdown(escapeHtml(quote.join("\n"))).replaceAll("\n", "<br>")}</blockquote>`);
    quote = [];
  }

  function flushOpenBlocks() {
    flushParagraph();
    flushList();
    flushQuote();
  }

  for (const line of lines) {
    const fence = line.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fence) {
      if (code) {
        html.push(`<pre><code data-lang="${escapeHtml(code.lang || "text")}">${escapeHtml(code.lines.join("\n"))}</code></pre>`);
        code = null;
      } else {
        flushOpenBlocks();
        code = { lang: fence[1] || "text", lines: [] };
      }
      continue;
    }
    if (code) {
      code.lines.push(line);
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushOpenBlocks();
      const level = Math.min(heading[1].length, 6);
      html.push(`<h${level}>${inlineMarkdown(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }

    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      flushQuote();
      list.push(bullet[1]);
      continue;
    }

    const blockquote = line.match(/^>\s?(.*)$/);
    if (blockquote) {
      flushParagraph();
      flushList();
      quote.push(blockquote[1]);
      continue;
    }

    if (!line.trim()) {
      flushOpenBlocks();
      continue;
    }

    flushList();
    flushQuote();
    paragraph.push(line);
  }

  if (code) {
    html.push(`<pre><code data-lang="${escapeHtml(code.lang || "text")}">${escapeHtml(code.lines.join("\n"))}</code></pre>`);
  }
  flushOpenBlocks();
  return html.join("");
}

// Only explicit web/mail schemes may become links; relative, protocol-relative and active schemes stay inert.
function safeMarkdownHref(url) {
  const trimmed = String(url || "").trim();
  return /^(?:https?:\/\/|mailto:)/i.test(trimmed) ? trimmed : "";
}

function inlineMarkdown(value) {
  return value
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, label, url) => {
      const href = safeMarkdownHref(url);
      return href ? `<a href="${href}" rel="noopener noreferrer">${label}</a>` : match;
    });
}

function renderAttachment(att, removable = false) {
  const label = att.label || att.path || "attachment";
  const image = att.kind === "image"
    ? `<button type="button" class="attachment-preview" data-open-image="${escapeHtml(att.url)}" data-open-label="${escapeHtml(label)}"><img src="${escapeHtml(att.url)}" alt="${escapeHtml(label)}" loading="lazy" decoding="async"><span class="attachment-open">Open</span></button>`
    : `<div class="attachment-file" title="${escapeHtml(label)}">${escapeHtml(fileBadge(label))}</div>`;
  const remove = removable ? `<button class="attachment-remove" data-remove="${att.id}" title="Remove attachment">X</button>` : "";
  return `<div class="attachment">${image}<span>${escapeHtml(label)}</span>${remove}</div>`;
}

function fileBadge(label) {
  const ext = String(label || "").split(".").pop();
  return ext && ext.length <= 5 ? ext.toUpperCase() : "FILE";
}

function messageSignature(msg) {
  const speech = state.data?.speech || {};
  const speechSignature = speech.message_id === msg.id ? `${speech.state}:${speech.message_id}` : "";
  return `${msg.signature || `${msg.id}:${msg.status}`}:${speechSignature}`;
}

function renderTimeline(events) {
  const rows = (events || []).slice(-8).filter(Boolean);
  if (!rows.length) return "";
  return `<div class="timeline">${rows.map(event => {
    const title = event.title || event.kind || "Pi event";
    const status = event.status ? `<span class="timeline-status">${escapeHtml(event.status)}</span>` : "";
    const text = event.text ? `<div class="timeline-text">${escapeHtml(event.text)}</div>` : "";
    return `<div class="timeline-row ${escapeHtml(event.status || "")}"><div><span class="timeline-title">${escapeHtml(title)}</span>${status}</div>${text}</div>`;
  }).join("")}</div>`;
}

function renderStructuredBlocks(structured) {
  if (!structured || typeof structured !== "object") return "";
  const parts = [];
  if (structured.thinking) {
    parts.push(`<details class="pi-thinking"><summary>🧠 Reasoning Trace (${structured.thinking.length} chars)</summary><pre>${escapeHtml(structured.thinking)}</pre></details>`);
  }
  if (Array.isArray(structured.tools) && structured.tools.length) {
    for (const tool of structured.tools) {
      const badge = tool.is_error ? "error" : (tool.status || "done");
      const argsText = typeof tool.args === "string" ? tool.args : JSON.stringify(tool.args || {}, null, 2);
      parts.push(`<details class="pi-tool-card ${escapeHtml(badge)}"><summary>🛠️ <code>${escapeHtml(tool.name || "tool")}</code> <span class="timeline-status">${escapeHtml(badge)}</span></summary><div class="pi-tool-body"><pre>${escapeHtml(argsText)}</pre>${tool.result ? `<pre class="pi-tool-output">${escapeHtml(tool.result)}</pre>` : ""}</div></details>`);
    }
  }
  if (Array.isArray(structured.subagents) && structured.subagents.length) {
    for (const sub of structured.subagents) {
      parts.push(`<div class="pi-subagent-card"><strong>🤖 Subagent ${escapeHtml(sub.task_id || "")} (${escapeHtml(sub.status || "done")})</strong><div>${escapeHtml(sub.summary || "")}</div>${sub.result ? `<pre>${escapeHtml(sub.result)}</pre>` : ""}</div>`);
    }
  }
  return parts.join("");
}

function upsertMessage(msg) {
  const signature = messageSignature(msg);
  const cached = renderedMessages.get(msg.id);
  if (cached && cached.signature === signature) return cached.node;
  const template = document.createElement("template");
  const canCopy = (msg.text || "").trim();
  const speech = state.data?.speech || {};
  const canRead = msg.role === "assistant" && msg.status === "done" && canCopy;
  const speakingThis = canRead && speech.message_id === msg.id && ["synthesizing", "playing", "stopping"].includes(speech.state);
  const actionButtons = [];
  if (canCopy) actionButtons.push(`<button type="button" data-copy-message="${escapeHtml(msg.id)}">Copy</button>`);
  if (canRead) {
    actionButtons.push(speakingThis
      ? `<button type="button" data-stop-speech="1">Stop</button>`
      : `<button type="button" data-read-message="${escapeHtml(msg.id)}">Read</button>`);
  }
  const actions = actionButtons.length ? `<div class="message-actions">${actionButtons.join("")}</div>` : "";
  const roleClass = msg.status === "note" ? "note" : (msg.role === "user" ? "user" : "assistant");
  template.innerHTML = `
    <article class="message ${roleClass} ${msg.status}" data-message-id="${escapeHtml(msg.id)}">
      <div class="message-head">
        <div class="author">${escapeHtml(msg.author || "main")}</div>
        ${actions}
      </div>
      ${renderStructuredBlocks(msg.structured)}
      <div class="content">${renderMarkdown((msg.structured && msg.structured.text) || msg.text || (msg.status === "thinking" ? "Thinking" : ""))}</div>
      ${renderTimeline(msg.events)}
      ${msg.attachments && msg.attachments.length ? `<div class="message-attachments">${msg.attachments.map(att => renderAttachment(att)).join("")}</div>` : ""}
    </article>
  `.trim();
  const node = template.content.firstElementChild;
  renderedMessages.set(msg.id, { signature, node });
  return node;
}

function renderMessages(data, sessionChanged = false) {
  const ids = data.messages.map(msg => `${msg.id}:${msg.signature || msg.status}`).join("|");
  const hasNewMessages = ids !== state.lastMessageIds && state.lastMessageIds !== "";
  const shouldStick = !sessionChanged && (state.localSendPending || state.atBottom);
  if (hasNewMessages && !state.atBottom && !sessionChanged) state.hasUnseenBottom = true;
  const activeIds = new Set(data.messages.map(msg => msg.id));
  for (const messageId of renderedMessages.keys()) {
    if (!activeIds.has(messageId)) renderedMessages.delete(messageId);
  }
  const nodes = data.messages.map(upsertMessage);
  if (!nodes.length) {
    const empty = document.createElement("div");
    empty.className = "empty-welcome";
    empty.innerHTML = `
      <h3>🏮 STC Multimodal &amp; Pi-RPC Workspace (v2.1)</h3>
      <p>Snap regions with <strong>Right-Drag</strong>, push multi-snaps with <strong>Ctrl+Shift+Snap</strong>, drop any image/PDF/DOCX/code file here, or use Pi-RPC commands:</p>
      <div class="welcome-grid">
        <span class="welcome-pill" data-welcome-slash="/pi">🤖 /pi (Pi-RPC Agent)</span>
        <span class="welcome-pill" data-welcome-slash="/thinking high">🧠 /thinking high</span>
        <span class="welcome-pill" data-welcome-slash="/skills">🧰 /skills</span>
        <span class="welcome-pill" data-welcome-slash="/compact">🗜 /compact</span>
        <span class="welcome-pill" data-welcome-slash="/fork">⑂ /fork</span>
        <span class="welcome-pill" data-welcome-slash="/export">⤓ /export HTML</span>
      </div>
    `;
    el.messages.replaceChildren(empty);
  } else if (!childrenMatchOrder(el.messages, nodes)) {
    el.messages.replaceChildren(...nodes);
  }
  state.lastMessageIds = ids;
  return shouldStick;
}

function childrenMatchOrder(parent, nodes) {
  if (parent.children.length !== nodes.length) return false;
  return nodes.every((node, index) => parent.children[index] === node);
}

function renderPending(data) {
  const items = data.pending_attachments || [];
  el.attachments.hidden = !items.length;
  el.attachments.innerHTML = items.map(att => renderAttachment(att, true)).join("");
}

function renderSessionRail(data) {
  const sessions = data.sessions || [];
  el.railSessions.innerHTML = sessions.map(session => `
    <div class="rail-session ${session.active ? "active" : ""}">
      <button class="rail-session-load" type="button" data-session="${escapeHtml(session.id)}" title="${escapeHtml(session.title)}">${session.pending ? "• " : ""}${escapeHtml(session.title)}</button>
      <button class="rail-session-delete" type="button" data-delete-session="${escapeHtml(session.id)}" title="Delete chat" aria-label="Delete ${escapeHtml(session.title)}">×</button>
    </div>
  `).join("");
}

function renderHistoryMenu() {
  const sessions = state.data?.sessions || [];
  showMenu(`<button data-new-chat="1">+ New chat</button>` + sessions.map(session => `
    <div class="session-row ${session.active ? "active" : ""}">
      <button class="session-load" data-session="${escapeHtml(session.id)}">${session.pending ? "• " : ""}${escapeHtml(session.title)}</button>
      <button class="session-delete" data-delete-session="${escapeHtml(session.id)}" title="Delete chat">Delete</button>
    </div>
  `).join(""));
}

function lastPrimaryError(data) {
  const messages = data?.messages || [];
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].status === "error") return String(messages[index].text || "").trim();
  }
  return "";
}

function renderRuntime(runtime = {}) {
  const effective = state.offline ? {
    state: "offline",
    scope: "ui",
    label: "Offline",
    message: "Connection to Screenshot Chat was lost. Your draft is preserved.",
    detail: runtime.detail || "",
    retryable: true,
  } : runtime;
  const runtimeState = effective.state || "ready";
  const label = effective.label || (runtimeState === "ready" ? "Ready" : runtimeState.replaceAll("_", " "));
  el.runtimeStatus.className = `runtime-status ${runtimeState}`;
  el.runtimeLabel.textContent = label;

  const primaryError = lastPrimaryError(state.data);
  const message = String(effective.message || "").trim();
  const showMessage = Boolean(message) && !(runtimeState === "error" && message === primaryError);
  const detail = String(effective.detail || "").trim();
  const actionable = ["waiting_approval", "error", "offline"].includes(runtimeState);
  el.runtimeBanner.hidden = !(showMessage && actionable);
  el.runtimeBanner.className = `runtime-banner ${runtimeState}`;
  if (!el.runtimeBanner.hidden) {
    const details = detail ? `<details><summary>Details</summary><div class="runtime-detail">${escapeHtml(detail)}</div></details>` : "";
    el.runtimeBanner.innerHTML = `<div>${escapeHtml(message)}</div>${details}`;
  } else {
    el.runtimeBanner.innerHTML = "";
  }

  const pending = Boolean(state.data?.session?.pending_job_ids?.length);
  const stopping = runtimeState === "canceling";
  el.actionBtn.textContent = pending ? (stopping ? "Stopping..." : "Stop") : "Send";
  el.actionBtn.classList.toggle("stop", pending);
  el.actionBtn.disabled = state.offline || stopping;
  el.input.disabled = state.offline;
}

function setState(data) {
  const nextSessionId = data.active_session_id || data.session?.id || "";
  const sessionChanged = Boolean(state.activeSessionId && nextSessionId && state.activeSessionId !== nextSessionId);
  const oldAtBottom = sessionChanged ? false : nearBottom();
  if (sessionChanged) {
    cancelScrollTimers();
    renderedMessages.clear();
    state.lastMessageIds = "";
    state.hasUnseenBottom = false;
  }
  state.activeSessionId = nextSessionId;
  state.data = data;
  state.atBottom = oldAtBottom;
  el.title.textContent = data.session.title || "New Chat";
  el.modelBtn.textContent = data.model_label || data.session.model_label || modelLabel(data.session.model_name);
  const speech = data.speech || {};
  el.autoReadBtn.textContent = speech.auto_read ? "Auto-read on" : "Auto-read off";
  el.autoReadBtn.setAttribute("aria-pressed", speech.auto_read ? "true" : "false");
  el.status.hidden = !speech.message;
  el.status.textContent = speech.message || "";
  renderModeToggle(data.session.mode || "chat");
  const shouldStick = renderMessages(data, sessionChanged);
  renderPending(data);
  renderSessionRail(data);
  renderRuntime(data.runtime_status || {});
  if (sessionChanged) scrollToTop();
  updateJumpButton();
  if (shouldStick) scrollToBottom(nextSessionId);
  state.localSendPending = false;
}

function shortModel(model) {
  if (!model) return "Model";
  if (model.startsWith("pi://agent/")) return "Pi Agent";
  const tail = model.split("/").pop();
  return tail.length > 18 ? `${tail.slice(0, 15)}...` : tail;
}

function modelOptions() {
  const options = state.data?.model_options;
  if (Array.isArray(options) && options.length) return options;
  return (state.data?.models || []).map(model => ({
    id: model,
    label: shortModel(model),
    runtime: "llm",
    available: true,
    reason: "",
  }));
}

function modelLabel(model) {
  const option = modelOptions().find(item => item.id === model);
  return option?.label || shortModel(model);
}

function renderModelButton(option) {
  const selected = option.id === state.data?.session?.model_name;
  const disabled = option.available === false;
  const star = option.favorite ? "★ " : "";
  const label = `${selected ? "[x] " : "[ ] "}${star}${option.label || shortModel(option.id)}`;
  const title = disabled && option.reason ? ` title="${escapeHtml(option.reason)}"` : "";
  return `<div class="model-row"><button data-model="${escapeHtml(option.id)}"${disabled ? " disabled" : ""}${title}>${escapeHtml(label)}</button><button type="button" class="fav-btn" data-fav-model="${escapeHtml(option.id)}" title="Toggle Favorite">${option.favorite ? "★" : "☆"}</button></div>`;
}

function renderModeToggle(mode) {
  el.modeToggle.querySelectorAll("button[data-mode]").forEach(button => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  el.input.placeholder = mode === "note" ? "Take a note..." : "Ask about screenshots...";
}

function systemPromptLabel(session) {
  const label = session?.system_prompt_label || session?.prompt_type || "Blank";
  return promptDisplayName(label);
}

function promptDisplayName(value) {
  const text = String(value || "Blank").replace(/^prompt_/, "").replaceAll("_", " ");
  return text.length > 20 ? `${text.slice(0, 17)}...` : text;
}

function nearBottom() {
  return el.messages.scrollTop + el.messages.clientHeight >= el.messages.scrollHeight - 16;
}

function cancelScrollTimers() {
  for (const timer of scrollTimers) clearTimeout(timer);
  for (const frame of scrollFrames) cancelAnimationFrame(frame);
  scrollTimers = [];
  scrollFrames = [];
}

function scrollToTop() {
  cancelScrollTimers();
  el.messages.scrollTop = 0;
  state.atBottom = nearBottom();
  state.hasUnseenBottom = false;
  updateJumpButton();
}

function scrollToBottom(sessionId = state.activeSessionId) {
  cancelScrollTimers();
  const apply = () => {
    if (sessionId && state.activeSessionId && sessionId !== state.activeSessionId) return;
    el.messages.scrollTop = el.messages.scrollHeight;
    state.atBottom = true;
    state.hasUnseenBottom = false;
    updateJumpButton();
  };
  apply();
  scrollFrames.push(requestAnimationFrame(apply));
  scrollTimers.push(setTimeout(apply, 80));
}

function updateJumpButton() {
  state.atBottom = nearBottom();
  if (state.atBottom) state.hasUnseenBottom = false;
  el.jumpBottom.hidden = state.atBottom && !state.hasUnseenBottom;
}

function autoSizeInput() {
  el.input.style.height = "auto";
  el.input.style.height = `${Math.min(el.input.scrollHeight, 132)}px`;
}

function showMenu(html) {
  el.menus.innerHTML = html;
}

function hasImageClipboard(event) {
  const data = event.clipboardData;
  if (!data) return false;
  const files = Array.from(data.files || []);
  if (files.some(file => String(file.type || "").startsWith("image/"))) return true;
  const items = Array.from(data.items || []);
  return items.some(item => item.kind === "file" && String(item.type || "").startsWith("image/"));
}

function hideMenu() {
  el.menus.innerHTML = "";
}

el.closeBtn.addEventListener("click", () => call("hide"));
el.jumpBottom.addEventListener("click", () => scrollToBottom());
el.messages.addEventListener("scroll", updateJumpButton);
el.input.addEventListener("input", () => {
  autoSizeInput();
  const val = el.input.value || "";
  if (val.startsWith("/") && !val.includes("\n")) {
    const query = val.trim().toLowerCase();
    const cmds = (state.data?.slash_commands || []).filter(c => c.command.startsWith(query) || query === "/");
    if (cmds.length) {
      showMenu(`<div class="menu-heading">Pi Sidecar Slash Commands</div>` + cmds.map(c =>
        `<button type="button" data-slash-cmd="${escapeHtml(c.command)}"><strong>${escapeHtml(c.command)}</strong> <span style="opacity:0.75">${escapeHtml(c.args || "")} — ${escapeHtml(c.description || "")}</span></button>`
      ).join(""));
    }
  }
});
el.input.addEventListener("paste", event => {
  if (!hasImageClipboard(event)) return;
  event.preventDefault();
  call("import_clipboard_image");
});
el.input.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendCurrent();
  }
});
el.actionBtn.addEventListener("click", () => {
  const pending = Boolean(state.data?.session?.pending_job_ids?.length);
  if (pending) call("stop_jobs");
  else sendCurrent();
});
el.modeToggle.addEventListener("click", event => {
  const button = event.target?.closest?.("button[data-mode]");
  if (button) call("set_mode", button.dataset.mode);
});

async function sendCurrent() {
  const text = el.input.value;
  if (state.offline) return;
  if (!text.trim() && !(state.data?.pending_attachments || []).length) return;
  state.localSendPending = true;
  const result = await call("send_message", text, state.data?.session?.model_name || "", state.data?.session?.prompt_type || "");
  if (!result) {
    state.localSendPending = false;
    return;
  }
  el.input.value = "";
  autoSizeInput();
}

el.modelBtn.addEventListener("click", () => {
  const options = modelOptions();
  showMenu(options.map(renderModelButton).join(""));
});
el.autoReadBtn.addEventListener("click", () => {
  call("set_auto_read", !(state.data?.speech?.auto_read));
});
el.runtimeStatus.addEventListener("click", () => {
  const runtime = state.data?.runtime_status || {};
  const detail = String(runtime.detail || "").trim();
  const message = String(runtime.message || "").trim();
  if (!detail && !message) return;
  showMenu(`
    <div class="menu-heading">${escapeHtml(runtime.label || "Runtime")}</div>
    ${message ? `<div class="runtime-menu-message">${escapeHtml(message)}</div>` : ""}
    ${detail ? `<div class="runtime-detail">${escapeHtml(detail)}</div>` : ""}
  `);
});

el.historyBtn.addEventListener("click", renderHistoryMenu);
el.newChatBtn.addEventListener("click", () => call("create_session"));
el.railCollapseBtn.addEventListener("click", () => {
  state.railCollapsed = !state.railCollapsed;
  el.app.classList.toggle("rail-collapsed", state.railCollapsed);
});
el.railSessions.addEventListener("click", event => {
  const deleteBtn = event.target?.closest?.("[data-delete-session]");
  if (deleteBtn) {
    if (window.confirm("Delete this chat history?")) call("delete_session", deleteBtn.dataset.deleteSession);
    return;
  }
  const sessionBtn = event.target?.closest?.("[data-session]");
  if (sessionBtn) call("load_session", sessionBtn.dataset.session);
});

el.addBtn.addEventListener("click", () => {
  const pins = state.data?.pinned_snaps || [];
  showMenu(`
    <button data-held="1">Add held snaps</button>
    <button data-file="1">Add file</button>
    <div class="menu-heading">Pi Sidecar Harness</div>
    <button data-slash-run="/pi">🤖 Switch to Pi Sidecar (/pi)</button>
    <button data-slash-run="/compact">🗜️ Compact Context (/compact)</button>
    <button data-slash-run="/fork">🌿 Fork Session (/fork)</button>
    <button data-slash-run="/export">📄 Export Full HTML (/export)</button>
    <button data-slash-run="/harness init">⚙️ Init .pi Harness (/harness init)</button>
    ${renderSystemPromptMenu()}
    ${pins.map((pin, index) => `<button data-pin="${index}">${pin.used ? "[x]" : "[ ]"} ${escapeHtml(pin.display_title)}</button>`).join("")}
  `);
});

function renderSystemPromptMenu() {
  const prompts = state.data?.prompts || {};
  const currentLabel = systemPromptLabel(state.data?.session);
  return `
    <div class="menu-heading">System prompt: ${escapeHtml(currentLabel)}</div>
    <button data-system-blank="1">Blank</button>
    <button data-system-custom="1">Custom...</button>
    ${Object.keys(prompts).map(key => `<button data-system-key="${escapeHtml(key)}">${escapeHtml(promptDisplayName(key))}</button>`).join("")}
  `;
}

el.attachments.addEventListener("click", event => {
  const removeBtn = event.target?.closest?.("[data-remove]");
  if (removeBtn) call("clear_pending_attachment", removeBtn.dataset.remove);
  const imageBtn = event.target?.closest?.("[data-open-image]");
  if (imageBtn) openImageViewer(imageBtn.dataset.openImage, imageBtn.dataset.openLabel || "Image");
});

el.messages.addEventListener("click", event => {
  const imageBtn = event.target?.closest?.("[data-open-image]");
  if (imageBtn) {
    event.preventDefault();
    openImageViewer(imageBtn.dataset.openImage, imageBtn.dataset.openLabel || "Image");
    return;
  }
  const readBtn = event.target?.closest?.("[data-read-message]");
  if (readBtn) {
    call("read_message", readBtn.dataset.readMessage);
    return;
  }
  const stopSpeechBtn = event.target?.closest?.("[data-stop-speech]");
  if (stopSpeechBtn) {
    call("stop_speech");
    return;
  }
  const copyBtn = event.target?.closest?.("[data-copy-message]");
  if (copyBtn) {
    const message = getMessageById(copyBtn.dataset.copyMessage);
    copyText(message?.text || "");
  }
});

el.messages.addEventListener("contextmenu", event => {
  const messageNode = event.target?.closest?.(".message");
  if (!messageNode) return;
  const message = getMessageById(messageNode.dataset.messageId);
  if (!message) return;
  event.preventDefault();
  showContextMenu(event.clientX, event.clientY, message);
});

el.menus.addEventListener("click", event => {
  const item = event.target;
  const deleteBtn = item.closest?.("[data-delete-session]");
  if (deleteBtn) {
    const sessionId = deleteBtn.dataset.deleteSession;
    if (window.confirm("Delete this chat history?")) call("delete_session", sessionId);
    hideMenu();
    return;
  }
  if (item.dataset.favModel) {
    call("toggle_favorite_model", item.dataset.favModel);
    return;
  }
  if (item.dataset.slashCmd) {
    el.input.value = item.dataset.slashCmd + " ";
    el.input.focus();
    hideMenu();
    return;
  }
  if (item.dataset.slashRun) {
    call("slash_command", item.dataset.slashRun);
    hideMenu();
    return;
  }
  if (item.dataset.model) call("set_model", item.dataset.model);
  if (item.dataset.session) call("load_session", item.dataset.session);
  if (item.dataset.newChat) call("create_session");
  if (item.dataset.held) call("import_held_snaps");
  if (item.dataset.file) call("import_file");
  if (item.dataset.systemBlank) call("set_system_prompt", "", "", "Blank");
  if (item.dataset.systemCustom) {
    const current = state.data?.session?.system_prompt || "";
    const value = window.prompt("Custom system prompt", current);
    if (value !== null) call("set_system_prompt", "custom", value, "Custom");
  }
  if (item.dataset.systemKey) {
    const key = item.dataset.systemKey;
    call("set_system_prompt", key, state.data.prompts[key] || "", key);
  }
  if (item.dataset.pin) call("import_pinned_snap", state.data.pinned_snaps[Number(item.dataset.pin)]);
  hideMenu();
});

const imageViewer = document.createElement("div");
imageViewer.className = "image-viewer";
imageViewer.hidden = true;
imageViewer.innerHTML = `
  <div class="image-viewer-backdrop" data-close-image="1"></div>
  <div class="image-viewer-panel">
    <div class="image-viewer-topbar">
      <span class="image-viewer-title"></span>
      <button data-close-image="1">Close</button>
    </div>
    <img alt="">
  </div>
`;
document.body.appendChild(imageViewer);

const contextMenu = document.createElement("div");
contextMenu.className = "context-menu";
contextMenu.hidden = true;
document.body.appendChild(contextMenu);

function openImageViewer(url, label) {
  if (!url) return;
  imageViewer.querySelector("img").src = url;
  imageViewer.querySelector("img").alt = label || "Image";
  imageViewer.querySelector(".image-viewer-title").textContent = label || "Image";
  imageViewer.hidden = false;
}

function closeImageViewer() {
  imageViewer.hidden = true;
  imageViewer.querySelector("img").removeAttribute("src");
}

imageViewer.addEventListener("click", event => {
  if (event.target?.dataset?.closeImage) closeImageViewer();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") {
    if (!imageViewer.hidden) {
      closeImageViewer();
      return;
    }
    if (!contextMenu.hidden) {
      hideContextMenu();
      return;
    }
    if (el.menus.innerHTML) {
      hideMenu();
      return;
    }
    call("hide");
    return;
  }
  if (!event.ctrlKey) return;
  const key = event.key.toLowerCase();
  if (key === "w") {
    event.preventDefault();
    call("hide");
  } else if (key === "n") {
    event.preventDefault();
    call("create_session");
  } else if (event.key.toLowerCase() === "l") {
    event.preventDefault();
    el.input.focus();
  }
});

function getMessageById(messageId) {
  return (state.data?.messages || []).find(message => message.id === messageId);
}

function showContextMenu(x, y, message) {
  const copy = (message.text || "").trim()
    ? `<button data-copy-response="${escapeHtml(message.id)}">Copy all response</button>`
    : "";
  contextMenu.innerHTML = `${copy}<button data-delete-message="${escapeHtml(message.id)}">Delete</button>`;
  contextMenu.style.left = `${Math.min(x, window.innerWidth - 190)}px`;
  contextMenu.style.top = `${Math.min(y, window.innerHeight - 80)}px`;
  contextMenu.hidden = false;
}

function hideContextMenu() {
  contextMenu.hidden = true;
}

contextMenu.addEventListener("click", event => {
  const copyMessageId = event.target?.dataset?.copyResponse;
  const deleteMessageId = event.target?.dataset?.deleteMessage;
  if (copyMessageId) {
    const message = getMessageById(copyMessageId);
    copyText(message?.text || "");
    hideContextMenu();
    return;
  }
  if (deleteMessageId) {
    call("delete_message", deleteMessageId);
    hideContextMenu();
  }
});
document.addEventListener("click", event => {
  if (!contextMenu.contains(event.target)) hideContextMenu();
});
window.addEventListener("scroll", hideContextMenu, true);

async function copyText(text) {
  if (!text) return;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    el.status.hidden = false;
    el.status.textContent = "Response copied.";
  } catch (error) {
    el.status.hidden = false;
    el.status.textContent = "Copy failed.";
  }
}


async function uploadClientFileList(fileList) {
  const files = Array.from(fileList || []).filter(f => f && f.size > 0).slice(0, 10);
  if (!files.length) return;
  el.status.hidden = false;
  el.status.textContent = `Uploading ${files.length} file(s)...`;
  const encoded = await Promise.all(files.map(file => new Promise(resolve => {
    const reader = new FileReader();
    reader.onload = () => resolve({
      name: file.name || "attachment.png",
      type: file.type || "application/octet-stream",
      size: file.size,
      dataBase64: String(reader.result || ""),
    });
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  })));
  const valid = encoded.filter(Boolean);
  if (valid.length) {
    await call("upload_files", valid);
    el.status.textContent = `Attached ${valid.length} file(s).`;
  }
}

const dropOverlay = document.getElementById("dropOverlay");
const osFileInput = document.getElementById("osFileInput");
if (osFileInput) {
  osFileInput.addEventListener("change", () => {
    if (osFileInput.files && osFileInput.files.length) {
      void uploadClientFileList(osFileInput.files);
      osFileInput.value = "";
    }
  });
}

let dragCounter = 0;
window.addEventListener("dragenter", event => {
  if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes("Files")) return;
  event.preventDefault();
  dragCounter += 1;
  if (dropOverlay) dropOverlay.hidden = false;
});
window.addEventListener("dragover", event => {
  if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes("Files")) return;
  event.preventDefault();
});
window.addEventListener("dragleave", () => {
  dragCounter = Math.max(0, dragCounter - 1);
  if (dragCounter === 0 && dropOverlay) dropOverlay.hidden = true;
});
window.addEventListener("drop", event => {
  dragCounter = 0;
  if (dropOverlay) dropOverlay.hidden = true;
  if (!event.dataTransfer || !event.dataTransfer.files || !event.dataTransfer.files.length) return;
  event.preventDefault();
  void uploadClientFileList(event.dataTransfer.files);
});

document.addEventListener("click", event => {
  const quickSlash = event.target?.closest?.("[data-quick-slash],[data-welcome-slash]");
  if (quickSlash) {
    const cmd = quickSlash.dataset.quickSlash || quickSlash.dataset.welcomeSlash || "";
    if (cmd) void call("slash_command", cmd);
    return;
  }
  const quickAct = event.target?.closest?.("[data-quick-action]");
  if (quickAct) {
    const act = quickAct.dataset.quickAction;
    if (act === "held") void call("import_held_snaps");
    else if (act === "clip") void call("import_clipboard_image");
    else if (act === "upload") void call("import_file");
  }
});

let bootstrapped = false;

async function bootstrap() {
  if (bootstrapped || !runtimeConfig.serverUrl) return;
  bootstrapped = true;
  await call("get_state");
  window.set_interval = window.setInterval(
    () => {
      if (document.hidden && !state.data?.busy && state.data?.ui?.action === "hide") return;
      return call("drain_events");
    },
    Number(runtimeConfig.pollIntervalMs || 650)
  );
}

window.ScreenshotChat = { setState, bootstrap, modelOptions, modelLabel, hasImageClipboard, scrollToBottom, scrollToTop };
window.addEventListener("pywebviewready", bootstrap);
window.addEventListener("DOMContentLoaded", bootstrap);

