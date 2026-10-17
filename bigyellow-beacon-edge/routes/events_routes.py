"""
Server-Sent Events Route (/events)
Per-role channels:
  - Master / officers: vessel:{vessel_id}:master
  - Crew: crew:{crew_id}
Supports Last-Event-ID header replay.
"""

from typing import Dict, Any, Optional
from fastapi import APIRouter, Header, Depends
from fastapi.responses import StreamingResponse

from auth import get_current_edge_user
from config import VESSEL_ID
from services.sse_manager import sse_manager
from contract.enums import Role

router = APIRouter(tags=["Server-Sent Events"])


@router.get("/events")
async def sse_events_stream(
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    user: Dict[str, Any] = Depends(get_current_edge_user),
):
    """
    Subscribe to live shipboard event stream.
    Replays missed events if Last-Event-ID header is provided.
    """
    role = user.get("role")
    crew_id = user.get("crew_id")
    vessel_id = user.get("vessel_id", VESSEL_ID)

    channel = f"vessel:{vessel_id}:master" if role == Role.MASTER else f"crew:{crew_id}"

    last_id_int = int(last_event_id) if last_event_id and last_event_id.isdigit() else None

    return StreamingResponse(
        sse_manager.subscribe(channel, last_event_id=last_id_int),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
