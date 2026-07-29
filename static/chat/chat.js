// Minimal chat frontend — vanilla ES module, no framework/build step,
// matching static/mind/scene.js's directness. No auto-reconnect in v1.
const statsEl = document.getElementById("stats");
const logEl = document.getElementById("chat-log");
const form = document.getElementById("composer");
const input = document.getElementById("composer-input");
const sendButton = document.getElementById("composer-send");

function appendMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
  return div;
}

function setBusy(busy) {
  input.disabled = busy;
  sendButton.disabled = busy;
  if (!busy) input.focus();
}

function setConnected(connected) {
  input.disabled = !connected;
  sendButton.disabled = !connected;
}

const proto = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
let assistantBubble = null;

setConnected(false);
statsEl.textContent = "connecting…";

ws.addEventListener("message", (event) => {
  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    return;
  }

  switch (payload.type) {
    case "ready":
      statsEl.textContent = "ready";
      setConnected(true);
      break;
    case "assistant_message":
      if (!assistantBubble) assistantBubble = appendMessage("assistant", "");
      assistantBubble.textContent += payload.text;
      logEl.scrollTop = logEl.scrollHeight;
      break;
    case "turn_complete":
      assistantBubble = null;
      statsEl.textContent = "ready";
      setBusy(false);
      break;
    case "error":
      appendMessage("error", `Error: ${payload.message}`);
      assistantBubble = null;
      statsEl.textContent = "ready";
      setBusy(false);
      break;
    default:
      break;
  }
});

ws.addEventListener("close", () => {
  statsEl.textContent = "disconnected — refresh to retry";
  setConnected(false);
  appendMessage("system", "Connection closed.");
});

ws.addEventListener("error", () => {
  statsEl.textContent = "connection error";
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || input.disabled) return;
  appendMessage("user", text);
  input.value = "";
  setBusy(true);
  statsEl.textContent = "thinking…";
  ws.send(JSON.stringify({ type: "user_message", text }));
});
