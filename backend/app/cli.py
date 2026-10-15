"""Admin commands, run from the backend/ folder.

  python -m app.cli create-owner     Create an owner account (asks for details)
  python -m app.cli check            Check configuration, database and stock ledger
"""
import argparse
import getpass
import sys

from pydantic import ValidationError
from sqlalchemy import text

from app.database import SessionLocal
from app.schemas.user import UserCreate
from app.services import stock as stock_service
from app.services import users as user_service


def _prompt(label: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or (default or "")


def create_owner(args: argparse.Namespace) -> int:
    full_name = args.full_name or _prompt("Full name")
    username = args.username or _prompt("Username (e.g. jalal)")
    password = args.password
    if not password:
        password = getpass.getpass("Password (min 8 characters): ")
        if getpass.getpass("Repeat password: ") != password:
            print("Passwords do not match. Nothing was created.")
            return 1
    try:
        data = UserCreate(full_name=full_name, username=username, password=password, role="owner")
    except ValidationError as e:
        for err in e.errors():
            print(f"  - {err['loc'][0]}: {str(err['msg']).removeprefix('Value error, ')}")
        return 1
    with SessionLocal() as db:
        try:
            user = user_service.create_user(db, data, actor=None, request=None)
        except Exception as e:  # HTTPException from the service, e.g. duplicate username
            print(f"Could not create owner: {getattr(e, 'detail', e)}")
            return 1
        print(f"Owner account '{user.username}' created. You can now log in.")
    return 0


def check(_: argparse.Namespace) -> int:
    from app.config import get_settings

    s = get_settings()
    print(f"Environment: {s.environment}")
    try:
        with SessionLocal() as db:
            version = db.scalar(text("SELECT version_num FROM alembic_version"))
            users = user_service.count_users(db)
            mismatches = stock_service.ledger_mismatches(db)
        print(f"Database: connected (migration {version}, {users} user account(s))")
    except Exception as e:
        print(f"Database: NOT reachable — {e.__class__.__name__}: {e}")
        return 1
    if mismatches:
        print(f"Stock ledger: {len(mismatches)} product(s) do NOT match their movements:")
        for product_id, sku, cached, ledger in mismatches:
            print(f"  - #{product_id} {sku}: quantity {cached}, movements add up to {ledger}")
        return 1
    print("Stock ledger: every product quantity matches its movements")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("create-owner", help="Create an owner account")
    p.add_argument("--full-name")
    p.add_argument("--username")
    p.add_argument("--password", help="Avoid on shared machines; you will be prompted instead.")
    p.set_defaults(func=create_owner)
    c = sub.add_parser("check", help="Check configuration and database")
    c.set_defaults(func=check)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
