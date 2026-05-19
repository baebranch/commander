"""Control agent that converts natural language commands into GUI actions.

Uses an LLM (Gemini SDK if available) to plan a sequence of GUI actions and
executes them with `pyautogui`.

This module is written to be defensive: the Gemini SDK and `pydantic` are
optional. If they are not installed the agent will still attempt to parse
JSON returned by the model. The agent restricts allowed actions to a safe
subset (mouse/keyboard/wait/scroll) and rejects shell/file-destructive ops.

Usage:
  from command.control_agent import ControlAgent
  agent = ControlAgent(dry_run=True)  # dry_run=True to see plans without executing
  agent.execute_text("Open calculator and type 123")
"""

from __future__ import annotations

import json
import os
import time
import threading
from typing import Any, Dict, List, Optional

import pyautogui

# Allowed action types and their minimal required fields
_ALLOWED_ACTIONS = {
  "click": (),          # optional: x, y, button, clicks
  "move": (),           # required: x, y
  "press": (),          # required: key
  "hotkey": (),         # required: keys (list)
  "type": (),           # required: text
  "write": (),          # alias for type
  "wait": (),           # required: seconds
  "scroll": (),         # required: clicks or amount
  "screenshot": (),     # optional: path
}


class ControlAgent:
  def __init__(self, dry_run: bool = False, gemini_model: str = "gemini-1.0"):
    self.dry_run = dry_run
    self.gemini_model = gemini_model
    # Optional injected LLM function for easier testing
    self._llm_fn = None

  def set_llm(self, fn):
    """Set a custom llm function: fn(prompt: str) -> str (raw text response)."""
    self._llm_fn = fn

  def _query_llm(self, prompt: str) -> str:
    """Query Gemini SDK (if available) or raise an informative error.

    The implementation is lazy so importing the gemini SDK is optional.
    """
    if self._llm_fn:
      return self._llm_fn(prompt)

    # Try Google generative AI client (gemini)
    try:
      try:
        import google.generativeai as genai
      except Exception:
        # older packaging or different import path
        import google.generativeai as genai
      api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
      if not api_key:
        raise RuntimeError("Gemini API key not found in GOOGLE_API_KEY or GEMINI_API_KEY environment variables")
      genai.configure(api_key=api_key)
      # generate_text is the high-level helper; fall back if not present
      if hasattr(genai, "generate_text"):
        resp = genai.generate_text(model=self.gemini_model, prompt=prompt)
        # response shape may vary between SDK versions
        text = getattr(resp, "text", None) or getattr(resp, "output", None) or str(resp)
        return text
      elif hasattr(genai, "client") and hasattr(genai.client, "generate_text"):
        resp = genai.client.generate_text(model=self.gemini_model, prompt=prompt)
        text = getattr(resp, "text", None) or getattr(resp, "output", None) or str(resp)
        return text
      else:
        raise RuntimeError("Installed google.generativeai package does not expose a known generate_text API")
    except Exception as e:
      raise RuntimeError("Gemini SDK not available or failed: %s" % e)

  def _build_prompt(self, user_text: str) -> str:
    """Construct a prompt instructing the model to output JSON actions.

    The model is asked to return strict JSON with a single top-level object
    containing `actions: [...]`. Each action is an object with a `type` and
    action-specific fields. Only a small, safe action vocabulary is allowed.
    """
    examples = json.dumps({
      "actions": [
        {"type": "click", "x": 100, "y": 200},
        {"type": "wait", "seconds": 0.5},
        {"type": "type", "text": "Hello"}
      ]
    }, indent=2)

    prompt = f"Convert the following natural-language instruction into a JSON object with a single field `actions`, an array of steps."
    prompt += "\nOnly use these action types and their parameters:\n"
    prompt += "- click: {x:int, y:int, button:optional('left'|'right'|'middle'), clicks:optional(int)}\n"
    prompt += "- move: {x:int, y:int}  (absolute screen coordinates)\n"
    prompt += "- press: {key:str}  (single key press)\n"
    prompt += "- hotkey: {keys:[str]}  (chord of keys, e.g. ['ctrl','c'])\n"
    prompt += "- type / write: {text:str}  (type the given text using keyboard)\n"
    prompt += "- wait: {seconds:float}  (pause)\n"
    prompt += "- scroll: {amount:int}  (positive=up, negative=down)\n"
    prompt += "- screenshot: {path:optional str}  (take a screenshot)\n"
    prompt += "\nDo NOT output any shell commands, or any file-deleting/formatting operations, or arbitrary code execution. If the instruction would require such an action, return an empty `actions` array and include an `explain` field explaining why."
    prompt += "\nRespond with valid JSON only. Example response:\n"
    prompt += examples + "\n"
    prompt += "\nNow convert the following instruction into JSON actions:\n" + json.dumps({"instruction": user_text})
    return prompt

  def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from the model text and return parsed dict."""
    try:
      # Quick path: try full text
      return json.loads(text)
    except Exception:
      # Find first { ... } block
      start = text.find("{")
      end = text.rfind("}")
      if start == -1 or end == -1 or end <= start:
        return None
      try:
        return json.loads(text[start:end+1])
      except Exception:
        return None

  def _validate_actions(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    actions = payload.get("actions") or []
    valid: List[Dict[str, Any]] = []
    for a in actions:
      if not isinstance(a, dict):
        continue
      t = a.get("type")
      if not t or t not in _ALLOWED_ACTIONS:
        continue
      valid.append(a)
    return valid

  def plan(self, instruction: str) -> Dict[str, Any]:
    """Ask Gemini to plan actions for the given instruction and return parsed JSON."""
    prompt = self._build_prompt(instruction)
    text = self._query_llm(prompt)
    payload = self._extract_json(text)
    if not payload:
      raise RuntimeError("LLM did not return valid JSON")
    payload_actions = self._validate_actions(payload)
    return {"actions": payload_actions, "raw": payload}

  def _exec_action(self, a: Dict[str, Any]):
    t = a.get("type")
    try:
      if t == "click":
        x = a.get("x")
        y = a.get("y")
        clicks = int(a.get("clicks", 1))
        button = a.get("button", "left")
        if x is None or y is None:
          pyautogui.click(button=button, clicks=clicks)
        else:
          pyautogui.click(x=int(x), y=int(y), clicks=clicks, button=button)
      elif t == "move":
        pyautogui.moveTo(int(a["x"]), int(a["y"]))
      elif t == "press":
        pyautogui.press(str(a["key"]))
      elif t == "hotkey":
        keys = a.get("keys") or []
        if isinstance(keys, list):
          pyautogui.hotkey(*[str(k) for k in keys])
      elif t in ("type", "write"):
        txt = str(a.get("text", ""))
        pyautogui.write(txt, interval=0.02)
      elif t == "wait":
        secs = float(a.get("seconds", 0.0))
        time.sleep(secs)
      elif t == "scroll":
        amt = int(a.get("amount", a.get("clicks", 0)))
        pyautogui.scroll(amt)
      elif t == "screenshot":
        path = a.get("path") or f"screenshot_{int(time.time())}.png"
        pyautogui.screenshot(path)
      else:
        raise RuntimeError(f"Unsupported action type: {t}")
      return {"ok": True}
    except Exception as e:
      return {"ok": False, "error": str(e)}

  def execute_text(self, instruction: str) -> Dict[str, Any]:
    """Plan and execute the actions for a natural language instruction.

    Returns a dict with keys: `planned` (list), `results` (list) and `raw`.
    If `dry_run` is True the actions are returned but not executed.
    """
    plan = self.plan(instruction)
    actions = plan.get("actions", [])
    results: List[Dict[str, Any]] = []
    if not actions:
      return {"planned": actions, "results": results, "raw": plan.get("raw")}

    for a in actions:
      if self.dry_run:
        results.append({"action": a, "result": "dry-run"})
        continue
      res = self._exec_action(a)
      results.append({"action": a, "result": res})
    return {"planned": actions, "results": results, "raw": plan.get("raw")}
