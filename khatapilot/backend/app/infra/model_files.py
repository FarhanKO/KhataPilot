"""Saving and loading trained models with an integrity check.

A pickled model is code. Each model file is written with a SHA-256 sidecar by its training script,
and loaded only if the file still matches the sidecar and is the expected class.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypeVar

import joblib

T = TypeVar("T")


class ModelIntegrityError(Exception):
    """The saved model is missing, altered, or not the expected kind of model."""


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_checked(model: object, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    value = digest(path)
    path.with_name(path.name + ".sha256").write_text(value, encoding="utf-8")
    return value


def load_checked(path: Path, kind: type[T], *, name: str, train_with: str) -> T:
    sidecar = path.with_name(path.name + ".sha256")
    if not path.exists() or not sidecar.exists():
        raise ModelIntegrityError(f"No trained model at {path}. Run `python {train_with}`.")
    if digest(path) != sidecar.read_text(encoding="utf-8").strip():
        raise ModelIntegrityError(f"The saved {name} model does not match its checksum; refusing to load it.")
    model = joblib.load(path)
    if not isinstance(model, kind):
        raise ModelIntegrityError(f"The file is not a {name} model.")
    return model
