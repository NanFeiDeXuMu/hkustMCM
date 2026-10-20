"""Stable scenario contract; no experiment paths, persistence, or selection logic.

Times are seconds. RuntimeAdmission is a declared donor-speed scenario, not
a measured speed predictor. Frozen engines remain the executable references.
"""
from dataclasses import asdict, dataclass
import math

from routing_admission import RuntimeAdmission
from scheduler import RoutingConfig, run_simulation as reference_engine
from experimental_scheduler import run_simulation as admission_engine


@dataclass(frozen=True)
class SimulationSpec:
    policy: str = 'log_aging'
    log_aging_tau_seconds: float = 604800.
    aging_alpha: float = 0.
    max_backfill_scan: int = 128
    routing: RoutingConfig | None = None
    admission: RuntimeAdmission | None = None

    def __post_init__(self):
        if self.policy not in {'fcfs', 'sjf', 'backfill', 'aging', 'log_aging'}:
            raise ValueError('Unknown queue policy')
        if not math.isfinite(self.log_aging_tau_seconds) or self.log_aging_tau_seconds <= 0:
            raise ValueError('Log aging time scale must be finite and positive')
        if not math.isfinite(self.aging_alpha) or self.aging_alpha < 0:
            raise ValueError('Aging alpha must be finite and nonnegative')
        if type(self.max_backfill_scan) is not int or self.max_backfill_scan < 0:
            raise ValueError('Backfill scan must be a nonnegative integer')
        if self.admission is not None:
            if self.routing is None or self.routing.mode != 'pull':
                raise ValueError('Donor runtime admission requires pull routing')
            self.admission.validate_donors(self.routing.donors)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        """Strict config parsing; misspelled fields must not silently disappear."""
        values = dict(value)
        if values.get('routing') is not None:
            route = dict(values['routing'])
            route['donors'] = tuple(route['donors'])
            route['donor_caps'] = tuple(tuple(item) for item in route.get('donor_caps', ()))
            values['routing'] = RoutingConfig(**route)
        if values.get('admission') is not None:
            admission = dict(values['admission'])
            admission['limits_seconds'] = tuple(tuple(item) for item in admission['limits_seconds'])
            values['admission'] = RuntimeAdmission(**admission)
        return cls(**values)

    @classmethod
    def from_manifest(cls, manifest):
        """Extract only execution fields from a historical scenario manifest."""
        values = {key: manifest[key] for key in cls.__dataclass_fields__
                  if key in manifest and manifest[key] is not None}
        if 'policy' not in values:
            raise ValueError('Historical manifest must specify its queue policy')
        # The original engine default was one day. Honor it if omitted.
        values.setdefault('log_aging_tau_seconds', 86400.)
        return cls.from_dict(values)


def simulate(jobs, nodes, spec: SimulationSpec):
    """Execute a declared scenario without changing globals or input frames."""
    kwargs = dict(policy=spec.policy, routing=spec.routing,
                  max_backfill_scan=spec.max_backfill_scan, aging_alpha=spec.aging_alpha,
                  log_aging_tau_seconds=spec.log_aging_tau_seconds)
    if spec.admission is None:
        return reference_engine(jobs, nodes, **kwargs)
    return admission_engine(jobs, nodes, admission=spec.admission, **kwargs)
