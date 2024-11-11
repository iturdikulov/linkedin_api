from dataclasses import dataclass
from enum import Enum


@dataclass
class LinkedinApFeatureAccess:
    linkedin: bool
    premium: bool

class LinkedinConnectionState(Enum):
    SUCCESS = 0
    CANT_RESEND_YET = 1

