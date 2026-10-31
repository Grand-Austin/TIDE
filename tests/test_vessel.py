from pathlib import Path

import numpy as np
import pytest

from game.simulation.vessel import RAW, RAW_SHA256, prepare_s175, load_s175


@pytest.fixture
def prepared(tmp_path):
    if not RAW.exists():
        pytest.skip("Run python -m pipeline.prepare_vessel to obtain pinned S175 source")
    return prepare_s175(RAW,tmp_path)


def test_preparation_is_numerically_reproducible_and_physically_consistent(prepared,tmp_path):
    first=load_s175(prepared)
    second=load_s175(prepare_s175(RAW,tmp_path/'second'))
    for key in ('M','MA','D','G','force_rao'):
        np.testing.assert_array_equal(getattr(first,key),getattr(second,key))
    np.testing.assert_allclose(first.M@first.Minv,np.eye(6),atol=1e-12)
    assert np.linalg.eigvalsh(first.M).min()>0
    assert np.linalg.eigvalsh(first.D).min()>0
    assert np.linalg.eigvalsh(first.G[2:5,2:5]).min()>0
    assert first.main['Lpp']==175
    assert first.main['T']==9.5
    assert first.main['B']==25.4
    assert first.force_rao.shape==(6,len(first.freqs),len(first.headings))
    assert not first.M.flags.writeable


def test_wrong_source_is_rejected(tmp_path):
    source=tmp_path/'bad.mat'
    source.write_bytes(b'not the pinned vessel')
    with pytest.raises(ValueError,match='checksum'):
        prepare_s175(source,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_prepared_checksum_rejects_tampering(prepared):
    prepared.write_bytes(prepared.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='checksum'):
        load_s175(prepared)


def test_load_never_downloads_missing_data(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_s175(tmp_path/'absent.npz')
