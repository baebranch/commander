"""Entry point for `python -m command`."""

from command.tray_app import TrayApp


def main() -> None:
  app = TrayApp()
  app.run()


if __name__ == "__main__":
  main()
