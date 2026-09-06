"""Auth seam (D2): public demo — all mutations attributed to the seeded Demo Actuary.
Replace this dependency to add real auth without refactoring."""
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User

DEMO_EMAIL = "demo.actuary@vortex.app"

_actor_dep = Depends(get_session)


def get_current_actor(session: Session = _actor_dep) -> User:
    user = session.execute(select(User).where(User.email == DEMO_EMAIL)).scalar_one_or_none()
    if user is None:
        user = User(email=DEMO_EMAIL, name="Demo Actuary", role="actuary")
        session.add(user)
        session.commit()
        session.refresh(user)
    return user
