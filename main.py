""" The Command Runtime """
from commander.tray_app import TrayApp


def main() -> None:
  app = TrayApp()
  app.run()


if __name__ == "__main__":
  main()
