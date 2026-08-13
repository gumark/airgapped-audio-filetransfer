"""Launch one local control plane per computer.

The explicit 127.0.0.1 bind is a security boundary: browsers control the
process locally, while all file bytes leave only through the audio device.
"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser

import uvicorn

from backend.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Air-gapped audio file transfer")
    parser.add_argument("--mode", choices=("transmitter", "receiver"), required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    app = create_app(args.mode)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/?mode={args.mode}")).start()
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="info")


if __name__ == "__main__":
    main()
