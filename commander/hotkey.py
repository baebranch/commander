import time
from pynput import keyboard


class DoubleKeyListener:
  """ Listen for a double-press of a specific key and call a callback.

      Default key names follow pynput's naming (e.g. 'f12').
  """

  def __init__(self, key: str = "f12", callback=None, interval: float = 0.45):
    self.key = key
    self._last_time = 0.0
    self._running = False
    self.callback = callback
    self.interval = interval
    self._listener = keyboard.Listener(on_press=self._on_press)

  def _on_press(self, key):
    try:
      key_str = key.name if hasattr(key, "name") else key.char
    except Exception:
      key_str = str(key)

    if key_str == self.key:
      now = time.time()
      if now - self._last_time <= self.interval:
        # Detected double press
        if self.callback:
          try:
            self.callback()
          except Exception as e:
            print("Hotkey callback error:", e)
        self._last_time = 0.0
      else:
        self._last_time = now

  def start(self):
    if self._running:
      return
    self._running = True
    self._listener.start()

  def stop(self):
    self._running = False
    try:
      self._listener.stop()
    except Exception:
      pass
