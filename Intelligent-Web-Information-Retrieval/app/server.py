"""Match 4 server (optional).

The Match 4 brief allows a thin client + resident server pattern
(per Match 3 best practice). For the deterministic core, this is
unnecessary — but if a future user wants to plug in a local LLM (e.g.
vLLM on ROCm), the server can hold the model and the CLI can dispatch
queries over a Unix socket.

Default behavior: this script does nothing. It exists so the Dockerfile
can wire `CMD ["python", "-m", "app.server"]` without breaking.
"""

from __future__ import annotations

import logging
import os
import signal
import sys

log = logging.getLogger("match4.server")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("MATCH4_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("match4 server: deterministic core, no warmup required")
    log.info("set MATCH4_USE_LLM=1 and MATCH4_LLM_ENDPOINT=... to enable LLM augmentation")

    stop = {"flag": False}

    def _handler(_sig, _frame):
        stop["flag"] = True
        log.info("server: shutdown signal received")

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    # If MATCH4_KEEP_ALIVE=1, block forever (used by harness startup test).
    if os.environ.get("MATCH4_KEEP_ALIVE", "0") == "1":
        log.info("server: keep-alive mode, waiting for signal")
        while not stop["flag"]:
            signal.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())