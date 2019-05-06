# -*- coding: utf-8 -*-

from enum import Enum, auto, unique

@unique
class CleanupStoreScope(Enum):
    CURRENT_JOB = auto()
    CURRENT_BUILD = auto()
    PREVIOUS_FINISHED_BUILDS = auto()
