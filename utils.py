import ctypes
from typing import List, Optional, Tuple


# Keywords that identify non-physical / loopback input devices to exclude from
# the user-facing device list.
_EXCLUDED_DEVICE_KEYWORDS = (
  "stereo mix",
  "what u hear",
  "loopback",
  "virtual",
  "output",
  "wave out",
  "wavout",
  "soundboard",
)


def get_input_devices() -> List[Tuple[int, str]]:
  """Return (index, name) pairs for usable physical input devices.

  Only devices from the system's default host API are included so that the
  same physical microphone is not listed multiple times (MME, WASAPI, etc.).
  Virtual and loopback devices are also excluded.
  """
  try:
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
      default_host = pa.get_default_host_api_info()["index"]
      devices: List[Tuple[int, str]] = []
      for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)
        if info["hostApi"] != default_host:
          continue
        if info["maxInputChannels"] < 1:
          continue
        name_lower = info["name"].lower()
        if any(kw in name_lower for kw in _EXCLUDED_DEVICE_KEYWORDS):
          continue
        devices.append((int(info["index"]), info["name"]))
      return devices
    finally:
      pa.terminate()
  except Exception as e:
    print("get_input_devices error:", e)
    return []


def get_default_input_device_index() -> Optional[int]:
  """Return the PyAudio index of the system's default input device, or None."""
  try:
    import pyaudio
    pa = pyaudio.PyAudio()
    try:
      return int(pa.get_default_input_device_info()["index"])
    finally:
      pa.terminate()
  except Exception as e:
    print("get_default_input_device_index error:", e)
    return None


def caret_available() -> bool:
  """ Return True if a focused text input is available in the foreground window.

      Queries the foreground window's thread so the check targets the app the user
      is interacting with, not this process.  hwndCaret covers classic Win32 edit
      controls; hwndFocus covers modern/custom-rendered controls (browsers,
      Electron) that draw their own cursor without using the Windows caret API.
  """
  try:
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
      _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class GUITHREADINFO(ctypes.Structure):
      _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", RECT),
      ]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
      return False

    tid = user32.GetWindowThreadProcessId(hwnd, None)

    gui = GUITHREADINFO()
    gui.cbSize = ctypes.sizeof(gui)
    if not user32.GetGUIThreadInfo(tid, ctypes.byref(gui)):
      return False

    # hwndCaret: classic Win32 caret; hwndFocus: browsers/Electron/modern apps
    return bool(gui.hwndCaret or gui.hwndFocus)
  except Exception as e:
    print("caret_available check failed:", e)
    return False
