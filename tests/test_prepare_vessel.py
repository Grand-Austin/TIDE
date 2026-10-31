from pathlib import Path

import pytest

from pipeline.prepare_vessel import FILES, fetch_sources


def test_existing_corrupt_source_is_rejected_before_network_or_conversion(tmp_path):
    name = next(iter(FILES))
    source = tmp_path / name
    source.parent.mkdir(parents=True)
    source.write_bytes(b"corrupt source")
    with pytest.raises(ValueError, match="checksum mismatch"):
        fetch_sources(tmp_path)
    assert source.read_bytes() == b"corrupt source"
