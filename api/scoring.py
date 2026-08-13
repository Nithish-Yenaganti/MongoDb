"""Gateway-facing exports for the real context scorer."""
# mongodb/api/scoring.py
from scorer import PIN_THRESHOLD, score

__all__ = ["PIN_THRESHOLD", "score"]
