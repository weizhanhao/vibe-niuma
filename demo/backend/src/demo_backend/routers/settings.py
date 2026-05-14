from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from demo_backend.db import get_db
from demo_backend.models import AppSetting
from demo_backend.schemas import SettingOut, SettingUpdateIn

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=list[SettingOut])
def list_settings(db: Session = Depends(get_db)) -> list[AppSetting]:
    return db.query(AppSetting).order_by(AppSetting.key).all()


@router.put("/{key}", response_model=SettingOut)
def update_setting(
    key: str, payload: SettingUpdateIn, db: Session = Depends(get_db)
) -> AppSetting:
    setting = db.get(AppSetting, key)
    if setting is None:
        raise HTTPException(status_code=404, detail="设置项不存在")
    setting.value = payload.value
    db.commit()
    db.refresh(setting)
    return setting
