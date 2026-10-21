"""Column storage for unchanged book scripts on memory-limited hosts.

Only the representation changes: every input row and field remains available.
The list interface keeps the original scripts' normalization and grouping code.
"""
from collections.abc import MutableMapping
import json
import mmap
from pathlib import Path
import os
import struct
import tempfile


class _MMapVector:
    """A growable typed vector backed by a temporary file mapping."""

    _FLUSH_ROWS = 32768

    def __init__(self, fmt, size=0):
        self.fmt = fmt
        self.item_size = struct.calcsize(fmt)
        self._file = tempfile.TemporaryFile()
        self._map = None
        self._view = None
        self._capacity = 0
        self._size = 0
        self._resize(max(256, size))
        self._size = size

    def _resize(self, capacity):
        if self._view is not None:
            self._view.release()
            self._view = None
        if self._map is not None:
            self._map.flush()
            self._map.close()
        self._capacity = max(1, int(capacity))
        self._file.truncate(self._capacity * self.item_size)
        self._map = mmap.mmap(self._file.fileno(), self._capacity * self.item_size, access=mmap.ACCESS_WRITE)
        self._view = memoryview(self._map).cast(self.fmt)

    def append(self, value=0):
        if self._size >= self._capacity:
            self._resize(self._capacity * 2)
        self._view[self._size] = value
        self._size += 1
        if self._size % self._FLUSH_ROWS == 0:
            self.flush()

    def __len__(self):
        return self._size

    def __getitem__(self, index):
        if index < 0:
            index += self._size
        if index < 0 or index >= self._size:
            raise IndexError(index)
        return self._view[index]

    def __setitem__(self, index, value):
        if index < 0:
            index += self._size
        if index < 0 or index >= self._size:
            raise IndexError(index)
        self._view[index] = value

    def flush(self):
        if self._map is None:
            return
        self._map.flush()
        try:
            self._map.madvise(mmap.MADV_DONTNEED)
        except (AttributeError, OSError, ValueError):
            pass

    def close(self):
        view, mapping, file_obj = self._view, self._map, self._file
        self._view = self._map = None
        if view is not None:
            view.release()
        if mapping is not None:
            mapping.close()
        if file_obj is not None:
            file_obj.close()
        self._file = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class _Column:
    def __init__(self, size=0):
        self.numbers = None
        self.tags = _MMapVector('B', size)
        self.strings = {}
        self.string_values = []
        self.string_ids = None
        self.exact = {}

    def _ensure_numbers(self):
        if self.numbers is None:
            self.numbers = _MMapVector('d')
            for _ in range(len(self.tags)):
                self.numbers.append(0.0)

    def _ensure_string_ids(self):
        if self.string_ids is None:
            self.string_ids = _MMapVector('I')
            for _ in range(len(self.tags)):
                self.string_ids.append(0)

    def append(self, value):
        self.tags.append(0)
        if self.numbers is not None:
            self.numbers.append(0.0)
        if self.string_ids is not None:
            self.string_ids.append(0)
        self.set(len(self.tags) - 1, value)

    def set(self, i, value):
        self.exact.pop(i, None)
        if value is None:
            self.tags[i] = 0
        elif isinstance(value, str):
            self.tags[i] = 3
            self._ensure_string_ids()
            idx = self.strings.get(value)
            if idx is None:
                idx = len(self.string_values)
                self.strings[value] = idx
                self.string_values.append(value)
            self.string_ids[i] = idx
        elif isinstance(value, bool):
            self.tags[i] = 4
            self._ensure_numbers()
            self.numbers[i] = int(value)
        elif isinstance(value, (float, int)) and (not isinstance(value, int) or abs(value) <= 2 ** 53):
            self.tags[i] = 1 if isinstance(value, int) else 2
            self._ensure_numbers()
            self.numbers[i] = value
        else:
            self.tags[i] = 5
            self.exact[i] = value

    def get(self, i):
        tag = self.tags[i]
        if tag == 0:
            return None
        if tag == 3:
            return self.string_values[self.string_ids[i]]
        if tag == 5:
            return self.exact[i]
        if tag == 1:
            return int(self.numbers[i])
        if tag == 4:
            return bool(self.numbers[i])
        return self.numbers[i]

    def close(self):
        for vector in (self.numbers, self.tags, self.string_ids):
            if vector is not None:
                vector.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


class FeatureRow(MutableMapping):
    __slots__ = ('table', 'index')

    def __init__(self, table, index):
        self.table, self.index = table, index

    def __getitem__(self, key):
        if key not in self.table.columns:
            raise KeyError(key)
        return self.table.columns[key].get(self.index)

    def __setitem__(self, key, value):
        if key not in self.table.columns:
            self.table.columns[key] = _Column(len(self.table))
        self.table.columns[key].set(self.index, value)

    def __delitem__(self, key):
        raise TypeError('Feature fields cannot be deleted')

    def __iter__(self):
        return iter(self.table.columns)

    def __len__(self):
        return len(self.table.columns)


class FeatureRows(list):
    def __init__(self, rows):
        super().__init__()
        self.columns = {}
        for row in rows:
            for key in row:
                if key not in self.columns:
                    self.columns[key] = _Column(len(self))
            for key, column in self.columns.items():
                column.append(row.get(key))
            super().append(FeatureRow(self, len(self)))

    def close(self):
        for column in self.columns.values():
            column.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def load_feature_rows(path):
    """Stream large top-level arrays; retain small legacy wrapper support."""
    import ijson
    path = Path(path)
    with path.open('rb') as f:
        first = f.read(256).lstrip()[:1]
        f.seek(0)
        if first == b'[':
            return FeatureRows(ijson.items(f, 'item', use_float=True))
        return json.load(f)


class CachedDailyRows:
    """Repeatable full-market rows with at most one trading day in memory."""
    def __init__(self, files):
        self.files = list(files)  # (path, row_count)

    def __iter__(self):
        for path, count in self.files:
            with Path(path).open(encoding='utf-8') as f:
                yield from json.load(f)

    def __len__(self):
        return sum(count for path, count in self.files)

    def without_day(self, day):
        return CachedDailyRows([(p, n) for p, n in self.files if Path(p).stem != day])


def write_feature_rows(path, rows):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.features-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('[')
            for i, row in enumerate(rows):
                if i:
                    f.write(',')
                json.dump(dict(row), f, ensure_ascii=False, separators=(',', ':'))
            f.write(']')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
