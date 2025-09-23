from collections.abc import Generator
from pathlib import Path


def iter_files(root: Path, *, recursive: bool = True) -> Generator[Path]:
    for item in root.iterdir():
        if item.is_file():
            yield item
        elif item.is_dir() and recursive and not item.name.startswith('.'):
            yield from iter_files(item)
