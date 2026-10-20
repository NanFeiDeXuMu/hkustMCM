"""Pure donor-specific admission policies, independent of the event engine.

Durations and limits are seconds. Speed is an explicit scenario assumption,
not a hardware prediction. This policy sees only the arriving job's declared
runtime and configured donor; it cannot consult future arrivals or outcomes.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RuntimeAdmission:
    """Limit predicted non-preemptive occupancy separately for each donor."""

    limits_seconds: tuple[tuple[str, float], ...]

    def __post_init__(self):
        names = [name for name, _ in self.limits_seconds]
        if not names or len(names) != len(set(names)):
            raise ValueError('Need distinct, nonempty donor limits')
        if any(not isinstance(name, str) or not name or name == 'A10' for name in names):
            raise ValueError('Limits must name alternative donor pools')
        if any(not math.isfinite(limit) or limit < 0 for _, limit in self.limits_seconds):
            raise ValueError('Runtime limits must be finite and nonnegative')

    def validate_donors(self, donors):
        if set(donors) != {name for name, _ in self.limits_seconds}:
            raise ValueError('Runtime limits must cover exactly the configured donors')

    def allows(self, original_duration: float, speedup: float, donor: str) -> bool:
        if not math.isfinite(original_duration) or original_duration <= 0:
            raise ValueError('Original duration must be finite and positive')
        if not math.isfinite(speedup) or speedup <= 0:
            raise ValueError('Speedup must be finite and positive')
        limit = dict(self.limits_seconds).get(donor)
        if limit is None:
            raise ValueError(f'Unconfigured donor: {donor}')
        return original_duration / speedup <= limit
