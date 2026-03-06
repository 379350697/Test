from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

from .strategies import BaseStrategy, register_strategy_class


def _load_module_from_file(path: Path) -> ModuleType:
    module_name = f"quantx_user_strategy_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _register_from_module(mod: ModuleType) -> list[str]:
    loaded: list[str] = []
    explicit = getattr(mod, "STRATEGY_EXPORTS", None)
    if explicit:
        for cls in explicit:
            register_strategy_class(cls)
            loaded.append(cls.name)
        return loaded

    for _, obj in inspect.getmembers(mod, inspect.isclass):
        if issubclass(obj, BaseStrategy) and obj is not BaseStrategy and obj.__module__ == mod.__name__:
            register_strategy_class(obj)
            loaded.append(obj.name)
    return loaded


def load_strategy_repos(paths: list[str] | None) -> dict[str, list[str]]:
    if not paths:
        return {"loaded": [], "files": []}

    loaded_names: list[str] = []
    loaded_files: list[str] = []
    for raw in paths:
        p = Path(raw)
        targets: list[Path] = []
        if p.is_file() and p.suffix == ".py":
            targets = [p]
        elif p.is_dir():
            targets = sorted(x for x in p.glob("*.py") if x.is_file())
        else:
            continue

        for t in targets:
            mod = _load_module_from_file(t)
            names = _register_from_module(mod)
            if names:
                loaded_names.extend(names)
                loaded_files.append(str(t))

    return {"loaded": sorted(set(loaded_names)), "files": loaded_files}
