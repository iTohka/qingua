from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python app.py` from any working directory.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qingua.core import App  # noqa: E402
from qingua.server import serve  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="轻刮 QingGua — 轻量影视刮削器")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    parser.add_argument("--port", type=int, default=8787, help="起始端口，默认 8787")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    app = App()
    serve(app, host=args.host, port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
