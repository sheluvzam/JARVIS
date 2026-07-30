# JARVIS desktop companion

Gives JARVIS's Desktop sub-agent real access to your screen, files, and OS
by running a small program on your own Windows machine. The JARVIS backend
runs in the cloud and can't reach your computer directly — this program
dials out to it instead, over a WebSocket, and executes whatever commands
the backend sends.

## What this actually grants

Once paired and running, JARVIS can, on **this machine**, without asking
first:
- Take screenshots and read the active window's title
- List and read files at any path it's given
- Run arbitrary shell commands (this is also how it launches applications)
- Move the mouse, click, type text, and press keys

There is **no per-action confirmation step** — this was an explicit choice
made when setting this up, after being shown the risk (including indirect
risk: JARVIS's Research sub-agent reads web pages, and untrusted page
content could attempt to plant instructions for a later action-taking
step to blindly follow).

**The only kill switch is closing this process.** Keep its terminal window
visible while it's running so you can see what it's doing (every command
it executes is printed) and stop it (Ctrl+C) if anything looks wrong.
Closing it immediately cuts off all access — there's no other session to
tear down.

To revoke access without trusting yourself to always close it in time:
delete `backend/data/companion_token.txt` on the server and restart it. A
new token is generated, and this companion's old one stops working.

## Setup

1. `pip install -r requirements.txt`
2. `copy config.example.json config.json` (or just duplicate and rename it)
3. Edit `config.json`:
   - `backend_url`: the JARVIS backend's `/ws/companion` WebSocket URL —
     e.g. `ws://localhost:8731/ws/companion` for a locally-run backend, or
     `wss://your-host/ws/companion` for a remotely-hosted one.
   - `token`: printed by the JARVIS backend to its console at startup
     (`[jarvis] companion pairing token: ...`) — copy it exactly.
4. `python jarvis_companion.py`

You should see `paired with backend — waiting for commands.` If pairing
fails, double-check the token matches what the backend printed most
recently (it's stable across backend restarts, but regenerated if you
delete `companion_token.txt`).

Leave it running in the background while you want JARVIS to have desktop
access. There's no tray icon or background-service mode in this version —
it's a visible terminal process by design, so you always know it's active.
