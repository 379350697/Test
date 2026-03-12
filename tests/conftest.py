from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest


class WorkspaceTmpPathFactory:
    def __init__(self, root: Path):
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def getbasetemp(self) -> Path:
        return self._root

    def mktemp(self, basename: str, numbered: bool = True) -> Path:
        safe_name = ''.join(ch if ch.isalnum() or ch in {'-', '_'} else '-' for ch in basename)
        leaf = f'{safe_name}-{uuid4().hex}' if numbered else safe_name
        path = self._root / leaf
        path.mkdir(parents=True, exist_ok=False)
        return path


@pytest.fixture(scope='session')
def tmp_path_factory() -> WorkspaceTmpPathFactory:
    return WorkspaceTmpPathFactory(Path('writable_tmp_env') / 'pytest-fixtures')


@pytest.fixture
def tmp_path(tmp_path_factory: WorkspaceTmpPathFactory, request: pytest.FixtureRequest) -> Path:
    name = request.node.name or 'tmp-path'
    return tmp_path_factory.mktemp(name, numbered=True)
