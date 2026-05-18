"""Command — background voice agent for desktop."""

__all__ = ["TrayApp", "AppMode"]


def __getattr__(name):
  if name in __all__:
    from .tray_app import TrayApp, AppMode  # noqa: F401
    return {"TrayApp": TrayApp, "AppMode": AppMode}[name]
  raise AttributeError(f"module 'command' has no attribute {name!r}")
