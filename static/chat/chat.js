// Minimal chat frontend — vanilla ES module, no framework/build step,
// matching static/mind/scene.js's directness. No auto-reconnect in v1.
//
// Voice is layered on top of the same text path, not a parallel one:
// SpeechRecognition writes into #composer-input and submits through the
// existing form, and speechSynthesis reads the same accumulated reply text
// the chat bubble already shows. Both are the browser's own Web Speech
// API — client-side, the user's own network, no backend involvement at
// all, so none of this session's network-blocking issues apply here.
const statsEl = document.getElementById("chat-stats");
const logEl = document.getElementById("chat-log");
const form = document.getElementById("composer");
const input = document.getElementById("composer-input");
const sendButton = document.getElementById("composer-send");
const micButton = document.getElementById("mic-button");
const voiceOutputToggle = document.getElementById("voice-output-toggle");

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
  micButton.disabled = busy;
  if (!busy) input.focus();
}

function setConnected(connected) {
  input.disabled = !connected;
  sendButton.disabled = !connected;
  micButton.disabled = !connected;
}

const proto = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${proto}//${location.host}/ws/chat`);
let assistantBubble = null;
let accumulatedReply = "";
let voiceOutputEnabled = true;

setConnected(false);
statsEl.textContent = "connecting…";

function speak(text) {
  if (!voiceOutputEnabled || !text || !("speechSynthesis" in window)) return;
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

voiceOutputToggle.addEventListener("click", () => {
  voiceOutputEnabled = !voiceOutputEnabled;
  voiceOutputToggle.textContent = voiceOutputEnabled ? "🔊" : "🔇";
  voiceOutputToggle.classList.toggle("muted", !voiceOutputEnabled);
  if (!voiceOutputEnabled && "speechSynthesis" in window) window.speechSynthesis.cancel();
});

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
      accumulatedReply += payload.text;
      logEl.scrollTop = logEl.scrollHeight;
      break;
    case "turn_complete":
      assistantBubble = null;
      speak(accumulatedReply);
      accumulatedReply = "";
      statsEl.textContent = "ready";
      setBusy(false);
      break;
    case "error":
      appendMessage("error", `Error: ${payload.message}`);
      assistantBubble = null;
      accumulatedReply = "";
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

// Speech input — feature-detected. Browsers without SpeechRecognition
// (Firefox, Safari as of this writing) simply keep the mic button hidden;
// typing still works exactly as before.
const SpeechRecognitionImpl = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognitionImpl) {
  const recognition = new SpeechRecognitionImpl();
  recognition.continuous = false;
  recognition.interimResults = true;
  let listening = false;

  const setListening = (value) => {
    listening = value;
    micButton.classList.toggle("listening", value);
  };

  recognition.addEventListener("result", (event) => {
    let transcript = "";
    let isFinal = false;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      transcript += event.results[i][0].transcript;
      if (event.results[i].isFinal) isFinal = true;
    }
    input.value = transcript;
    if (isFinal && transcript.trim()) {
      form.requestSubmit();
    }
  });

  recognition.addEventListener("error", (event) => {
    setListening(false);
    if (event.error === "no-speech") return; // not worth a system message — just try again
    appendMessage("system", `Voice input error: ${event.error}`);
  });

  recognition.addEventListener("end", () => setListening(false));

  micButton.addEventListener("click", () => {
    if (listening) {
      recognition.stop();
      return;
    }
    if ("speechSynthesis" in window) window.speechSynthesis.cancel(); // don't talk over yourself
    input.value = "";
    setListening(true);
    recognition.start();
  });
} else {
  micButton.remove();
}
