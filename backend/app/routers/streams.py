from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import project_service as svc

router = APIRouter(tags=["streams"])


class PolicyBody(BaseModel):
    upsert_mode: str
    note: str | None = None


@router.get("/streams")
def list_streams():
    return svc.list_streams()


@router.get("/streams/{stream_id}/policy")
def get_policy(stream_id: int):
    try:
        return svc.get_stream_policy(stream_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e


@router.put("/streams/{stream_id}/policy")
def update_policy(stream_id: int, body: PolicyBody):
    try:
        return svc.change_upsert_mode(stream_id, body.upsert_mode, body.note)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/streams/{stream_id}/merge-compare")
def merge_compare(stream_id: int):
    try:
        return svc.merge_compare(stream_id)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
