import os
import time
import pystray
import threading
import pyautogui
from enum import Enum
from datetime import datetime
from PIL import Image, ImageDraw
from pystray import MenuItem as Item

from hotkey import DoubleKeyListener
from speech_client import SpeechmaticsAgent
from utils import caret_available, get_input_devices, get_default_input_device_index


class AppMode(Enum):
  IDLE = "idle"
  DICTATION = "dictation"
  MEETING = "meeting"
  COMMAND = "command"


class TrayApp:
  def __init__(self, device_index: int = None):
    self.icon = self._create_icon()
    self.icon.title = "Command"
    self.mode = AppMode.IDLE
    self._icon_thread: threading.Thread = None
    self.input_devices = get_input_devices()
    self.device_index = device_index if device_index is not None else get_default_input_device_index()
    self.agent = SpeechmaticsAgent(on_final_segment=self._on_final_segment, device_index=self.device_index)
    self._meeting_file = None
    self._meeting_path = None

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
    return pystray.Menu(
      Item('Dictation  (double F12)', lambda icon, item: self._toggle_mode(AppMode.DICTATION), checked=lambda item: self.mode == AppMode.DICTATION),
      Item('Meeting    (double F11)', lambda icon, item: self._toggle_mode(AppMode.MEETING), checked=lambda item: self.mode == AppMode.MEETING),
      Item('Command    (double F10)', lambda icon, item: self._toggle_mode(AppMode.COMMAND), checked=lambda item: self.mode == AppMode.COMMAND),
      pystray.Menu.SEPARATOR,
      Item('Input Device', pystray.Menu(*device_items)),
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
    self.agent = SpeechmaticsAgent(on_final_segment=self._on_final_segment, device_index=self.device_index)
    if active_mode != AppMode.IDLE:
      self._activate(active_mode)

  # ------------------------------------------------------------------ modes --

  def _toggle_mode(self, mode: AppMode, from_hotkey: bool = False):
    if self.mode == mode:
      self._deactivate(from_hotkey=from_hotkey)
    else:
      self._activate(mode, from_hotkey=from_hotkey)

  def _activate(self, mode: AppMode, from_hotkey: bool = False):
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
    self.agent.start()

  def _stop_agent(self):
    self.agent.stop()
    # Re-create the agent so it can be started fresh next time
    self.agent = SpeechmaticsAgent(on_final_segment=self._on_final_segment, device_index=self.device_index)

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

  def _open_meeting_file(self):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    self._meeting_path = os.path.join(os.path.expanduser("~"), f"meeting_{ts}.txt")
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
