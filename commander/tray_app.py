import os
import time
import pystray
import threading
import pyautogui
from enum import Enum
from pathlib import Path
from typing import Optional
from datetime import datetime
from PIL import Image, ImageDraw
from pystray import MenuItem as Item

from .hotkey import DoubleKeyListener
from .speech_client import SpeechmaticsAgent
from .utils import (
  caret_available,
  get_input_devices,
  get_default_input_device_index,
  get_default_meetings_dir,
  get_speechmatics_api_key,
  set_speechmatics_api_key,
  get_gemini_api_key,
  set_gemini_api_key,
)
from .control_agent import ControlAgent
from .system_audio import SystemAudioRecorder


class AppMode(Enum):
  IDLE = "idle"
  DICTATION = "dictation"
  MEETING = "meeting"
  COMMAND = "command"


class TrayApp:
  def __init__(self, device_index: int = None):
    self.mode = AppMode.IDLE
    self._meeting_file = None
    self._meeting_path = None
    self.icon = self._create_icon()
    self.icon.title = "Commander"
    self.input_devices = get_input_devices()
    self.api_key = get_speechmatics_api_key()
    # Gemini / Google API key (used by ControlAgent)
    self.gemini_api_key = get_gemini_api_key()
    self._icon_thread: threading.Thread = None
    self.meetings_dir: Path = get_default_meetings_dir()

    self.device_index = device_index if device_index is not None else get_default_input_device_index()
    # Agent is created fresh each time a mode is activated; start with None.
    self.agent: SpeechmaticsAgent = None
    # Computer control agent (voice->actions). Default to dry-run for safety.
    try:
      self.control_agent = ControlAgent(dry_run=True)
    except Exception:
      self.control_agent = None
    # System audio recording (for meetings)
    self.record_system_audio: bool = False
    self._system_recorder: Optional[SystemAudioRecorder] = None

    self._hotkey_meeting = DoubleKeyListener(key="f11", callback=lambda: self._toggle_mode(AppMode.MEETING, from_hotkey=True))
    self._hotkey_command = DoubleKeyListener(key="f10", callback=lambda: self._toggle_mode(AppMode.COMMAND, from_hotkey=True))
    self._hotkey_dictation = DoubleKeyListener(key="f12", callback=lambda: self._toggle_mode(AppMode.DICTATION, from_hotkey=True))

  def _create_icon(self) -> pystray.Icon:
    try:
      base = Path(__file__).resolve().parent
      logo_path = base / "assets" / "logo.png"
      if logo_path.exists():
        img = Image.open(logo_path).convert("RGBA")
        # Resize preserving aspect ratio to fit 64x64
        img.thumbnail((64, 64), Image.LANCZOS)
        canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        w, h = img.size
        canvas.paste(img, ((64 - w) // 2, (64 - h) // 2), img)
        return pystray.Icon("Commander", canvas)
    except Exception as e:
      try:
        print("Tray icon load error:", e)
      except Exception:
        pass

    # Fallback generated icon
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((8, 8, 56, 56), fill=(30, 144, 255, 255))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 255))
    icon = pystray.Icon("Commander", image)
    return icon

  def _build_menu(self) -> pystray.Menu:
    device_items = [
      Item(
        name,
        self._make_select_device(idx),
        checked=lambda item, i=idx: self.device_index == i,
        radio=True,
      )
      for idx, name in self.input_devices
    ]
    
    short_dir = str(self.meetings_dir).replace(str(Path.home()), "~")
    speech_status = "configured" if self.api_key else "Not configured"
    gemini_status = "configured" if self.gemini_api_key else "Not configured"

    return pystray.Menu(
      Item('Dictation  (double F12)', lambda icon, item: self._toggle_mode(AppMode.DICTATION), checked=lambda item: self.mode == AppMode.DICTATION),
      Item('Meeting    (double F11)', lambda icon, item: self._toggle_mode(AppMode.MEETING), checked=lambda item: self.mode == AppMode.MEETING),
      Item('Command    (double F10)', lambda icon, item: self._toggle_mode(AppMode.COMMAND), checked=lambda item: self.mode == AppMode.COMMAND),
      pystray.Menu.SEPARATOR,
      Item('Input Device', pystray.Menu(*device_items)),
      Item('Record System Audio', lambda icon, item: self._toggle_record_system_audio(), checked=lambda item: self.record_system_audio),
      pystray.Menu.SEPARATOR,
      Item(f'Speechmatics API: {speech_status}', None, enabled=False),
      Item(f'Gemini API: {gemini_status}', None, enabled=False),
      Item('Set API Keys…', lambda icon, item: self._prompt_for_api_key()),
      Item('Execute Commands', lambda icon, item: self._toggle_command_execution(), checked=lambda item: (self.control_agent is not None and not self.control_agent.dry_run)),
      pystray.Menu.SEPARATOR,
      Item(f'Transcripts: {short_dir}', None, enabled=False),
      Item('Choose Transcript Folder…', lambda icon, item: self._choose_meetings_dir()),
      pystray.Menu.SEPARATOR,
      Item('Quit', lambda icon, item: self.shutdown()),
    )

  def _make_select_device(self, device_index: int):
    def callback(icon, item):
      self._select_device(device_index)
    return callback

  def _select_device(self, device_index: int):
    if device_index == self.device_index:
      return
    active_mode = self.mode
    if active_mode != AppMode.IDLE:
      self._deactivate()
    self.device_index = device_index
    if active_mode != AppMode.IDLE:
      self._activate(active_mode)

  # ------------------------------------------------------------------ modes --

  def _toggle_mode(self, mode: AppMode, from_hotkey: bool = False):
    if self.mode == mode:
      self._deactivate(from_hotkey=from_hotkey)
    else:
      self._activate(mode, from_hotkey=from_hotkey)

  def _activate(self, mode: AppMode, from_hotkey: bool = False):
    if not self.api_key:
      try:
        self.icon.notify("Speechmatics API key not set. Please configure via the tray menu.")
      except Exception:
        pass
      return

    if self.mode != AppMode.IDLE:
      self._stop_agent()
      self._close_meeting_file()

    self.mode = mode
    print(f"{mode.value.title()} started")

    if mode == AppMode.MEETING:
      self._open_meeting_file()

    self._start_agent()

    try:
      self.icon.update_menu()
    except Exception:
      pass
    if from_hotkey:
      try:
        self.icon.notify(f"{mode.value.title()} enabled")
      except Exception:
        pass

  def _deactivate(self, from_hotkey: bool = False):
    if self.mode == AppMode.IDLE:
      return
    prev = self.mode
    self._stop_agent()
    self._close_meeting_file()
    self.mode = AppMode.IDLE
    print(f"{prev.value.title()} stopped")

    try:
      self.icon.update_menu()
    except Exception:
      pass
    if from_hotkey:
      try:
        self.icon.notify(f"{prev.value.title()} disabled")
      except Exception:
        pass

  # --------------------------------------------------------------- agent i/o -

  def _start_agent(self):
    if not self.api_key:
      try:
        self.icon.notify("Speechmatics API key not set. Please configure via the tray menu.")
      except Exception:
        pass
      return
    # Always create a fresh agent so it picks up the current mode and device.
    self.agent = SpeechmaticsAgent(
      api_key=self.api_key,
      on_final_segment=self._on_final_segment,
      mode=self.mode.value,
      device_index=self.device_index,
    )
    self.agent.start()

  def _stop_agent(self):
    if self.agent:
      self.agent.stop()
    self.agent = None

  def _on_final_segment(self, text: str):
    print(f"[{self.mode.value}] {text}")
    if self.mode == AppMode.DICTATION:
      self._handle_dictation(text)
    elif self.mode == AppMode.MEETING:
      self._handle_meeting(text)
    elif self.mode == AppMode.COMMAND:
      self._handle_command(text)

  def _handle_dictation(self, text: str):
    if caret_available():
      try:
        pyautogui.write(text, interval=0.01)
      except Exception as e:
        print("Failed to type text:", e)

  def _handle_meeting(self, text: str):
    if self._meeting_file:
      try:
        self._meeting_file.write(text + "\n")
        self._meeting_file.flush()
      except Exception as e:
        print("Failed to write meeting transcript:", e)

  def _handle_command(self, text: str):
    # Use the ControlAgent to plan/execute actions for the given instruction.
    print(f"[command] Received: {text}")
    if not self.control_agent:
      try:
        self.icon.notify("Control agent not available")
      except Exception:
        pass
      return
    try:
      res = self.control_agent.execute_text(text)
      planned = res.get("planned", [])
      if self.control_agent.dry_run:
        summary = ", ".join([a.get("type", "?") for a in planned]) or "(no actions)"
        try:
          self.icon.notify(f"Planned actions: {summary}")
        except Exception:
          pass
      else:
        results = res.get("results", [])
        ok = all((r.get("result", {}).get("ok", False) if isinstance(r.get("result"), dict) else r.get("result") == "dry-run") for r in results)
        try:
          self.icon.notify("Command executed" if ok else "Command executed with errors")
        except Exception:
          pass
    except Exception as e:
      print("ControlAgent error:", e)
      try:
        self.icon.notify(f"ControlAgent error: {e}")
      except Exception:
        pass

  def _toggle_record_system_audio(self, icon=None, item=None):
    """Toggle recording of system audio for meetings."""
    self.record_system_audio = not self.record_system_audio
    try:
      # If a meeting is already active, start/stop the recorder immediately
      if self.mode == AppMode.MEETING and self._meeting_path and self._meeting_file:
        if self.record_system_audio and (not getattr(self, '_system_recorder', None)):
          system_path = self.meetings_dir / (self._meeting_path.stem + "_system.wav")
          self._system_recorder = SystemAudioRecorder(system_path)
          try:
            self._system_recorder.start()
            try:
              self.icon.notify("Recording system audio for meeting")
            except Exception:
              pass
          except Exception:
            print("Failed to start system audio recorder")
        elif not self.record_system_audio and getattr(self, '_system_recorder', None):
          try:
            self._system_recorder.stop()
          except Exception:
            pass
          self._system_recorder = None
      try:
        self.icon.update_menu()
      except Exception:
        pass
    except Exception:
      pass

  def _toggle_command_execution(self, icon=None, item=None):
    if not self.control_agent:
      try:
        self.icon.notify("Control agent not available")
      except Exception:
        pass
      return
    self.control_agent.dry_run = not self.control_agent.dry_run
    try:
      self.icon.update_menu()
    except Exception:
      pass
    try:
      self.icon.notify("Command execution enabled" if not self.control_agent.dry_run else "Command execution disabled (dry-run)")
    except Exception:
      pass

  # ---------------------------------------------------------- meeting files --

  def _choose_meetings_dir(self):
    """Open a native folder-picker dialog (runs in a daemon thread to avoid
    blocking the pystray event loop)."""
    def _pick():
      try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(
          title="Choose Transcript Folder",
          initialdir=str(self.meetings_dir.parent if self.meetings_dir.exists() else self.meetings_dir),
        )
        root.destroy()
        if chosen:
          self.meetings_dir = Path(chosen)
          print(f"Transcript folder set to: {self.meetings_dir}")
          try:
            self.icon.update_menu()
          except Exception:
            pass
      except Exception as e:
        print("Folder picker error:", e)
    threading.Thread(target=_pick, daemon=True).start()

  def _prompt_for_api_key(self):
    """Open a ttk dialog to prompt for Speechmatics and Gemini API keys.

    Keys are stored in the OS keyring when possible; otherwise the values
    are kept in-session (matching existing behaviour). Both keys may be
    provided; Speechmatics controls the running agent and will be reloaded
    automatically if needed.
    """
    def _prompt():
      try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.withdraw()

        dlg = tk.Toplevel(root)
        dlg.title("API Keys")
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)

        try:
          style = ttk.Style(dlg)
          for theme in ("vista", "clam", "alt", "default"):
            try:
              style.theme_use(theme)
              break
            except Exception:
              continue
        except Exception:
          pass

        frame = ttk.Frame(dlg, padding=16)
        frame.grid(row=0, column=0, sticky="nsew")

        # Speechmatics key
        ttk.Label(frame, text="Enter Speechmatics API Key:").grid(row=0, column=0, columnspan=2, sticky="w")
        key_var = tk.StringVar(value=self.api_key or "")
        entry = ttk.Entry(frame, width=44, textvariable=key_var, show="*")
        entry.grid(row=1, column=0, columnspan=2, pady=(8, 4), sticky="ew")
        show_var_sm = tk.BooleanVar(value=False)
        def _toggle_show_sm():
          entry.configure(show="" if show_var_sm.get() else "*")
        ttk.Checkbutton(frame, text="Show Speechmatics key", variable=show_var_sm, command=_toggle_show_sm).grid(row=2, column=0, sticky="w")

        # Gemini / Google key
        ttk.Label(frame, text="Enter Gemini/Google API Key:").grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        gem_var = tk.StringVar(value=self.gemini_api_key or "")
        gem_entry = ttk.Entry(frame, width=44, textvariable=gem_var, show="*")
        gem_entry.grid(row=4, column=0, columnspan=2, pady=(8, 4), sticky="ew")
        show_var_gem = tk.BooleanVar(value=False)
        def _toggle_show_gem():
          gem_entry.configure(show="" if show_var_gem.get() else "*")
        ttk.Checkbutton(frame, text="Show Gemini key", variable=show_var_gem, command=_toggle_show_gem).grid(row=5, column=0, sticky="w")

        status_lbl = ttk.Label(frame, text="", foreground="#c00")
        status_lbl.grid(row=6, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=2, pady=(12, 0), sticky="e")

        def _finish():
          root.quit()

        def _save():
          speech_val = key_var.get().strip()
          gem_val = gem_var.get().strip()
          if not speech_val and not gem_val:
            status_lbl.configure(text="Please enter at least one API key.")
            return

          ok_speech = False
          ok_gem = False
          try:
            if speech_val:
              ok_speech = set_speechmatics_api_key(speech_val)
              self.api_key = speech_val
          except Exception:
            ok_speech = False
            self.api_key = speech_val

          try:
            if gem_val:
              ok_gem = set_gemini_api_key(gem_val)
              self.gemini_api_key = gem_val
          except Exception:
            ok_gem = False
            self.gemini_api_key = gem_val

          was_active = self.mode != AppMode.IDLE and self.agent is not None and self.agent.is_running()
          if was_active:
            try:
              self._stop_agent()
            except Exception:
              pass
            try:
              self._start_agent()
            except Exception:
              pass
          else:
            self.agent = None

          try:
            self.icon.update_menu()
          except Exception:
            pass

          msgs = []
          if speech_val:
            msgs.append("Speechmatics key saved" if ok_speech else "Speechmatics key set for session (keyring save failed)")
          if gem_val:
            msgs.append("Gemini key saved" if ok_gem else "Gemini key set for session (keyring save failed)")
          try:
            if msgs:
              self.icon.notify("; ".join(msgs))
          except Exception:
            pass
          _finish()

        def _cancel():
          _finish()

        ttk.Button(btn_frame, text="Save", command=_save).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btn_frame, text="Cancel", command=_cancel).grid(row=0, column=1)

        entry.bind("<Return>", lambda e: _save())
        gem_entry.bind("<Return>", lambda e: _save())
        dlg.bind("<Escape>", lambda e: _cancel())
        dlg.protocol("WM_DELETE_WINDOW", _cancel)

        dlg.update_idletasks()
        w = dlg.winfo_reqwidth()
        h = dlg.winfo_reqheight()
        x = (dlg.winfo_screenwidth() - w) // 2
        y = (dlg.winfo_screenheight() - h) // 3
        dlg.geometry(f"+{x}+{y}")

        dlg.deiconify()
        dlg.lift()
        dlg.focus_force()
        entry.focus_set()

        root.mainloop()
        root.destroy()
      except Exception as e:
        print("API key prompt error:", e)
    threading.Thread(target=_prompt, daemon=True).start()

  def _open_meeting_file(self):
    self.meetings_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    self._meeting_path = self.meetings_dir / f"meeting_{ts}.txt"
    self._meeting_file = open(self._meeting_path, "a", encoding="utf-8")
    self._meeting_file.write(f"=== Meeting started {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    self._meeting_file.flush()
    print(f"Meeting transcript: {self._meeting_path}")
    # Optionally start recording system audio to a WAV file alongside the
    # transcript so meetings include speaker output (e.g., remote participants).
    try:
      # Prepare a system audio file path next to the transcript
      system_path = self.meetings_dir / f"meeting_{ts}_system.wav"
      self._system_recorder = SystemAudioRecorder(system_path)
      if self.record_system_audio:
        try:
          started = self._system_recorder.start()
          if started:
            try:
              self.icon.notify("Recording system audio for meeting")
            except Exception:
              pass
        except Exception:
          print("Failed to start system audio recorder")
    except Exception:
      pass

  def _close_meeting_file(self):
    if self._meeting_file:
      try:
        self._meeting_file.write(f"=== Meeting ended {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        self._meeting_file.close()
      except Exception:
        pass
      self._meeting_file = None
    # Stop system audio recorder if active
    if getattr(self, '_system_recorder', None):
      try:
        self._system_recorder.stop()
      except Exception:
        pass
      self._system_recorder = None

  # --------------------------------------------------------------------- run -

  def run(self):
    self._hotkey_dictation.start()
    self._hotkey_meeting.start()
    self._hotkey_command.start()

    self.icon.menu = self._build_menu()
    self._icon_thread = threading.Thread(target=self.icon.run, daemon=True)
    self._icon_thread.start()
    # Notify user on startup if no API key is configured
    if not self.api_key:
      try:
        self.icon.notify("Warning: Speechmatics API key not found. Configure via the tray menu.")
      except Exception:
        pass

    try:
      while self._icon_thread.is_alive():
        time.sleep(0.5)
    except KeyboardInterrupt:
      self.shutdown()

  def shutdown(self):
    print("Shutting down Command tray app")
    for hk in (self._hotkey_dictation, self._hotkey_meeting, self._hotkey_command):
      try:
        hk.stop()
      except Exception:
        pass

    if self.mode != AppMode.IDLE:
      try:
        self._deactivate()
      except Exception:
        pass

    try:
      self.icon.stop()
    except Exception:
      pass

    time.sleep(0.1)
