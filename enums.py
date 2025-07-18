# enums.py
from enum import Enum, auto

class DropActionType(Enum):
    INVALID = auto()
    REORDER_CLIPS = auto()
    MOVE_CLIPS_COMPATIBLE = auto()
    MOVE_CLIPS_NEW_LAYER = auto()
    COPY_CLIPS_COMPATIBLE = auto()
    COPY_CLIPS_NEW_LAYER = auto()
    MERGE_LAYERS = auto()