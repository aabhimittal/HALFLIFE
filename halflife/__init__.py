"""HALFLIFE: measure how long a poisoned memory survives background consolidation."""

from .experiment import ExperimentConfig, ExperimentResult, run_experiment, run_matrix, run_trial
from .payloads import CHANNELS, PAYLOADS
from .defenses import DEFENSES
from .stats import HalfLife, fit_decay, prevalence_half_life, rogan_gladen

__all__ = [
    "CHANNELS", "DEFENSES", "PAYLOADS", "ExperimentConfig", "ExperimentResult", "HalfLife",
    "fit_decay", "prevalence_half_life", "rogan_gladen", "run_experiment", "run_matrix", "run_trial",
]
__version__ = "0.2.0"
