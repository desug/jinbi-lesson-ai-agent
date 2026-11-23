from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.schemas.ai import AiQueryRequest, AiQueryResponse
from app.schemas.auth import TokenPayload
from app.services.ai_query_service import answer_ai_query
from app.utils.deps import get_current_admin, get_db


router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/ai-query", response_model=AiQueryResponse, summary="管理员 AI 查询课时助手")
def admin_ai_query_endpoint(
    payload: AiQueryRequest,
    current_admin: TokenPayload = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> AiQueryResponse:
    return answer_ai_query(
        db=db,
        current_admin=current_admin,
        query=payload.query,
    )
from pydantic import Field
from sqlalchemy import select
from app.models.ai_memory import AiConversation, AiMessage, AiAdminPreference
from app.schemas import CamelModel
from app.services.common import parse_token_subject_id
from app.services.ai_query_service import GRADE_NAMES
from app.services.class_types import CLASS_TYPES
from fastapi import HTTPException


class PreferencesRequest(CamelModel):
    grade: str = Field(default='', max_length=50)
    class_type: str = Field(default='', max_length=50)
    confirmed: bool


def owned_conversation(db: Session, admin_id: int, conversation_id: int) -> AiConversation:
    conversation = db.scalar(select(AiConversation).where(AiConversation.id == conversation_id, AiConversation.admin_id == admin_id))
    if conversation is None:
        raise HTTPException(status_code=404, detail='会话不存在')
    return conversation


@router.get('/ai/conversations')
def list_conversations(current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    admin_id = parse_token_subject_id(current_admin)
    rows = db.scalars(select(AiConversation).where(AiConversation.admin_id == admin_id).order_by(AiConversation.updated_at.desc())).all()
    return [{'id': c.id, 'title': c.title, 'updatedAt': c.updated_at} for c in rows]


@router.get('/ai/conversations/{conversation_id}')
def get_conversation(conversation_id: int, current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    c = owned_conversation(db, parse_token_subject_id(current_admin), conversation_id)
    rows = db.scalars(select(AiMessage).where(AiMessage.conversation_id == c.id).order_by(AiMessage.id)).all()
    return {'id': c.id, 'title': c.title, 'messages': [{'role': m.role, 'content': m.content, 'result': m.result, 'createdAt': m.created_at} for m in rows]}


@router.delete('/ai/conversations/{conversation_id}')
def delete_conversation(conversation_id: int, current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    c = owned_conversation(db, parse_token_subject_id(current_admin), conversation_id)
    db.delete(c)
    db.commit()
    return {'deleted': True}


@router.get('/ai/preferences')
def get_preferences(current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    p = db.get(AiAdminPreference, parse_token_subject_id(current_admin))
    return {'grade': p.grade if p else '', 'classType': p.class_type if p else ''}


@router.put('/ai/preferences')
def put_preferences(payload: PreferencesRequest, current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    if not payload.confirmed or (payload.grade and payload.grade not in GRADE_NAMES) or (payload.class_type and payload.class_type not in CLASS_TYPES):
        raise HTTPException(status_code=422, detail='请确认并提供有效的年级和班型')
    admin_id = parse_token_subject_id(current_admin)
    p = db.get(AiAdminPreference, admin_id)
    if p is None:
        p = AiAdminPreference(admin_id=admin_id)
        db.add(p)
    p.grade, p.class_type = payload.grade, payload.class_type
    db.commit()
    return {'grade': p.grade, 'classType': p.class_type}


@router.delete('/ai/preferences')
def delete_preferences(current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    p = db.get(AiAdminPreference, parse_token_subject_id(current_admin))
    if p:
        db.delete(p)
        db.commit()
    return {'deleted': True}

class ChatRequest(AiQueryRequest):
    conversation_id: int | None = Field(default=None, gt=0)
    selected_student_id: int | None = Field(default=None, gt=0)


@router.post('/ai/chat')
def admin_function_chat(payload: ChatRequest, current_admin: TokenPayload = Depends(get_current_admin), db: Session = Depends(get_db)):
    from app.services.ai_conversation_chat import chat
    return chat(db, parse_token_subject_id(current_admin), payload.query, payload.conversation_id, payload.selected_student_id)
