# Commander

![Commander](commander/assets/social.png)

The Commander conversational AI agent handles the mundane tasks that every enterprise employee has to deal with, dictation for creating documents, drafting emails & prompts, taking and summarizing meeting notes and planning multistep workflows to complete complex tasks. By executing API calls, desktop automations and filesystem built in tools, tasks can be converted to systems and repeated. Ultimately freeing up users to focus on the core problems of their jobs.

Commander is a background voice agent for Windows. Double-press a function key to activate one of three modes — dictation, meeting recorder, or computer control — without leaving whatever you are doing.

---

## Modes

| Mode | Hotkey | What it does |
|---|---|---|
| **Dictation** | double F12 | Types transcribed speech at the cursor in the foreground window. Works in any app with a focused text field (browsers, editors, chat apps). |
| **Meeting** | double F11 | Records a continuous timestamped transcript of a meeting to `~/meeting_YYYY-MM-DD_HH-MM.txt`. |
| **Command** | double F10 | Desktop automation via voice (in development). |

Pressing the hotkey a second time while a mode is active deactivates it. Switching directly from one mode to another is supported — the current mode stops first.

---

## Quick Start

### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

### 2. Set your Speechmatics API key

```powershell
setx SPEECHMATICS_API_KEY "your_api_key_here"
# Restart your terminal after setx so the variable is picked up
```

### 3. Run

```powershell
python main.py
```

The app starts silently in the system tray. Right-click the tray icon to access the menu.

---

## Tray Menu

```
✓ Dictation   (double F12)
  Meeting     (double F11)
  Command     (double F10)
  ──────────────────────────
  Input Device ▶
    • Microphone Array (Realtek)   ← radio-checked = active device
      Headset Microphone
  ──────────────────────────
  Quit
```

Checkmarks on the three mode items reflect the currently active mode. The **Input Device** submenu lists physical microphones only (loopback and virtual devices are filtered out). Switching device while a mode is active briefly pauses and resumes recording on the new device.

---

## Dictation Mode

Activating Dictation streams microphone audio to the [Speechmatics](https://www.speechmatics.com/) real-time API. Each finalised segment is typed at the cursor using `pyautogui`.

- Text is only typed when the foreground window has keyboard focus (classic Win32 caret **or** `hwndFocus` for browsers and Electron apps).
- If no focused text field is detected the segment is printed to the console and discarded.

---

## Meeting Mode

Activating Meeting opens a new transcript file:

```
~/meeting_2026-05-18_14-30.txt
```

Every finalised segment is appended on its own line. The file is flushed after each write so it is readable in real time. A closing timestamp is written when the mode is deactivated or the app quits.

---

## Command Mode

> **Status: placeholder.** Voice input is received and printed to the console. A desktop automation agent will be wired in here.

Planned capabilities:
- Open, close, and switch applications by name.
- Dictate shell commands.
- Control the mouse and keyboard via voice instructions.

---

## Finding Your Microphone

If the default device does not work, run:

```powershell
python find_mic.py
```

This lists all usable physical input devices with their PyAudio index. Pass the correct index when constructing `TrayApp`:

```python
# main.py
app = TrayApp(device_index=3)
```

---

## Architecture

```
main.py
└── TrayApp                  tray_app.py
    ├── AppMode (enum)        IDLE / DICTATION / MEETING / COMMAND
    ├── DoubleKeyListener     hotkey.py   — pynput-based double-press detector
    ├── SpeechmaticsAgent     speech_client.py
    │   └── asyncio event loop in daemon thread
    │       ├── VoiceAgentClient  (speechmatics-voice)
    │       └── Microphone        (speechmatics-rt / pyaudio)
    └── utils.py
        ├── caret_available()    — foreground-window focus check
        ├── get_input_devices()  — filtered PyAudio device list
        └── get_default_input_device_index()
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SPEECHMATICS_API_KEY` | Yes | API key from the Speechmatics dashboard |

---

## License

MIT — see [LICENSE](LICENSE).


4. Double-press `F12` to enable/disable listening. When enabled, finished segments will be typed into the focused text input if a caret exists.

Notes
-----
- The implementation uses the official Speechmatics Python packages; if they are not installed or the API key is missing, the agent will not start but will print instructions.
- To enable ML turn detection features, install the smart extras: `pip install "speechmatics-voice[smart]"`.
- The caret detection is a Windows-specific heuristic (uses `GetGUIThreadInfo`).

