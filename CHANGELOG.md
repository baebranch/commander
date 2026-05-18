# Changelog

All notable changes to Command will be documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project adheres to [Semantic Versioning](https://semver.org/).

---

## [0.1.0] – 2026-05-18

### Added
- Background system tray icon (pystray) with menu-driven controls.
- Three voice modes activated by double-pressing a function key:
  - **Dictation** (double F12) — types transcribed speech at the focused cursor.
  - **Meeting** (double F11) — appends a timestamped transcript to `~/meeting_YYYY-MM-DD_HH-MM.txt`.
  - **Command** (double F10) — placeholder for desktop automation via voice.
- Input device selector submenu showing only physical microphones (filtered via PyAudio default host API).
- Automatic selection of the system default microphone on startup.
- Menu checkmarks reflect the active mode; hotkey activations also trigger a tray notification.
- Async-safe stop for `SpeechmaticsAgent` using `asyncio.Event` so the event loop exits cleanly without blocking.
- `caret_available()` now queries the foreground window's thread and accepts `hwndFocus` in addition to `hwndCaret` so modern apps (browsers, Electron) are covered.
