import os
import time
import pystray
import threading
import pyautogui
from enum import Enum
from pathlib import Path
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
)


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
    self.icon.title = "Command"
    self.input_devices = get_input_devices()
    self.api_key = get_speechmatics_api_key()
    self._icon_thread: threading.Thread = None
    self.meetings_dir: Path = get_default_meetings_dir()

    self.device_index = device_index if device_index is not None else get_default_input_device_index()
    self.agent = SpeechmaticsAgent(api_key=self.api_key, on_final_segment=self._on_final_segment, device_index=self.device_index)

    self._hotkey_meeting = DoubleKeyListener(key="f11", callback=lambda: self._toggle_mode(AppMode.MEETING, from_hotkey=True))
    self._hotkey_command = DoubleKeyListener(key="f10", callback=lambda: self._toggle_mode(AppMode.COMMAND, from_hotkey=True))
    self._hotkey_dictation = DoubleKeyListener(key="f12", callback=lambda: self._toggle_mode(AppMode.DICTATION, from_hotkey=True))

  def _create_icon(self) -> pystray.Icon:
    image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.ellipse((8, 8, 56, 56), fill=(30, 144, 255, 255))
    d.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 255))
    icon = pystray.Icon("Command", image)
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
    api_status = "configured" if self.api_key else "Not configured"

    return pystray.Menu(
      Item('Dictation  (double F12)', lambda icon, item: self._toggle_mode(AppMode.DICTATION), checked=lambda item: self.mode == AppMode.DICTATION),
      Item('Meeting    (double F11)', lambda icon, item: self._toggle_mode(AppMode.MEETING), checked=lambda item: self.mode == AppMode.MEETING),
      Item('Command    (double F10)', lambda icon, item: self._toggle_mode(AppMode.COMMAND), checked=lambda item: self.mode == AppMode.COMMAND),
      pystray.Menu.SEPARATOR,
      Item('Input Device', pystray.Menu(*device_items)),
      pystray.Menu.SEPARATOR,
      Item(f'API Key: {api_status}', None, enabled=False),
      Item('Set Speechmatics API Key…', lambda icon, item: self._prompt_for_api_key()),
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
    self.agent = SpeechmaticsAgent(api_key=self.api_key, on_final_segment=self._on_final_segment, device_index=self.device_index)
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
    if not self.agent:
      self.agent = SpeechmaticsAgent(api_key=self.api_key, on_final_segment=self._on_final_segment, device_index=self.device_index)
    self.agent.start()

  def _stop_agent(self):
    self.agent.stop()
    # Re-create the agent so it can be started fresh next time
    self.agent = SpeechmaticsAgent(api_key=self.api_key, on_final_segment=self._on_final_segment, device_index=self.device_index)

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
    # Placeholder — wire up a desktop automation agent here
    print(f"[command] Received: {text}")

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
    """Open a modern ttk dialog to prompt for the Speechmatics API key and store it in the OS keyring."""
    def _prompt():
      try:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        root.withdraw()

        # Dialog window
        dlg = tk.Toplevel(root)
        dlg.title("Speechmatics API Key")
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

        ttk.Label(frame, text="Enter Speechmatics API Key:").grid(row=0, column=0, columnspan=2, sticky="w")

        key_var = tk.StringVar()
        entry = ttk.Entry(frame, width=44, textvariable=key_var, show="*")
        entry.grid(row=1, column=0, columnspan=2, pady=(8, 4), sticky="ew")

        show_var = tk.BooleanVar(value=False)
        def _toggle_show():
          entry.configure(show="" if show_var.get() else "*")
        ttk.Checkbutton(frame, text="Show key", variable=show_var, command=_toggle_show).grid(row=2, column=0, sticky="w")

        status_lbl = ttk.Label(frame, text="", foreground="#c00")
        status_lbl.grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=4, column=0, columnspan=2, pady=(12, 0), sticky="e")

        def _finish():
          """Exit the event loop and clean up."""
          root.quit()

        def _save():
          val = key_var.get().strip()
          if not val:
            status_lbl.configure(text="Please enter an API key.")
            return
          try:
            ok = set_speechmatics_api_key(val)
            self.api_key = val
          except Exception:
            ok = False
            self.api_key = val

          was_active = self.mode != AppMode.IDLE and hasattr(self.agent, "is_running") and self.agent.is_running()
          if was_active:
            try:
              self._stop_agent()
            except Exception:
              pass
            self.agent = SpeechmaticsAgent(api_key=self.api_key, on_final_segment=self._on_final_segment, device_index=self.device_index)
            try:
              self._start_agent()
            except Exception:
              pass
          else:
            self.agent = SpeechmaticsAgent(api_key=self.api_key, on_final_segment=self._on_final_segment, device_index=self.device_index)

          try:
            self.icon.update_menu()
          except Exception:
            pass
          try:
            self.icon.notify("Speechmatics API key saved" if ok else "API key set for session (keyring save failed)")
          except Exception:
            pass
          _finish()

        def _cancel():
          _finish()

        ttk.Button(btn_frame, text="Save", command=_save).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(btn_frame, text="Cancel", command=_cancel).grid(row=0, column=1)

        entry.bind("<Return>", lambda e: _save())
        dlg.bind("<Escape>", lambda e: _cancel())
        dlg.protocol("WM_DELETE_WINDOW", _cancel)

        # Center on screen before showing
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

        # Run the event loop — blocks until _finish() calls root.quit()
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

  def _close_meeting_file(self):
    if self._meeting_file:
      try:
        self._meeting_file.write(f"=== Meeting ended {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        self._meeting_file.close()
      except Exception:
        pass
      self._meeting_file = None

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
