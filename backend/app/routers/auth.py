from fastapi import APIRouter, Depends,HTTPException
from sqlalchemy.orm import Session 
from app.core.config import settings
from app.database import get_db
from app.models.user import User
from app.schemas.auth import Token, UserRegister
from app.core.security import hash_password
from app.schemas.auth import UserLogin
from app.dependencies import enforce_rate_limit, get_current_user
from app.core.security import (verify_password,create_access_token)

router=APIRouter(
    prefix="/auth",
    tags=["Authentication"]
)

@router.post("/register")
def register(
    user: UserRegister,
    db: Session = Depends(get_db)
):
    existing_user=(
        db.query(User)
        .filter(User.email == user.email)
        .first()
    )

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Email Already Exist"
        )

    new_user=User(
        email=user.email,
        hashed_password=hash_password(user.password),
        full_name=user.full_name
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return{
        "Message" : "User Registered Successfully"
    }

@router.post("/login")
def login(
    user : UserLogin,
    db : Session=Depends(get_db)
):
     db_user = (
        db.query(User)
        .filter(User.email == user.email)
        .first()
    )
     if not db_user:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )
     if not verify_password(
        user.password,
        db_user.hashed_password
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )
     access_token = create_access_token(
        data={"sub": db_user.email}
    )
     return {
        "access_token": access_token,
        "token_type": "bearer"
    }
@router.get("/me")
def me(
    current_user: User = Depends(get_current_user)
):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name
    }


@router.post("/demo-login", response_model=Token)
def demo_login(
    db: Session = Depends(get_db)
):
    """Sign in as the shared demo account, without the caller knowing anything.

    Takes no body on purpose. The credentials live in `DEMO_USER_EMAIL` /
    `DEMO_USER_PASSWORD` on the server, so nothing about them reaches the
    browser: no email in the markup, no password in JavaScript, no
    NEXT_PUBLIC_* variable, nothing in an API response. The frontend's
    "Try demo" button is a bare POST to this path.

    Not a second authentication system. It resolves the configured user,
    checks the configured password against the bcrypt hash on that row with
    the same `verify_password` a normal login uses, and mints the same JWT
    through the same `create_access_token`. Everything downstream — the
    Authorization header, `get_current_user`, workspace membership — cannot
    tell a demo session from any other, which is the point: no parallel code
    path means no parallel set of bugs.

    Verifying rather than trusting the environment matters. If
    `DEMO_USER_PASSWORD` is rotated without re-running
    `scripts/provision_demo.py`, this fails closed with a 401 instead of
    handing out sessions on a stale hash.
    """

    if not settings.demo_user_email or not settings.demo_user_password:
        # 404, not 403: a deployment without a demo should not advertise that
        # the endpoint exists at all.
        raise HTTPException(status_code=404, detail="Not found")

    # One shared bucket rather than per-workspace, since there is no caller
    # identity yet. Generous enough for a launch-day crowd, low enough that
    # nobody drives bcrypt in a loop with it — each attempt is a deliberate
    # ~100ms hash comparison.
    enforce_rate_limit("demo-login", 0, limit=60, window_seconds=60.0)

    demo_user = (
        db.query(User)
        .filter(User.email == settings.demo_user_email)
        .first()
    )

    if demo_user is None or not verify_password(
        settings.demo_user_password, demo_user.hashed_password
    ):
        # Deliberately vague, and deliberately not logged with any of the
        # inputs: a misconfigured demo must not print its own credentials into
        # a log aggregator.
        raise HTTPException(
            status_code=503,
            detail="The demo account is not available on this deployment"
        )

    access_token = create_access_token(data={"sub": demo_user.email})

    return {
        "access_token": access_token,
        "token_type": "bearer"
    }
