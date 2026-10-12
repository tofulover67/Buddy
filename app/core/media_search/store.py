#!/usr/bin/env python3
"""
The media search index: one SQLite file. Buddy's own is
~/.buddy/media_search/index.sqlite, shared by every tool and project; a
Resolve project can keep one of its own in a folder the person picks
(Footage Search), INDEX_NAME there, so it goes where the job's files go.

  files      a file that's been looked at: its path, the size and time
             changed it had then (a file that's changed since is embedded
             again), what kind it is, how long, and - if it couldn't be read
             - why.
  pieces     what was embedded of it: a still, a frame at a time into the
             footage (standing for `span` seconds from there), or a window of
             sound. Each with its vector, 768 float16s.

A file's pieces go in, and its old ones out, in one transaction: a search
never sees half a file, and an index stopped halfway picks up at the next
file. Vectors are read into one matrix the first time something searches and
kept until the index changes - 10,000 pieces are 30 MB and compare in
milliseconds.

An index in a folder of the person's can turn up on another computer, or the
drive under another letter: files are known by where they are, so adopt()
takes entries over for the same files where they are now - same name and
size, changed within a couple of seconds of each other, and nothing left at
the old place. Such an index doesn't use SQLite's write-ahead log (shared=
True), which network drives can't keep.

sqlite3 and NumPy only; no Qt. Safe from any thread: each call opens the
file for itself, and SQLite keeps writers apart.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from core import tool_dirs

DIM = 768
INDEX_NAME = "Buddy footage index.sqlite"       # a project's own index, in the folder picked for it
SAME_TIME_NS = 2_000_000_000                    # two drives' clocks for one file: FAT keeps even seconds
KIND_IMAGE, KIND_FRAME, KIND_SOUND = "image", "frame", "sound"
PICTURES = (KIND_IMAGE, KIND_FRAME)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    model TEXT NOT NULL,
    kind TEXT NOT NULL,
    duration REAL NOT NULL DEFAULT 0,
    problem TEXT NOT NULL DEFAULT '',
    indexed REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pieces (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    start REAL NOT NULL DEFAULT 0,
    span REAL NOT NULL DEFAULT 0,
    vector BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS pieces_file ON pieces(file_id);
"""


def default_path() -> Path:
    return Path(tool_dirs.buddy_dir()) / "media_search" / "index.sqlite"


def key(path) -> str:
    """A file's name in the index: one spelling per file."""
    return os.path.normcase(os.path.abspath(str(path)))


@dataclass
class Piece:
    kind: str               # KIND_IMAGE, KIND_FRAME or KIND_SOUND
    vector: list            # DIM floats
    start: float = 0.0      # seconds into the file
    span: float = 0.0       # how many seconds from there it stands for


@dataclass
class Hit:
    path: str               # as key() spells it
    score: float            # cosine similarity, best piece of the file's
    kind: str
    start: float
    span: float


class Store:
    def __init__(self, path: Path | None = None, model: str = "", shared: bool = False):
        self.path = Path(path) if path else default_path()
        self.model = model              # vectors of another model's aren't these: such files are embedded again
        self.shared = shared            # in a folder of the person's, perhaps on a network drive: no write-ahead log
        self._lock = threading.Lock()
        self._matrix = None             # (version, vectors, file ids, kinds, starts, spans, {file id: path})
        self._made = False              # the file is made when first used, not when a page is
        self._changes = 0               # writes made here: the matrix is read again after one

    @contextlib.contextmanager
    def _open(self):
        """The file, opened for one piece of work: committed and closed
        after it (sqlite3's own `with` commits but leaves it open)."""
        if not self._made:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(str(self.path), timeout=30)
        try:
            db.execute("PRAGMA foreign_keys = ON")
            if not self._made:
                db.execute("PRAGMA journal_mode = " + ("DELETE" if self.shared else "WAL"))
                db.executescript(_SCHEMA)
                self._made = True
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    # ------------------------------------------------------------- files --

    @staticmethod
    def _stat(path) -> tuple[int, int] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return st.st_size, st.st_mtime_ns

    def state(self, paths) -> dict[str, str]:
        """{key: "done" | "failed" | "new"} for each of these files as they
        are on disk now - "new" for one never looked at, changed since, gone
        from disk or embedded with another model."""
        wanted = {key(p): p for p in paths}
        known = {}
        with self._open() as db:
            for row in db.execute("SELECT path, size, mtime_ns, model, problem FROM files"):
                if row[0] in wanted:
                    known[row[0]] = row[1:]
        out = {}
        for k, path in wanted.items():
            row, stat = known.get(k), self._stat(path)
            if row is None or stat is None or (row[0], row[1]) != stat or row[2] != self.model:
                out[k] = "new"
            else:
                out[k] = "failed" if row[3] else "done"
        return out

    def problems(self, paths) -> dict[str, str]:
        """{key: why it couldn't be read} for those of these files that couldn't."""
        wanted = {key(p) for p in paths}
        with self._open() as db:
            return {row[0]: row[1] for row in db.execute("SELECT path, problem FROM files WHERE problem != ''")
                    if row[0] in wanted}

    def put(self, path, kind: str, pieces: list[Piece], duration: float = 0.0, problem: str = "") -> None:
        """What was embedded of a file as it is now, in place of whatever
        was there for it. problem: why nothing could be - remembered, so it
        isn't tried again until the file changes."""
        import numpy as np
        stat = self._stat(path) or (0, 0)
        with self._lock, self._open() as db:
            db.execute("DELETE FROM files WHERE path = ?", (key(path),))
            cursor = db.execute(
                "INSERT INTO files (path, size, mtime_ns, model, kind, duration, problem, indexed) VALUES (?,?,?,?,?,?,?,?)",
                (key(path), stat[0], stat[1], self.model, kind, float(duration), problem, time.time()))
            db.executemany(
                "INSERT INTO pieces (file_id, kind, start, span, vector) VALUES (?,?,?,?,?)",
                [(cursor.lastrowid, p.kind, float(p.start), float(p.span),
                  np.asarray(p.vector, dtype=np.float16).tobytes()) for p in pieces if len(p.vector) == DIM])
            self._changes += 1

    def adopt(self, paths) -> int:
        """Entries made for these files where they were - another drive
        letter, another computer - taken over for where they are now. How
        many were. Only a file with the same name and size, changed within
        SAME_TIME_NS of the entry's, and only an entry whose own file isn't
        there any more: two copies of a clip each keep their own."""
        wanted = {key(p): p for p in paths}
        taken = 0
        with self._lock, self._open() as db:
            rows = db.execute("SELECT id, path, size, mtime_ns FROM files WHERE model = ?", (self.model,)).fetchall()
            known = {row[1] for row in rows}
            orphans = {}
            for file_id, path, size, mtime_ns in rows:
                if path not in wanted:
                    orphans.setdefault((os.path.basename(path), size), []).append((file_id, path, mtime_ns))
            for k, path in wanted.items():
                stat = self._stat(path)
                if k in known or stat is None:
                    continue
                for file_id, old, mtime_ns in orphans.get((os.path.basename(k), stat[0]), []):
                    if abs(mtime_ns - stat[1]) <= SAME_TIME_NS and not os.path.exists(old):
                        db.execute("UPDATE files SET path = ?, mtime_ns = ? WHERE id = ?", (k, stat[1], file_id))
                        orphans[(os.path.basename(k), stat[0])].remove((file_id, old, mtime_ns))
                        taken += 1
                        break
            if taken:
                self._changes += 1
        return taken

    def take_from(self, other: "Store", paths) -> int:
        """Copies what `other` has for these files into this index, for those
        it hasn't got itself - a project's entries following it to another
        index, nothing embedded again. How many files came over."""
        wanted = {key(p) for p in paths}
        have = {k for k, state in self.state(paths).items() if state != "new"}
        with other._open() as src:
            files = [row for row in src.execute(
                "SELECT id, path, size, mtime_ns, model, kind, duration, problem, indexed FROM files WHERE model = ?",
                (self.model,)) if row[1] in wanted and row[1] not in have]
            pieces = {row[0]: src.execute("SELECT kind, start, span, vector FROM pieces WHERE file_id = ?",
                                          (row[0],)).fetchall() for row in files}
        with self._lock, self._open() as db:
            for row in files:
                db.execute("DELETE FROM files WHERE path = ?", (row[1],))
                cursor = db.execute(
                    "INSERT INTO files (path, size, mtime_ns, model, kind, duration, problem, indexed) VALUES (?,?,?,?,?,?,?,?)",
                    row[1:])
                db.executemany("INSERT INTO pieces (file_id, kind, start, span, vector) VALUES (?,?,?,?,?)",
                               [(cursor.lastrowid, *piece) for piece in pieces[row[0]]])
            if files:
                self._changes += 1
        return len(files)

    def forget(self, paths) -> None:
        with self._lock, self._open() as db:
            db.executemany("DELETE FROM files WHERE path = ?", [(key(p),) for p in paths])
            self._changes += 1

    def counts(self) -> tuple[int, int]:
        """(files with something to find, pieces)."""
        with self._open() as db:
            files = db.execute("SELECT COUNT(DISTINCT file_id) FROM pieces").fetchone()[0]
            pieces = db.execute("SELECT COUNT(*) FROM pieces").fetchone()[0]
        return files, pieces

    # ------------------------------------------------------------ search --

    def _load(self):
        """Every vector, unit length, as one matrix - read again only when
        the file has changed since."""
        import numpy as np
        with self._lock, self._open() as db:
            # A fresh connection's data_version says nothing of earlier writes:
            # the file and its write-ahead log are what change with one.
            version = self._changes, os.path.getmtime(self.path), self._wal_size()
            if self._matrix is not None and self._matrix[0] == version:
                return self._matrix
            names = dict(db.execute("SELECT id, path FROM files WHERE problem = '' AND model = ?", (self.model,)))
            rows = [r for r in db.execute("SELECT file_id, kind, start, span, vector FROM pieces") if r[0] in names]
        vectors = np.zeros((len(rows), DIM), dtype=np.float32)
        for i, row in enumerate(rows):
            vectors[i] = np.frombuffer(row[4], dtype=np.float16, count=DIM)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        vectors /= np.where(norms > 0, norms, 1)
        self._matrix = (version, vectors, np.array([r[0] for r in rows], dtype=np.int64),
                        np.array([r[1] for r in rows], dtype=object), np.array([r[2] for r in rows], dtype=np.float32),
                        np.array([r[3] for r in rows], dtype=np.float32), names)
        return self._matrix

    def _wal_size(self) -> int:
        try:
            return os.path.getsize(str(self.path) + "-wal")
        except OSError:
            return 0

    def _scores(self, vector, paths, kinds):
        """(scores, row numbers) of the pieces a search may find: those of
        `paths` (None: every file's) and of `kinds` (None: any)."""
        import numpy as np
        _v, vectors, file_ids, piece_kinds, _s, _sp, names = self._load()
        if not len(vectors):
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.int64)
        keep = np.ones(len(vectors), dtype=bool)
        if paths is not None:
            wanted = {key(p) for p in paths}
            ids = np.array([i for i, name in names.items() if name in wanted], dtype=np.int64)
            keep &= np.isin(file_ids, ids)
        if kinds is not None:
            keep &= np.isin(piece_kinds, list(kinds))
        rows = np.nonzero(keep)[0]
        query = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if not len(rows) or not norm:
            return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.int64)
        return vectors[rows] @ (query / norm), rows

    def search(self, vector, paths=None, kinds=None, limit: int = 60) -> list[Hit]:
        """The files nearest a vector, best first - each by its nearest
        piece, which says where in the file to look."""
        import numpy as np
        scores, rows = self._scores(vector, paths, kinds)
        _v, _vectors, file_ids, piece_kinds, starts, spans, names = self._load()
        best = {}
        for i in np.argsort(-scores):
            file_id = int(file_ids[rows[i]])
            if file_id not in best:
                row = rows[i]
                best[file_id] = Hit(names[file_id], float(scores[i]), str(piece_kinds[row]), float(starts[row]),
                                    float(spans[row]))
                if len(best) >= limit:
                    break
        return list(best.values())

    def moments(self, vector, paths=None, kinds=PICTURES, limit: int = 40, apart: float = 8.0) -> list[Hit]:
        """The moments nearest a vector, best first: several of one file can
        be found, but not two within `apart` seconds of each other - a shot
        is found once, at its best frame."""
        import numpy as np
        scores, rows = self._scores(vector, paths, kinds)
        _v, _vectors, file_ids, piece_kinds, starts, spans, names = self._load()
        found, taken = [], {}
        for i in np.argsort(-scores):
            row = rows[i]
            file_id, start = int(file_ids[row]), float(starts[row])
            if any(abs(start - other) < apart for other in taken.get(file_id, ())):
                continue
            taken.setdefault(file_id, []).append(start)
            found.append(Hit(names[file_id], float(scores[i]), str(piece_kinds[row]), start, float(spans[row])))
            if len(found) >= limit:
                break
        return found

    def vector_of(self, path, kinds=None):
        """A file's own vector - the mean of its pieces' - to find others
        like it; None for a file that isn't in the index."""
        import numpy as np
        _v, vectors, file_ids, piece_kinds, _s, _sp, names = self._load()
        file_id = next((i for i, name in names.items() if name == key(path)), None)
        if file_id is None:
            return None
        keep = file_ids == file_id
        if kinds is not None:
            keep &= np.isin(piece_kinds, list(kinds))
        if not keep.any():
            return None
        return vectors[keep].mean(axis=0)
