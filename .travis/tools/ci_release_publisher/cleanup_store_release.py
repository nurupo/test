# -*- coding: utf-8 -*-

from enum import Enum, auto, unique

@unique
class CleanupStoreRelease(Enum):
    COMPLETE = auto()
    INCOMPLETE = auto()
