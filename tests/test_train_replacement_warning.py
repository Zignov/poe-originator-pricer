"""Training overwrites the model in place; it has to say when that is a downgrade."""
from __future__ import annotations

import json

from src.config import load_config
from src.ml.train import METADATA_NAME, _warn_if_thinner_than_current


def _config(tmp_path, monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(type(cfg), "model_dir", property(lambda _self: tmp_path))
    return cfg


def _sidecar(tmp_path, dataset_size) -> None:
    (tmp_path / METADATA_NAME).write_text(
        json.dumps({"dataset_size": dataset_size}), encoding="utf-8"
    )


def test_warns_when_the_new_dataset_is_a_fraction_of_the_old(tmp_path, monkeypatch):
    _sidecar(tmp_path, 4346)
    messages: list[str] = []
    _warn_if_thinner_than_current(_config(tmp_path, monkeypatch), 500, messages.append)
    assert messages and messages[0].startswith("warning:")
    assert "500" in messages[0] and "4346" in messages[0]


def test_stays_quiet_when_the_dataset_has_grown(tmp_path, monkeypatch):
    _sidecar(tmp_path, 4346)
    messages: list[str] = []
    _warn_if_thinner_than_current(_config(tmp_path, monkeypatch), 5000, messages.append)
    assert messages == []


def test_stays_quiet_on_a_modest_shrink(tmp_path, monkeypatch):
    """Pruning stale listings shrinks the set every day; that is not a downgrade."""
    _sidecar(tmp_path, 4346)
    messages: list[str] = []
    _warn_if_thinner_than_current(_config(tmp_path, monkeypatch), 4000, messages.append)
    assert messages == []


def test_stays_quiet_with_no_model_to_replace(tmp_path, monkeypatch):
    messages: list[str] = []
    _warn_if_thinner_than_current(_config(tmp_path, monkeypatch), 500, messages.append)
    assert messages == []


def test_survives_an_unreadable_sidecar(tmp_path, monkeypatch):
    (tmp_path / METADATA_NAME).write_text("{not json", encoding="utf-8")
    messages: list[str] = []
    _warn_if_thinner_than_current(_config(tmp_path, monkeypatch), 500, messages.append)
    assert messages == []
