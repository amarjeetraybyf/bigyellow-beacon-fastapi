"""
Shared Enums for BigYellow Beacon Contract
"""

from enum import Enum


class Role(str, Enum):
    CREW = "crew"
    MASTER = "master"
    SHORE_REVIEWER = "shore_reviewer"
    SHORE_ADMIN = "shore_admin"
    EDGE_DEVICE = "edge_device"


class Origin(str, Enum):
    EDGE = "edge"
    SHORE = "shore"


class OnboardStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ACTION_ASSIGNED = "ACTION_ASSIGNED"
    ACTIONED = "ACTIONED"
    ACTION_IN_PROGRESS = "ACTION_IN_PROGRESS"
    RESOLVED = "RESOLVED"
    RESOLVED_ON_BOARD = "RESOLVED_ON_BOARD"
    CLOSED = "CLOSED"


class ShoreStatus(str, Enum):
    DRAFT = "DRAFT"
    QUEUED = "QUEUED"
    NEW = "NEW"
    IN_REVIEW = "IN_REVIEW"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class LearningStatus(str, Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETRACTED = "RETRACTED"


class PullChangeType(str, Enum):
    TICKET_SHORE_STATUS = "ticket.shore_status"
    TICKET_MESSAGE = "ticket_message"
    LEARNING_PUBLISHED = "learning.published"
    LEARNING_RETRACTED = "learning.retracted"
    FLEET_ALERT = "fleet_alert"
    RAIL_UPDATE = "rail_update"
    ANCHOR_SCHEDULED = "anchor.scheduled"
    BRIEF_ENRICHMENT = "brief_enrichment"


class SSEEvent(str, Enum):
    TICKET_NEW = "ticket.new"
    TICKET_UPDATED = "ticket.updated"
    MESSAGE_NEW = "message.new"
    LEARNING_NEW = "learning.new"
    SYNC_STATUS = "sync.status"
    BRIEF_UPDATED = "brief.updated"
    ANCHOR_NEW = "anchor.new"
    CHECKIN_REMINDER = "checkin.reminder"
    WELFARE_REQUEST = "welfare.request"


class LinkOverride(str, Enum):
    AUTO = "auto"
    UP = "up"
    DOWN = "down"
