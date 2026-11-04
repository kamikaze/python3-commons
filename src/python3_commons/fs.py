# pyright: strict
import warnings
from collections.abc import Generator
from pathlib import Path

__all__ = ['iter_files']


def _iter_files(
    root: Path, *, recursive: bool, include_hidden: bool | None, follow_symlinks: bool | None, reasons: set[str]
) -> Generator[Path]:
    for item in root.iterdir():
        if item.is_file():
            if item.name.startswith('.'):
                if include_hidden is False:
                    continue

                if include_hidden is None:
                    reasons.add('hidden')

            yield item
        elif item.is_dir() and recursive and (include_hidden or not item.name.startswith('.')):
            if item.is_symlink():
                if follow_symlinks is False:
                    continue

                if follow_symlinks is None:
                    reasons.add('symlink')

            yield from _iter_files(
                item,
                recursive=recursive,
                include_hidden=include_hidden,
                follow_symlinks=follow_symlinks,
                reasons=reasons,
            )


def iter_files(
    root: Path, *, recursive: bool = True, include_hidden: bool | None = None, follow_symlinks: bool | None = None
) -> Generator[Path]:
    """Yield the files under `root`.

    `include_hidden=None` and `follow_symlinks=None` keep the 0.29 behaviour (hidden files are yielded, hidden
    directories are skipped, directory symlinks are followed) and warn when the result depends on it. In 1.0 both
    default to `False`.
    """
    reasons: set[str] = set()
    warned: set[str] = set()

    for item in _iter_files(
        root, recursive=recursive, include_hidden=include_hidden, follow_symlinks=follow_symlinks, reasons=reasons
    ):
        if reasons - warned:
            if 'hidden' in reasons and 'hidden' not in warned:
                warnings.warn(
                    'iter_files: hidden files are yielded today; in 1.0 they are skipped unless include_hidden=True. '
                    'Pass include_hidden explicitly',
                    DeprecationWarning,
                    stacklevel=2,
                )

            if 'symlink' in reasons and 'symlink' not in warned:
                warnings.warn(
                    'iter_files: directory symlinks are followed today; in 1.0 they are not unless '
                    'follow_symlinks=True. Pass follow_symlinks explicitly',
                    DeprecationWarning,
                    stacklevel=2,
                )

            warned |= reasons

        yield item
