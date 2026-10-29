"""A fixed viewer account for the public demo.

The password is published with the demo. The account is created with role "viewer"
and startup refuses to leave it as anything else. It is not an administrator.
"""

from sqlalchemy import select

from .auth import hash_password, verify_password
from .db import SessionLocal
from .models import User

DEMO_EMAIL = "demo@agentops.local"
DEMO_NAME = "Demo access"
DEMO_PASSWORD = "DemoAccess2026!"


def ensure_demo_viewer() -> None:
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == DEMO_EMAIL)).first()
        if user is None:
            db.add(User(
                email=DEMO_EMAIL,
                name=DEMO_NAME,
                password_hash=hash_password(DEMO_PASSWORD),
                role="viewer",
            ))
        else:
            user.role = "viewer"
            if not user.password_hash or not verify_password(DEMO_PASSWORD, user.password_hash):
                user.password_hash = hash_password(DEMO_PASSWORD)
        db.commit()
