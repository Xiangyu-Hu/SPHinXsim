(function () {
  "use strict";

  const conversation = document.getElementById("conversation");
  const emptyState = document.getElementById("empty-state");
  const form = document.getElementById("composer");
  const input = document.getElementById("message-input");
  const sendButton = document.getElementById("send-button");
  const status = document.getElementById("status");
  let bridge = null;
  let assistantNode = null;

  function setComposerState() { sendButton.disabled = input.value.trim().length === 0 || !bridge; }
  function addMessage(role, text) {
    emptyState.hidden = true;
    const node = document.createElement("article");
    node.className = "message " + role;
    const label = document.createElement("div");
    label.className = "role";
    label.textContent = role === "user" ? "You" : "SPHinXsim";
    const content = document.createElement("div");
    content.textContent = text || "";
    node.append(label, content);
    conversation.appendChild(node);
    conversation.scrollTop = conversation.scrollHeight;
    return content;
  }
  function resizeInput() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 140) + "px"; }
  function send() {
    const text = input.value.trim();
    if (!text || !bridge) return;
    addMessage("user", text);
    input.value = "";
    resizeInput();
    setComposerState();
    bridge.sendMessage(text);
  }
  form.addEventListener("submit", function (event) { event.preventDefault(); send(); });
  input.addEventListener("input", function () { resizeInput(); setComposerState(); });
  input.addEventListener("keydown", function (event) { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } });

  if (typeof qt === "undefined" || typeof QWebChannel === "undefined") {
    status.textContent = "Chat interface bridge is unavailable.";
    return;
  }
  new QWebChannel(qt.webChannelTransport, function (channel) {
    bridge = channel.objects.bridge;
    bridge.assistantMessageStarted.connect(function () { assistantNode = addMessage("assistant", ""); });
    bridge.assistantTextDelta.connect(function (text) {
      if (!assistantNode) assistantNode = addMessage("assistant", "");
      assistantNode.textContent += text;
      conversation.scrollTop = conversation.scrollHeight;
    });
    bridge.assistantMessageFinished.connect(function () { assistantNode = null; });
    bridge.statusChanged.connect(function (text) { status.textContent = text; });
    bridge.error.connect(function (text) { addMessage("assistant", "Error: " + text); });
    setComposerState();
    input.focus();
  });
})();
