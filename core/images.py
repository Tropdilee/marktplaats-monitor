"""Fetching listing images, off the GUI thread.

The Marktplaats image CDN (images.marktplaats.com) is separate from the search
API and serves several sizes per URL through the `rule` parameter. A small
thumbnail for the list is about 2 kB, a large one for the preview about 56 kB.

Everything runs through a queue on its own thread: downloading on the GUI thread
would freeze the window for about a second on every new search. What arrives is
handed over as a QImage, because a QPixmap may only exist on the GUI thread.

Whatever has been fetched stays in an on-disk cache, so a refresh or a restart
downloads nothing again.
"""

import hashlib
import queue
import threading
from pathlib import Path

import requests
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from core.paths import data_file

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126 Safari/537.36"
)

# Sizes the CDN offers. Small for the list, larger for the preview.
RULE_THUMBNAIL = "ecg_mp_eps$_14.jpg"
RULE_PREVIEW = "ecg_mp_eps$_84.jpg"

# Comfortably above the number of rows visible at once.
MAX_DISK_CACHE_FILES = 600


def sized_url(url, rule):
    """Rewrite an image URL to the requested size."""
    if not url:
        return ""
    return url.split("?")[0] + "?rule=" + rule


class ImageLoader(QThread):
    """Fetches images in the background and returns them as QImage."""

    loaded = pyqtSignal(str, QImage)

    def __init__(self, cache_dir=None, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._stop_event = threading.Event()
        self._gevraagd = set()
        self._lock = threading.Lock()

        if cache_dir is None:
            cache_dir = data_file("image_cache")
        self.cache_dir = Path(cache_dir)

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ------------------------------------------------------------------

    def _cache_path(self, url):
        naam = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{naam}.jpg"

    def request(self, url):
        """Queue a URL, unless it is already queued."""
        if not url:
            return
        with self._lock:
            if url in self._gevraagd:
                return
            self._gevraagd.add(url)
        self._queue.put(url)

    def cached_image(self, url):
        """Read straight from the disk cache, or return None when absent."""
        if not url:
            return None
        pad = self._cache_path(url)
        if not pad.exists():
            return None
        image = QImage()
        if image.load(str(pad)) and not image.isNull():
            return image
        return None

    def stop(self):
        self._stop_event.set()

    # ------------------------------------------------------------------

    def run(self):
        while not self._stop_event.is_set():
            try:
                url = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            image = self.cached_image(url)
            if image is None:
                image = self._download(url)

            if image is not None and not image.isNull():
                self.loaded.emit(url, image)
            else:
                # Failed: drop it from the set so a later attempt is allowed.
                with self._lock:
                    self._gevraagd.discard(url)

    def _download(self, url):
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200 or not resp.content:
                return None
        except requests.RequestException:
            return None

        image = QImage()
        if not image.loadFromData(resp.content):
            return None

        self._write_cache(url, resp.content)
        return image

    def _write_cache(self, url, data):
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._prune()
            self._cache_path(url).write_bytes(data)
        except OSError:
            # Without the disk cache everything still works, just less frugally.
            pass

    def _prune(self):
        """Discard the oldest files when the cache grows too large."""
        try:
            bestanden = sorted(
                self.cache_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime
            )
        except OSError:
            return
        for pad in bestanden[: max(0, len(bestanden) - MAX_DISK_CACHE_FILES)]:
            try:
                pad.unlink()
            except OSError:
                pass
