"""Launch the ZendAgent desktop supervisor.

    .venv\\Scripts\\pythonw.exe -m launcher
"""

import argparse

from launcher.app import main


def run() -> None:
    parser = argparse.ArgumentParser(description="ZendAgent desktop launcher")
    parser.add_argument("--screenshot", help="Save a PNG of the window and exit. Does not start or stop services.")
    args = parser.parse_args()
    main(screenshot=args.screenshot)


if __name__ == "__main__":
    run()
