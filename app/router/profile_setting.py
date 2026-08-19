from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Union, Any
from fastapi.encoders import jsonable_encoder

from app.config.database import get_db
from app.models.profile_setting import ProfileSetting
from app.schemas.profile_setting import ProfileSettingCreate, ProfileSettingUpdate, ProfileSettingResponse
from app.schemas.pagination_schema import PaginationQuery
from app.utils.response import success, error
from app.models.linkedin_search_config import LinkedinSearchConfig
import json

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

@router.get("/", response_model=Union[List[ProfileSettingResponse], Any])
def get_all_profiles(query: PaginationQuery = Depends(), db: Session = Depends(get_db)):
    try:
        base_query = db.query(ProfileSetting)
        
        if query.paginate == "true":
            per_page = query.result_per_page or 10
            page = query.page or 1
            
            total = base_query.count()
            profiles = base_query.offset((page - 1) * per_page).limit(per_page).all()
            data = jsonable_encoder([ProfileSettingResponse.model_validate(p) for p in profiles])
            
            return success(
                {
                    "items": data,
                    "pagination": {
                        "total": total,
                        "per_page": per_page,
                        "current_page": page,
                        "last_page": max(1, (total + per_page - 1) // per_page),
                    }
                },
                "Profiles fetched successfully"
            )
        else:
            return base_query.all()
    except Exception as exc:
        return error(f"Failed to fetch profiles: {exc}")

@router.get("/positions")
def get_positions_for_profile(db: Session = Depends(get_db)):
    try:
        configs = db.query(LinkedinSearchConfig).all()
        unique_positions = set()
        for config in configs:
            if config.positions:
                pos_list = config.positions
                if isinstance(pos_list, str):
                    try:
                        pos_list = json.loads(pos_list)
                    except Exception:
                        continue
                if isinstance(pos_list, list):
                    for p in pos_list:
                        if isinstance(p, str) and p.strip():
                            unique_positions.add(p.strip())
        return success(list(unique_positions), "Positions fetched successfully")
    except Exception as exc:
        return error(f"Failed to fetch positions: {exc}")

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