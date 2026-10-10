import os
import random

import pytest
from hypothesis import HealthCheck, settings

from halflife.experiment import ExperimentConfig

# Property tests search new cases on every run. "default" keeps the suite fast; the nightly
# job sets HYPOTHESIS_PROFILE=deep for a much larger search. A failure prints the smallest
# failing input and a @reproduce_failure line, and is replayed from .hypothesis/ next time.
settings.register_profile("default", max_examples=100, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.register_profile("deep", max_examples=3000, deadline=None,
                          suppress_health_check=[HealthCheck.too_slow])
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "default"))


@pytest.fixture
def rng():
    return random.Random(1234)


@pytest.fixture
def small_cfg():
    return ExperimentConfig(trials=12, cycles=12, seed=7)
