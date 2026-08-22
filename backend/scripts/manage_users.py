"""
User administration CLI.

The application has no password-change endpoint and no user list/delete route --
`POST /api/auth/register` (admin-only) is the entire user-management surface. This
script fills that gap and works against whatever `DATABASE_URL` points at, so the
same commands serve the local SQLite file and Azure SQL.

    python scripts/manage_users.py list
    python scripts/manage_users.py create jkim605 --role admin
    python scripts/manage_users.py set-password admin
    python scripts/manage_users.py set-role ncrone1 editor
    python scripts/manage_users.py delete olduser --reassign-to admin

Passwords are prompted for by default so they never land in shell history. Use
--password (or NEURO_NEW_PASSWORD) only for automation.

Roles: viewer (read-only) | editor (create/edit reconstructions, run exports)
       | admin (+ create users, permanent delete)
"""
import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, func, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

ROLES = ("viewer", "editor", "admin")

# Refused outright: these are the defaults this project has shipped, and the whole
# point of the script is to get off them.
BANNED = {"changeme", "password", "admin", "123456", "letmein"}
MIN_LEN = 8


def sync_url(url: str) -> str:
    return (url.replace("+aiosqlite", "")
               .replace("+aioodbc", "+pyodbc")
               .replace("+asyncpg", "+psycopg2"))


def get_password(args, who: str) -> str:
    pw = args.password or os.environ.get("NEURO_NEW_PASSWORD")
    if not pw:
        pw = getpass.getpass(f"New password for {who}: ")
        if pw != getpass.getpass("Repeat: "):
            sys.exit("Passwords do not match.")
    if len(pw) < MIN_LEN:
        sys.exit(f"Password must be at least {MIN_LEN} characters.")
    if pw.lower() in BANNED and not args.force:
        sys.exit(f"Refusing a well-known default password ({pw!r}). Use --force to override.")
    return pw


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", help="default: $DATABASE_URL, else the local SQLite file")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="show all users")

    p = sub.add_parser("create", help="create a user")
    p.add_argument("username")
    p.add_argument("--role", choices=ROLES, default="viewer")
    p.add_argument("--password")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("set-password", help="change a user's password")
    p.add_argument("username")
    p.add_argument("--password")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("set-role", help="change a user's role")
    p.add_argument("username")
    p.add_argument("role", choices=ROLES)

    p = sub.add_parser("delete", help="delete a user")
    p.add_argument("username")
    p.add_argument("--reassign-to", help="username to inherit their reconstructions")

    args = ap.parse_args()

    import database
    from auth import hash_password

    url = sync_url(args.database_url or os.environ.get("DATABASE_URL") or database.DATABASE_URL)
    engine = create_engine(url)
    users = database.User
    recons = database.Reconstruction

    with Session(engine) as s:
        def find(name):
            u = s.scalar(select(users).where(users.username == name))
            if not u:
                sys.exit(f"No such user: {name!r}")
            return u

        if args.cmd == "list":
            rows = s.scalars(select(users).order_by(users.id)).all()
            if not rows:
                print("(no users)")
                return
            print(f"{'id':>3}  {'username':<24} {'role':<8} {'recons':>6}  created")
            for u in rows:
                n = s.scalar(select(func.count()).select_from(recons)
                             .where(recons.created_by == u.id))
                print(f"{u.id:>3}  {u.username:<24} {u.role:<8} {n:>6}  {u.created_at}")
            return

        if args.cmd == "create":
            if s.scalar(select(users).where(users.username == args.username)):
                sys.exit(f"User {args.username!r} already exists.")
            pw = get_password(args, args.username)
            s.add(users(username=args.username, hashed_password=hash_password(pw), role=args.role))
            s.commit()
            print(f"Created {args.username!r} with role {args.role}.")
            return

        if args.cmd == "set-password":
            u = find(args.username)
            name = u.username
            pw = get_password(args, name)
            s.execute(update(users).where(users.id == u.id)
                      .values(hashed_password=hash_password(pw)))
            s.commit()
            # Existing JWTs stay valid until they expire (8 h) -- rotate SECRET_KEY
            # as well if you need to cut off sessions immediately.
            print(f"Password updated for {name!r}. "
                  "Existing sessions remain valid for up to 8 hours.")
            return

        if args.cmd == "set-role":
            u = find(args.username)
            # Capture before the update: the ORM object expires on commit, so
            # reading u.role afterwards yields the new value, not the old one.
            name, old_role = u.username, u.role
            if u.role == "admin" and args.role != "admin":
                n_admins = s.scalar(select(func.count()).select_from(users)
                                    .where(users.role == "admin"))
                if n_admins <= 1:
                    sys.exit("Refusing to demote the only admin -- nobody could create users.")
            s.execute(update(users).where(users.id == u.id).values(role=args.role))
            s.commit()
            print(f"{name!r}: {old_role} -> {args.role}")
            return

        if args.cmd == "delete":
            u = find(args.username)
            name = u.username  # same expire-on-commit caveat as set-role
            if u.role == "admin":
                n_admins = s.scalar(select(func.count()).select_from(users)
                                    .where(users.role == "admin"))
                if n_admins <= 1:
                    sys.exit("Refusing to delete the only admin.")
            # reconstructions.created_by is a foreign key -- deleting the owner
            # would either fail or leave rows pointing at a missing user.
            owned = s.scalar(select(func.count()).select_from(recons)
                             .where(recons.created_by == u.id))
            if owned:
                if not args.reassign_to:
                    sys.exit(f"{u.username!r} created {owned} reconstruction(s). "
                             "Pass --reassign-to <username> to transfer them first.")
                heir = find(args.reassign_to)
                s.execute(update(recons).where(recons.created_by == u.id)
                          .values(created_by=heir.id))
                print(f"Reassigned {owned} reconstruction(s) to {heir.username!r}.")
            s.delete(u)
            s.commit()
            print(f"Deleted {name!r}.")
            return


if __name__ == "__main__":
    main()
