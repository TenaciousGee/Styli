"""Background work: anything slow (web lookups, downloads) runs in a thread pool so the window
never freezes. run_task hands the result back to the window's thread. CoverStore loads covers as
they're needed, most important first, and drops queued previews you've scrolled past."""

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QPixmap

from styli_app import covers


class _Signals(QObject):
    """How a background task hands its result (or error) back to the window's thread."""
    done = pyqtSignal(object)
    failed = pyqtSignal(object)


class _Task(QRunnable):
    """Runs one function in the background thread pool, so the window never freezes during a download."""

    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.started = False
        self.setAutoDelete(False)

    def run(self):
        """Run the function and report back: done with the result, or failed with the error."""
        self.started = True
        try:
            result, ok = self.fn(), True
        except Exception as e:  # handed back to the window
            result, ok = e, False
        try:
            (self.signals.done if ok else self.signals.failed).emit(result)
        except RuntimeError:
            pass  # the app closed while this was still downloading


def run_task(fn, done, failed, keep, pool=None, priority=0):
    """Run fn in the background, then call done(result) or failed(error) back in the window. keep holds the task
    until it finishes; higher priority runs sooner."""
    task = _Task(fn)
    keep.add(task)

    def finish(cb):
        def inner(value):
            keep.discard(task)
            cb(value)
        return inner

    task.signals.done.connect(finish(done))
    task.signals.failed.connect(finish(failed))
    (pool or QThreadPool.globalInstance()).start(task, priority)
    return task


def resolve_cover(key):
    """Runs in a worker thread. Returns (path or None, album id found or None)."""
    kind = key[0]
    if kind == "record":
        _, rid, mbid, rgid, size, cover_url, artist, title = key
        return covers.ensure_record_cover(rid, mbid, rgid, size, cover_url, artist, title)  # saved with the library
    if kind == "url":
        return covers.fetch_image_cached(key[1]), None
    if kind == "pressing":
        _, mbid, rgid, size = key
        return (covers.fetch_cover("release", mbid, size)
                or (covers.fetch_cover("release-group", rgid, size) if rgid else None)), None
    _, mbid, size = key
    return covers.fetch_cover(kind, mbid, size), None


class CoverStore(QObject):
    """Loads covers in the background and hands out pixmaps once they arrive."""
    changed = pyqtSignal()
    rgid_found = pyqtSignal(int, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.pix, self.pending, self.missing, self.tasks, self.queued = {}, set(), set(), set(), {}

    def get(self, key, priority=0):
        """The cover if it's loaded, otherwise None (and it's queued). Higher priority loads first."""
        if not key:
            return None
        pm = self.pix.get(key)
        if pm is None and key not in self.pending and key not in self.missing:
            self.pending.add(key)
            self.queued[key] = run_task(lambda k=key: resolve_cover(k), lambda res, k=key: self._done(k, res),
                                        lambda e, k=key: self._done(k, (None, None)), self.tasks, self.pool,
                                        priority)
        return pm

    def cancel_queued(self):
        """Drop downloads that haven't started yet, so other work isn't kept waiting.

        Anything still on screen asks again when it next repaints.
        """
        self.pool.clear()
        for key, task in list(self.queued.items()):
            if not task.started:
                self.pending.discard(key)
                self.tasks.discard(task)
                del self.queued[key]
        self.changed.emit()

    def forget_previews(self):
        """Drop cached search previews from memory (after the preview cache is cleared)."""
        for store in (self.pix,):
            for k in [k for k in store if k[0] != "record"]:
                del store[k]
        self.missing = {k for k in self.missing if k[0] == "record"}

    def loading(self, key):
        """True while a cover is still downloading."""
        return key in self.pending

    def _done(self, key, result):
        """A cover arrived (or didn't): remember it and repaint whatever is showing it."""
        path, rgid = result
        self.pending.discard(key)
        self.queued.pop(key, None)
        pm = QPixmap(str(path)) if path else QPixmap()
        if pm.isNull():
            self.missing.add(key)
        else:
            self.pix[key] = pm
        if rgid and key[0] == "record":
            self.rgid_found.emit(key[1], rgid)
        self.changed.emit()

    def forget_record(self, rid):
        """Drop a record's cached cover so a newly chosen image is loaded."""
        for k in [k for k in self.pix if k[0] == "record" and k[1] == rid]:
            del self.pix[k]
        self.missing = {k for k in self.missing if not (k[0] == "record" and k[1] == rid)}
