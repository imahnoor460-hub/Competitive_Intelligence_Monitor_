from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.dependencies import (
    get_current_user,
    require_not_demo_user,
)
from app.services.gdpr_service import (
    export_user_data, delete_user_account, SoleOwnerWithOthersError
)

router = APIRouter(
    prefix="/users/me",
    tags=["GDPR"]
)


@router.get("/export")
def export_my_data(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    return export_user_data(db, current_user)


@router.delete("")
def delete_my_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _demo: None = Depends(require_not_demo_user("delete the account"))
):

    try:
        delete_user_account(db, current_user)
    except SoleOwnerWithOthersError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"message": "Account deleted"}
