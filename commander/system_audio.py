import time
import threading
import wave
from pathlib import Path
from typing import Optional


class SystemAudioRecorder:
  """Record system (speaker) audio to a WAV file.

  Attempts to use `sounddevice` with WASAPI loopback on Windows. Falls back
  to a named "Stereo Mix"/loopback input via PyAudio if available. If
  neither backend is available the recorder will create an empty WAV file
  so callers can rely on the path existing.
  """

  def __init__(self, path: Path, sample_rate: int = 48000, channels: int = 2, chunk: int = 1024):
    self.path = Path(path)
    self.sample_rate = int(sample_rate)
    self.channels = int(channels)
    self.chunk = int(chunk)
    self._thread: Optional[threading.Thread] = None
    self._stop = threading.Event()

  def start(self) -> bool:
    if self._thread and self._thread.is_alive():
      return False
    self._stop.clear()
    self._thread = threading.Thread(target=self._run, daemon=True)
    self._thread.start()
    return True

  def stop(self) -> None:
    self._stop.set()
    if self._thread:
      self._thread.join(timeout=3.0)
    self._thread = None

  def _run(self) -> None:
    # Ensure parent directory exists
    try:
      self.path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
      pass

    # Try sounddevice + WASAPI loopback first (best on modern Windows)
    try:
      import sounddevice as sd

      try:
        out_dev = None
        # sd.default.device may be (in, out) tuple or single value
        try:
          dev = sd.default.device
          if isinstance(dev, (tuple, list)) and len(dev) >= 2:
            out_dev = dev[1]
          elif isinstance(dev, int):
            out_dev = dev
        except Exception:
          out_dev = None

        if out_dev is None or out_dev < 0:
          # Find first device with output channels
          for i, d in enumerate(sd.query_devices()):
            if d.get('max_output_channels', 0) > 0:
              out_dev = i
              break

        wf = wave.open(str(self.path), 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(2)
        wf.setframerate(self.sample_rate)

        try:
          wasapi = getattr(sd, 'WasapiSettings', None)
          extra = wasapi(loopback=True) if wasapi is not None else None

          def callback(indata, frames, time_info, status):
            try:
              # indata may be numpy array (RawInputStream) or bytes - handle both
              if hasattr(indata, 'tobytes'):
                wf.writeframes(indata.tobytes())
              else:
                wf.writeframes(indata)
            except Exception:
              pass

          # Use RawInputStream so we can set WASAPI loopback via extra_settings
          stream = sd.RawInputStream(samplerate=self.sample_rate, blocksize=self.chunk, dtype='int16', channels=self.channels, callback=callback, device=out_dev, extra_settings=extra)
          stream.start()
          while not self._stop.is_set():
            time.sleep(0.1)
          try:
            stream.stop()
            stream.close()
          except Exception:
            pass
        finally:
          try:
            wf.close()
          except Exception:
            pass

        return
      except Exception:
        # sounddevice failed — fall through to PyAudio fallback
        pass
    except Exception:
      pass

    # PyAudio fallback: look for 'stereo mix' / 'what u hear' / 'loopback' devices
    try:
      import pyaudio
      pa = pyaudio.PyAudio()
      idx = None
      for i in range(pa.get_device_count()):
        try:
          info = pa.get_device_info_by_index(i)
          name = (info.get('name') or '').lower()
          if 'stereo mix' in name or 'what u hear' in name or 'loopback' in name:
            idx = int(info['index'])
            break
        except Exception:
          continue

      if idx is not None:
        wf = wave.open(str(self.path), 'wb')
        wf.setnchannels(self.channels)
        wf.setsampwidth(2)
        wf.setframerate(self.sample_rate)
        try:
          stream = pa.open(format=pyaudio.paInt16, channels=self.channels, rate=self.sample_rate, input=True, frames_per_buffer=self.chunk, input_device_index=idx)
          while not self._stop.is_set():
            try:
              data = stream.read(self.chunk, exception_on_overflow=False)
              wf.writeframes(data)
            except Exception:
              time.sleep(0.05)
          try:
            stream.stop_stream()
            stream.close()
          except Exception:
            pass
        finally:
          try:
            wf.close()
          except Exception:
            pass
        try:
          pa.terminate()
        except Exception:
          pass
        return
    except Exception:
      pass

    # As a last resort write a tiny empty WAV so callers have a file to reference
    try:
      wf = wave.open(str(self.path), 'wb')
      wf.setnchannels(1)
      wf.setsampwidth(2)
      wf.setframerate(16000)
      wf.close()
    except Exception:
      pass
