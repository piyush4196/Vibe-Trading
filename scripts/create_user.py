#!/usr/bin/env python3
"""Create a local Vibe-Trading API user.

Usage:
  python scripts/create_user.py --username admin --password 'ChangeMe123!' --role admin
  python scripts/create_user.py --username alice --prompt-password

Users are stored in ~/.vibe-trading/users.json (mode 0600).
After creation, log in via:

  curl -X POST http://127.0.0.1:8899/auth/login \\
    -H 'Content-Type: application/json' \\
    -d '{"username":"admin","password":"..."}'
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# Allow running from repo root without install.
_ROOT = Path(__file__).resolve().parents[1]
_AGENT = _ROOT / "agent"
if str(_AGENT) not in sys.path:
    sys.path.insert(0, str(_AGENT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Vibe-Trading API user")
    parser.add_argument("--username", "-u", required=True, help="Login username")
    parser.add_argument("--password", "-p", default=None, help="Password (min 8 chars)")
    parser.add_argument(
        "--prompt-password",
        action="store_true",
        help="Prompt for password interactively (no echo)",
    )
    parser.add_argument(
        "--role",
        choices=["admin", "user"],
        default="admin",
        help="Role (default: admin)",
    )
    parser.add_argument("--display-name", default="", help="Optional display name")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite password/role if the username already exists",
    )
    args = parser.parse_args(argv)

    password = args.password
    if args.prompt_password or not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("error: passwords do not match", file=sys.stderr)
            return 1

    from src.auth.users import AuthError, create_user, users_path

    try:
        user = create_user(
            args.username,
            password,
            role=args.role,
            display_name=args.display_name,
            force=bool(args.force),
        )
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"created user '{user.username}' role={user.role}")
    print(f"store: {users_path()}")
    print("login: POST /auth/login  with {\"username\",\"password\"}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
