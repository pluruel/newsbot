from enum import Enum


class MessageType(Enum):
    SLASH_CYCLE = "cycle"
    SLASH_MORNING = "morning"
    SLASH_WEEKLY = "weekly"
    SLASH_REFLECT = "reflect"
    TRACKER_QUERY = "tracker"


_SLASH_MAP = {
    "/cycle": MessageType.SLASH_CYCLE,
    "/morning": MessageType.SLASH_MORNING,
    "/weekly": MessageType.SLASH_WEEKLY,
    "/reflect": MessageType.SLASH_REFLECT,
}


def classify_message(text: str) -> MessageType:
    return _SLASH_MAP.get(text.strip(), MessageType.TRACKER_QUERY)
