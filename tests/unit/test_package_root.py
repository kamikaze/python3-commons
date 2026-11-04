import importlib.metadata
import subprocess
import sys

import pytest

import python3_commons


def test_version_matches_metadata():
    assert python3_commons.__version__ == importlib.metadata.version('python3-commons')


def test_dist_name_and_importlib_deprecated():
    with pytest.warns(DeprecationWarning, match='1.0'):
        assert python3_commons.dist_name == 'python3_commons'

    with pytest.warns(DeprecationWarning, match='1.0'):
        assert python3_commons.importlib is importlib


def test_unknown_attribute():
    with pytest.raises(AttributeError):
        _ = python3_commons.nope


def test_dir_and_lazy_version_in_subprocess():
    code = (
        'import python3_commons as p, importlib.metadata as m;'
        'assert "__version__" not in vars(p); assert "importlib" not in vars(p); assert "dist_name" not in vars(p);'
        'assert p.__version__ == m.version("python3-commons")'
    )
    subprocess.run([sys.executable, '-W', 'error', '-c', code], check=True)  # noqa: S603
