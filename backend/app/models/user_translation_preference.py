import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, UniqueConstraint
from app.database import Base


class UserTranslationPreference(Base):
    """
    用户翻译偏好：整类内容默认不翻译（如人名、机构名、化学式等）。
    category 取值见 app.constants.PREFERENCE_CATEGORIES。
    """
    __tablename__ = "user_translation_preferences"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), nullable=False, index=True)
    # 偏好分类（person_name / institution / chemical_formula / …）
    category = Column(String(50), nullable=False)
    # 处理方式，目前固定为 never_translate，预留扩展
    action = Column(String(20), nullable=False, default="never_translate", server_default="never_translate")
    # 用户自定义备注（可选）
    note = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "category", name="uq_user_pref_category"),
    )
