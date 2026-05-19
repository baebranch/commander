import os
import asyncio
import threading
from typing import Callable, Optional
from speechmatics.rt import Microphone
from speechmatics.voice import (
  VoiceAgentClient,
  VoiceAgentConfig,
  VoiceAgentConfigPreset,
  AgentServerMessageType,
  EndOfUtteranceMode,
  SpeechSegmentConfig,
  AdditionalVocabEntry
)


class SpeechmaticsAgent:
  """ Wrapper around Speechmatics Voice SDK.

      This will attempt to use the `speechmatics.voice` and `speechmatics.rt`
      packages if available. The agent runs in a background thread and calls
      `on_final_segment(text)` for finalized segments.
  """

  def __init__(self, api_key: Optional[str] = None, on_final_segment: Optional[Callable] = None, mode: str = "dictation", sample_rate: int = 16000, chunk_size: int = 160, device_index: Optional[int] = None):
    self.api_key = api_key or os.getenv("SPEECHMATICS_API_KEY")
    self.on_final_segment = on_final_segment
    self.mode = mode
    self.sample_rate = sample_rate
    self.chunk_size = chunk_size
    self.device_index = device_index

    self._running = False
    self._stop_flag = False
    self._thread: Optional[threading.Thread] = None
    self._stop_event: Optional[asyncio.Event] = None
    self._loop: Optional[asyncio.AbstractEventLoop] = None

  def start(self):
    if self._running:
      return
    self._stop_flag = False
    self._thread = threading.Thread(target=self._thread_main, daemon=True)
    self._thread.start()

  def stop(self):
    self._stop_flag = True
    if self._loop and self._stop_event:
      self._loop.call_soon_threadsafe(self._stop_event.set)
    if self._thread and self._thread.is_alive():
      self._thread.join(timeout=3.0)
    self._loop = None
    self._stop_event = None

  def is_running(self) -> bool:
    return self._running

  def _thread_main(self):
    try:
      asyncio.run(self._run())
    except Exception as e:
      print("SpeechmaticsAgent runtime error:", e)

  def _build_config(self, VoiceAgentConfig, VoiceAgentConfigPreset, EndOfUtteranceMode):
    """Return a VoiceAgentConfig appropriate for the current mode.

    - dictation : SCRIBE preset — note-taking optimised segmentation.
    - meeting   : SMART_TURN preset (ADAPTIVE + ML turn detection) with
                  diarization enabled so segments carry a speaker_id.  Falls
                  back to plain ADAPTIVE when speechmatics-voice[smart] is not
                  installed.
    - command   : ADAPTIVE preset — natural-conversation turn detection.
    """
    # Overrides shared by every mode
    base = dict(sample_rate=self.sample_rate, include_partials=False)

    if self.mode == "meeting":
      overrides = VoiceAgentConfig(
        **base,
        enable_diarization=True
      )
      try:
        from speechmatics.voice import SmartTurnConfig
        return VoiceAgentConfigPreset.SMART_TURN(overrides)
      except ImportError:
        print("[Command] speechmatics-voice[smart] not installed — falling back to ADAPTIVE turn detection.")
        print("          Run: pip install speechmatics-voice[smart]  for ML-based turn detection.")
        overrides2 = VoiceAgentConfig(
          **base,
          enable_diarization=True,
          end_of_utterance_mode=EndOfUtteranceMode.ADAPTIVE,
        )
        return VoiceAgentConfigPreset.ADAPTIVE(overrides2)

    elif self.mode == "dictation":
      # SCRIBE preset is purpose-built for note-taking / dictation
      return VoiceAgentConfigPreset.SCRIBE(
        VoiceAgentConfig(
          **base
        )
      )

    else:  # command — conversational, adaptive turn detection
      overrides = VoiceAgentConfig(
        **base,
        end_of_utterance_mode=EndOfUtteranceMode.ADAPTIVE,
        additional_vocab=[
          SpeechSegmentConfig(
            emit_sentences=True
          )
        ]
      )
      return VoiceAgentConfigPreset.ADAPTIVE(overrides)

  async def _run(self):
    if not self.api_key:
      print("SPEECHMATICS_API_KEY not set; Speechmatics client will not start.")
      return

    self._stop_event = asyncio.Event()
    self._loop = asyncio.get_running_loop()

    config = self._build_config(VoiceAgentConfig, VoiceAgentConfigPreset, EndOfUtteranceMode)
    client = VoiceAgentClient(api_key=self.api_key, config=config)

    @client.on(AgentServerMessageType.ADD_SEGMENT)
    def _on_segment(message):
      for segment in message.get("segments", []):
        text = (segment.get("text") or "").strip()
        if not text:
          continue
        # For meeting mode, prepend the speaker label so the transcript is readable
        if self.mode == "meeting":
          speaker = segment.get("speaker_id", "")
          if speaker:
            text = f"{speaker}: {text}"
        if self.on_final_segment:
          # Dispatch in a daemon thread — on_final_segment may call blocking
          # GUI APIs (pyautogui) that must not run on the event loop thread.
          t = threading.Thread(target=self._invoke_callback, args=(text,), daemon=True)
          t.start()

    mic_kwargs = {"sample_rate": self.sample_rate, "chunk_size": self.chunk_size}
    if self.device_index is not None:
      mic_kwargs["device_index"] = self.device_index
    mic = Microphone(**mic_kwargs)
    if not mic.start():
      print("Error: Microphone not available or failed to start")
      return

    self._running = True
    try:
      await client.connect()
    except Exception as e:
      print("Failed to connect to Speechmatics Voice Agent:", e)
      self._running = False
      return

    try:
      while not self._stop_event.is_set():
        read_task = asyncio.create_task(mic.read(self.chunk_size))
        stop_task = asyncio.create_task(self._stop_event.wait())
        done, pending = await asyncio.wait(
          {read_task, stop_task},
          return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
          task.cancel()
          try:
            await task
          except asyncio.CancelledError:
            pass
        if stop_task in done:
          break
        if read_task in done:
          audio_chunk = read_task.result()
          if not audio_chunk:
            break
          try:
            await client.send_audio(audio_chunk)
          except Exception as e:
            print("Error sending audio chunk:", e)
    except Exception as e:
      print("Speechmatics streaming error:", e)
    finally:
      try:
        await client.disconnect()
      except Exception:
        pass
      self._running = False

  def _invoke_callback(self, text: str):
    try:
      self.on_final_segment(text)
    except Exception as e:
      print("on_final_segment callback error:", e)
