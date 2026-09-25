"""The Docker image ships every data file the engine reads at runtime."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# What the engine reads from settings.data_dir (/data in the image). A missing file does not crash
# the service: it starts and degrades quietly (no slots, no ordering, empty no-article answers).
RUNTIME_DATA = {
    "data/kit/": "/data/kit/",
    "data/slot_lexicon.json": "/data/",
    "data/dependencies.json": "/data/",
    "data/appliance_exclusions.json": "/data/",
    "results.jsonl": "/data/results.jsonl",  # the no-article kit table (cache/no_siis.py)
}


def _copies() -> dict[str, str]:
    """Source -> destination for every COPY in api/Dockerfile."""
    copies: dict[str, str] = {}
    for line in (ROOT / "api" / "Dockerfile").read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts[:1] == ["COPY"]:
            copies.update({source: parts[-1] for source in parts[1:-1]})
    return copies


def test_the_image_ships_every_runtime_data_file():
    copies = _copies()
    assert {source: copies.get(source) for source in RUNTIME_DATA} == RUNTIME_DATA
