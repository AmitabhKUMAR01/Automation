from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.config.database import get_db
from app.models.profile_setting import ProfileSetting
from app.schemas.profile_setting import ProfileSettingCreate, ProfileSettingUpdate, ProfileSettingResponse

router = APIRouter(prefix="/profile-settings", tags=["Profile Settings"])

@router.post("/", response_model=ProfileSettingResponse, status_code=status.HTTP_201_CREATED)
def create_profile(profile: ProfileSettingCreate, db: Session = Depends(get_db)):
    db_profile = db.query(ProfileSetting).filter(ProfileSetting.name == profile.name).first()
    if db_profile:
        raise HTTPException(status_code=400, detail="Profile with this name already exists")
    
    new_profile = ProfileSetting(**profile.model_dump())
    db.add(new_profile)
    db.commit()
    db.refresh(new_profile)
    return new_profile

@router.get("/", response_model=List[ProfileSettingResponse])
def get_all_profiles(db: Session = Depends(get_db)):
    return db.query(ProfileSetting).all()

@router.get("/{profile_id}", response_model=ProfileSettingResponse)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile

@router.put("/{profile_id}", response_model=ProfileSettingResponse)
def update_profile(profile_id: int, profile_update: ProfileSettingUpdate, db: Session = Depends(get_db)):
    db_profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    update_data = profile_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_profile, key, value)
    
    db.commit()
    db.refresh(db_profile)
    return db_profile

@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    db_profile = db.query(ProfileSetting).filter(ProfileSetting.id == profile_id).first()
    if not db_profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    db.delete(db_profile)
    db.commit()
    return None
