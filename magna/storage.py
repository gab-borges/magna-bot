"""Persistent JSON files kept in DATA_DIR."""
import json
import os
import tempfile


def data_file(name):
    """Return the path of a data file, creating DATA_DIR when needed."""
    directory = os.getenv('DATA_DIR', '.')
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, name)


def load_json(path, default):
    """Read a JSON file, or return `default` when it doesn't exist yet."""
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json(path, data):
    """Write a JSON file atomically.

    The data goes to a temporary file that then replaces the old one, so a
    crash mid-write leaves the previous contents intact instead of a
    truncated file.
    """
    directory = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(
        dir=directory, prefix=f".{os.path.basename(path)}.", suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise
