"""Launch the Faceless Studio local dashboard and open it in the browser."""
from __future__ import annotations

import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8765


def _open_browser() -> None:
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}/")


def main() -> None:
    print(f"\n  Faceless Studio  ->  http://{HOST}:{PORT}/")
    print("  (close this window to stop the app)\n")
    threading.Thread(target=_open_browser, daemon=True).start()
    uvicorn.run("webapp.server:app", host=HOST, port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
