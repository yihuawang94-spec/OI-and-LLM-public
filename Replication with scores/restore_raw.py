"""Restore the two saved response logs from their lossless archive parts."""

from pathlib import Path, PurePosixPath
import hashlib
import json
import os
import tempfile
import zipfile


BLOCK_SIZE = 1024 * 1024


def _digest(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _checked_path(root, relative):
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in relative:
        raise ValueError(f"Unsafe archive path: {relative}")
    result = root.joinpath(*path.parts)
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Archive path leaves the package: {relative}")
    return result


def _matches(path, expected):
    return _digest(path) == (expected["size_bytes"], expected["sha256"])


def restore_raw(package_root=None):
    """Verify existing logs and restore missing logs without changing their bytes.

    Existing files that differ from the original archive cause an error and
    remain untouched. All archive and restored-file hashes must pass before
    any missing raw file is placed in the package.
    """
    root = Path(package_root).resolve() if package_root is not None else Path(__file__).resolve().parent
    archive_root = root / "Raw response archive"
    manifest = json.loads((archive_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["format"] != 1:
        raise ValueError("Unsupported raw response archive format.")

    missing = []
    for entry in manifest["files"]:
        target = _checked_path(root, entry["path"])
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or not _matches(target, entry):
                raise ValueError(f"Existing raw file differs from the original; left untouched: {entry['path']}")
        else:
            missing.append(entry)

    if not missing:
        print(f"Saved raw responses: verified {len(manifest['files'])} files; no restoration needed.")
        return []

    archive = manifest["archive"]
    with tempfile.TemporaryDirectory(prefix=".restore-raw-", dir=root) as temporary:
        temporary_root = Path(temporary)
        zip_path = temporary_root / "saved-responses.zip"
        whole_digest = hashlib.sha256()
        whole_size = 0
        with zip_path.open("wb") as destination:
            for part in archive["parts"]:
                source = _checked_path(archive_root, part["name"])
                part_digest = hashlib.sha256()
                part_size = 0
                with source.open("rb") as stream:
                    for block in iter(lambda: stream.read(BLOCK_SIZE), b""):
                        destination.write(block)
                        part_digest.update(block)
                        whole_digest.update(block)
                        part_size += len(block)
                        whole_size += len(block)
                if (part_size, part_digest.hexdigest()) != (part["size_bytes"], part["sha256"]):
                    raise ValueError(f"Raw archive part failed its integrity check: {part['name']}")
        if (whole_size, whole_digest.hexdigest()) != (archive["size_bytes"], archive["sha256"]):
            raise ValueError("Reassembled raw archive failed its integrity check.")

        staged = []
        with zipfile.ZipFile(zip_path) as saved:
            names = saved.namelist()
            expected_names = [entry["path"] for entry in manifest["files"]]
            if len(names) != len(set(names)) or set(names) != set(expected_names):
                raise ValueError("Raw archive contents differ from the manifest.")
            for index, entry in enumerate(missing):
                restored = temporary_root / f"restored-{index}"
                with saved.open(entry["path"]) as source, restored.open("wb") as destination:
                    for block in iter(lambda: source.read(BLOCK_SIZE), b""):
                        destination.write(block)
                if not _matches(restored, entry):
                    raise ValueError(f"Restored raw file failed its integrity check: {entry['path']}")
                staged.append((restored, _checked_path(root, entry["path"])))

        # Validate every destination before placing any restored raw file.
        for _, target in staged:
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"Raw file appeared during restoration; left untouched: {target}")
        for restored, target in staged:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(restored, target)

    print(f"Saved raw responses: verified {len(manifest['files'])} files; restored {len(missing)}.")
    return [entry["path"] for entry in missing]


if __name__ == "__main__":
    restore_raw()
