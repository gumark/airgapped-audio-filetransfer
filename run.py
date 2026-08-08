#!/usr/bin/env python3
"""
Air-Gapped Audio File Transfer System

Main entry point for the application.
Usage:
    python run.py --mode transmitter
    python run.py --mode receiver

Opens a local web browser with the appropriate GUI.
"""

import sys
import argparse
import webbrowser
import threading
import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="Air-Gapped Audio File Transfer System"
    )
    parser.add_argument(
        "--mode",
        choices=["transmitter", "receiver"],
        required=True,
        help="Run as transmitter or receiver"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port to listen on (default: 8000)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't automatically open browser"
    )

    args = parser.parse_args()

    # Set mode via environment or direct state manipulation
    from backend.api import app_state
    app_state.mode = args.mode
    print(f"\n{'='*60}")
    print(f"  Air-Gapped Audio File Transfer System")
    print(f"  Mode: {args.mode.upper()}")
    print(f"  URL:  http://{args.host}:{args.port}")
    print(f"{'='*60}\n")

    def open_browser():
        """Open browser after a short delay."""
        import time
        time.sleep(1.5)
        webbrowser.open(f"http://{args.host}:{args.port}")

    if not args.no_browser:
        threading.Thread(target=open_browser, daemon=True).start()

    # Start the server
    # IMPORTANT: Bind to 127.0.0.1 only for security
    uvicorn.run(
        "backend.api:app",
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
