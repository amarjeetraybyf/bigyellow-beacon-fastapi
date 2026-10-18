"""
Server-Sent Events (SSE) Manager with per-role channels and Last-Event-ID replay.
Channels:
  - vessel:{vessel_id}:master (for Master & ship officers)
  - crew:{crew_id} (for individual crew members)
"""

import asyncio
import json
import time
from typing import Dict, List, Any, Optional, AsyncGenerator

from contract.enums import SSEEvent


class SSEMessage:
    def __init__(self, event_id: int, channel: str, event_type: str, data: Dict[str, Any]):
        self.event_id = event_id
        self.channel = channel
        self.event_type = event_type
        self.data = data
        self.timestamp = time.time()

    def to_sse_string(self) -> str:
        return f"id: {self.event_id}\nevent: {self.event_type}\ndata: {json.dumps(self.data)}\n\n"


class SSEManager:
    def __init__(self, max_buffer_size: int = 1000):
        self._counter: int = 0
        self._history: List[SSEMessage] = []
        self._max_buffer = max_buffer_size
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

    def publish(self, channel: str, event_type: Any, data: Dict[str, Any]) -> int:
        evt_str = event_type.value if hasattr(event_type, "value") else str(event_type)
        self._counter += 1
        msg = SSEMessage(self._counter, channel, evt_str, data)
        self._history.append(msg)
        if len(self._history) > self._max_buffer:
            self._history.pop(0)

        # Notify active subscribers
        queues = self._subscribers.get(channel, [])
        for q in queues:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

        return self._counter

    def get_replay_events(self, channel: str, last_event_id: int) -> List[SSEMessage]:
        return [
            msg for msg in self._history
            if msg.channel == channel and msg.event_id > last_event_id
        ]

    def broadcast_sync_status(self, vessel_id: str, data: Dict[str, Any]) -> None:
        self.publish(f"vessel:{vessel_id}:master", "sync.status", data)
        for ch in list(self._subscribers.keys()):
            if ch != f"vessel:{vessel_id}:master":
                self.publish(ch, "sync.status", data)

    def broadcast_event(self, event_type: str, data: Dict[str, Any], vessel_id: Optional[str] = None) -> None:
        """
        Broadcasts an event to the vessel master history and all active crew subscribers.
        Ensures both Master officers and connected crew app users receive real-time updates.
        """
        if vessel_id:
            master_channel = f"vessel:{vessel_id}:master"
            self.publish(master_channel, event_type, data)
            for ch in list(self._subscribers.keys()):
                if ch != master_channel:
                    self.publish(ch, event_type, data)
        else:
            for ch in list(self._subscribers.keys()):
                self.publish(ch, event_type, data)

    async def subscribe(
        self, channel: str, last_event_id: Optional[int] = None
    ) -> AsyncGenerator[str, None]:
        q = asyncio.Queue(maxsize=100)
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(q)

        try:
            if last_event_id is not None:
                for msg in self._history:
                    if msg.channel == channel and msg.event_id > last_event_id:
                        yield msg.to_sse_string()

            while True:
                msg = await q.get()
                yield msg.to_sse_string()
        finally:
            if channel in self._subscribers and q in self._subscribers[channel]:
                self._subscribers[channel].remove(q)


sse_manager = SSEManager()
