import random

import pytest

from halflife.experiment import ExperimentConfig


@pytest.fixture
def rng():
    return random.Random(1234)


@pytest.fixture
def small_cfg():
    return ExperimentConfig(trials=12, cycles=12, seed=7)
