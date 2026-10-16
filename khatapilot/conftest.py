"""Fixtures shared by every test suite (backend, sandbox, data)."""

from __future__ import annotations

import pytest

from data.generate_shops import generate

TEST_POPULATION = 120  # smaller than the default 400 so the suite stays fast; the hero shop is identical


@pytest.fixture(scope="session")
def generated_dir(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("generated")
    generate(n_shops=TEST_POPULATION, seed=42, out_dir=out_dir)
    return out_dir


@pytest.fixture(scope="session")
def credit_model_path(generated_dir, tmp_path_factory):
    """A credit model trained once per test run on the test population (no ablation, to stay fast)."""
    import warnings

    import pandas as pd

    from app.modules.credit.training import train

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        model, _ = train(pd.read_csv(generated_dir / "shops.csv"), pd.read_csv(generated_dir / "daily.csv"),
                         ablation=False)
    path = tmp_path_factory.mktemp("models") / "credit_model.joblib"
    model.save(path)
    return path
