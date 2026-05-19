import sys
import os
import ctypes
import ctypes.wintypes
from pathlib import Path
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


def get_default_meetings_dir() -> Path:
  """Return the platform-appropriate default directory for meeting transcripts.

  Windows : ~/Documents/Command/Meetings
  macOS   : ~/Documents/Command/Meetings
  Linux   : ~/Documents/Command/Meetings  (falls back to ~/Command/Meetings)
  """
  if sys.platform == "win32":
    # Use SHGetFolderPath to get the real My Documents path (handles redirection)
    try:
      buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
      # CSIDL_PERSONAL = 0x0005
      ctypes.windll.shell32.SHGetFolderPathW(None, 0x0005, None, 0, buf)
      docs = Path(buf.value)
    except Exception:
      docs = Path.home() / "Documents"
  else:
    docs = Path.home() / "Documents"
    if not docs.exists():
      docs = Path.home()  # Linux fallback when ~/Documents doesn't exist

  return docs / "Command" / "Meetings"


def get_speechmatics_api_key() -> Optional[str]:
  """Return the stored Speechmatics API key.

  First attempts to read from the OS keyring (via the `keyring` package).
  If that fails or no key is stored, fall back to the `SPEECHMATICS_API_KEY`
  environment variable. Returns None if no key is available.
  """
  # Try keyring first (preferred, OS-native secure storage)
  try:
    import keyring
    key = keyring.get_password("commander-command", "speechmatics_api_key")
    if key:
      return key
  except Exception:
    # keyring not available or failed — fall through to environment var
    pass

  # Fallback to environment variable
  return os.environ.get("SPEECHMATICS_API_KEY")


def set_speechmatics_api_key(key: str) -> bool:
  """Store the Speechmatics API key in the OS keyring.

  Returns True on success, False on failure.
  """
  try:
    import keyring
    keyring.set_password("ancilla-command", "speechmatics_api_key", key)
    return True
  except Exception as e:
    print("set_speechmatics_api_key error:", e)
    return False


def delete_speechmatics_api_key() -> bool:
  """Remove the stored Speechmatics API key from the OS keyring.

  Returns True on success, False on failure or when keyring isn't available.
  """
  try:
    import keyring
    keyring.delete_password("ancilla-command", "speechmatics_api_key")
    return True
  except Exception as e:
    print("delete_speechmatics_api_key error:", e)
    return False


def get_gemini_api_key() -> Optional[str]:
  """Return the stored Gemini/Google API key.

  Tries the OS keyring first (preferred). Falls back to the environment
  variables `GOOGLE_API_KEY` or `GEMINI_API_KEY`.
  """
  try:
    import keyring
    key = keyring.get_password("ancilla-command", "gemini_api_key")
    if key:
      return key
  except Exception:
    pass

  return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def set_gemini_api_key(key: str) -> bool:
  """Store the Gemini/Google API key in the OS keyring. Returns True on success."""
  try:
    import keyring
    keyring.set_password("ancilla-command", "gemini_api_key", key)
    return True
  except Exception as e:
    print("set_gemini_api_key error:", e)
    return False


def delete_gemini_api_key() -> bool:
  """Remove the stored Gemini key from the OS keyring."""
  try:
    import keyring
    keyring.delete_password("ancilla-command", "gemini_api_key")
    return True
  except Exception as e:
    print("delete_gemini_api_key error:", e)
    return False

