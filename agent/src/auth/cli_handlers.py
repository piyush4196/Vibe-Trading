"""CLI: ``vibe-trading user {create,list,passwd,disable,enable}``."""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from src.auth.users import (
    AuthError,
    create_user,
    list_users,
    set_disabled,
    set_password,
    users_path,
)


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "user",
        help="Manage local API users (JWT login alongside API_AUTH_KEY)",
    )
    sub = parser.add_subparsers(dest="user_command")

    create = sub.add_parser("create", help="Create a user")
    create.add_argument("username")
    create.add_argument("--password", "-p", default=None)
    create.add_argument("--prompt-password", action="store_true")
    create.add_argument("--role", choices=["admin", "user"], default="admin")
    create.add_argument("--display-name", default="")
    create.add_argument("--force", action="store_true")

    sub.add_parser("list", help="List users")

    passwd = sub.add_parser("passwd", help="Change a user's password")
    passwd.add_argument("username")
    passwd.add_argument("--password", "-p", default=None)
    passwd.add_argument("--prompt-password", action="store_true")

    disable = sub.add_parser("disable", help="Disable a user")
    disable.add_argument("username")
    enable = sub.add_parser("enable", help="Enable a user")
    enable.add_argument("username")


def dispatch(args: argparse.Namespace) -> int:
    cmd = getattr(args, "user_command", None)
    if not cmd:
        print("user requires a subcommand: create | list | passwd | disable | enable", file=sys.stderr)
        return 2
    try:
        if cmd == "create":
            return _create(args)
        if cmd == "list":
            return _list()
        if cmd == "passwd":
            return _passwd(args)
        if cmd == "disable":
            set_disabled(args.username, True)
            print(f"disabled {args.username}")
            return 0
        if cmd == "enable":
            set_disabled(args.username, False)
            print(f"enabled {args.username}")
            return 0
    except AuthError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"unknown user command: {cmd}", file=sys.stderr)
    return 2


def _read_password(args: argparse.Namespace) -> str:
    password = getattr(args, "password", None)
    if getattr(args, "prompt_password", False) or not password:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            raise AuthError("passwords do not match")
    return str(password)


def _create(args: argparse.Namespace) -> int:
    password = _read_password(args)
    user = create_user(
        args.username,
        password,
        role=args.role,
        display_name=args.display_name,
        force=bool(args.force),
    )
    print(f"created user '{user.username}' role={user.role}")
    print(f"store: {users_path()}")
    return 0


def _list() -> int:
    rows = [u.public_dict() for u in list_users()]
    print(json.dumps({"store": str(users_path()), "users": rows}, indent=2))
    return 0


def _passwd(args: argparse.Namespace) -> int:
    password = _read_password(args)
    set_password(args.username, password)
    print(f"updated password for {args.username}")
    return 0
