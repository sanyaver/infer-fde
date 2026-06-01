from .progressive import ProgressiveCarrier
from .geico import GeicoCarrier
from .liberty_mutual import LibertyMutualCarrier
from .allstate import AllstateCarrier

REGISTRY: dict = {
    "geico": {
        "label": "Geico",
        "class": GeicoCarrier,
    },
    "allstate": {
        "label": "Allstate",
        "class": AllstateCarrier,
    },
    "progressive": {
        "label": "Progressive",
        "class": ProgressiveCarrier,
    },
    "liberty_mutual": {
        "label": "Liberty Mutual",
        "class": LibertyMutualCarrier,
    },
}
