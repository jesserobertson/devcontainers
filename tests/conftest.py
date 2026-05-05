import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--gpu",
        action="store_true",
        default=False,
        help="Run tests that require an NVIDIA GPU",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "gpu: requires NVIDIA GPU — run locally with pytest --gpu",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--gpu"):
        skip = pytest.mark.skip(reason="pass --gpu to run GPU tests")
        for item in items:
            if "gpu" in item.keywords:
                item.add_marker(skip)
