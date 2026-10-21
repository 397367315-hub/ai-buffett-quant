"""Column storage for unchanged book scripts on memory-limited hosts.

Only the representation changes: every input row and field remains available.
The list interface keeps the original scripts' normalization and grouping code.
"""
from array import array
from collections.abc import MutableMapping
import json
from pathlib import Path
import os
import tempfile


class _Column:
    def __init__(self, size=0):
        self.numbers = array('d', [0.0]) * size
        self.tags = bytearray(size)
        self.strings = {}
        self.string_values = []
        self.string_ids = array('I', [0]) * size
        self.exact = {}

    def append(self, value):
        self.numbers.append(0.0)
        self.tags.append(0)
        self.string_ids.append(0)
        self.set(len(self.tags) - 1, value)

    def set(self, i, value):
        self.exact.pop(i, None)
        if value is None:
            self.tags[i] = 0
        elif isinstance(value, str):
            self.tags[i] = 3
            idx = self.strings.get(value)
            if idx is None:
                idx = len(self.string_values)
                self.strings[value] = idx
                self.string_values.append(value)
            self.string_ids[i] = idx
        elif isinstance(value, bool):
            self.tags[i] = 4
            self.numbers[i] = int(value)
        elif isinstance(value, (float, int)) and (not isinstance(value, int) or abs(value) <= 2 ** 53):
            self.tags[i] = 1 if isinstance(value, int) else 2
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
