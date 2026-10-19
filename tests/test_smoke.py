"""Scaffold smoke tests: packages import and the generator config is well-formed."""

from pathlib import Path

import yaml

import generator
import isbank


def test_isbank_has_version():
    assert isbank.__version__


def test_generator_is_importable():
    assert generator.__doc__


def test_generator_config_has_seed_and_profiles(repo_root: Path):
    config = yaml.safe_load((repo_root / "generator" / "config.yaml").read_text(encoding="utf-8"))

    assert isinstance(config["seed"], int)
    assert {"dev", "full"} <= config["profiles"].keys()
    assert config["profiles"]["dev"]["customers"] < config["profiles"]["full"]["customers"]


def test_isbank_does_not_import_generator():
    """The platform library must never depend on the simulator (it could leak ground truth)."""
    src = Path(isbank.__file__).parent
    offenders = [
        p.name
        for p in src.rglob("*.py")
        if "import generator" in p.read_text(encoding="utf-8")
        or "from generator" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
