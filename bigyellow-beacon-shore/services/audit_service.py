"""
Shore Audit Trail Service
Records immutable audit logs for sensitive shore actions:
- accept (NEW -> IN_REVIEW)
- reject
- close
- reply (ticket message)
- publish (learning)
- retract (learning)
"""

from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from db_models import ShoreAuditLogModel


def log_shore_action(
    db: Session,
    action: str,
    user_id: str,
    target_id: str,
    details: Optional[Dict[str, Any]] = None,
) -> ShoreAuditLogModel:
    entry = ShoreAuditLogModel(
        action=action,
        user_id=user_id,
        target_id=target_id,
        details=details or {},
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
