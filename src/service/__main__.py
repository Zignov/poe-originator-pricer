"""Run the pricing service.

    python -m src.service                 # 127.0.0.1:8777, token printed once
    python -m src.service --port 9000
    python -m src.service --no-auth       # local testing only

Bound to the loopback interface and nothing else. See src/service/api.py for
why the token exists even on a local-only port.
"""
from __future__ import annotations

import argparse
import secrets

from src.service.api import create_app, write_token

DEFAULT_PORT = 8777


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help="serve without a token; for local testing only",
    )
    args = parser.parse_args(argv)

    import uvicorn

    token = "" if args.no_auth else secrets.token_urlsafe(32)
    app = create_app(token=token)

    if token:
        path = write_token(token)
        print(f"token: {token}")
        print(f"  also written to {path}")
    else:
        print("WARNING: serving with no token. Any page in your browser can read this.")

    # host is hardcoded. Making it an option would be an invitation to set it
    # to 0.0.0.0 and expose a stash reader to the network.
    print(f"listening on http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
