"""Afbeeldingen van advertenties ophalen, buiten de GUI-thread om.

De beeld-CDN van Marktplaats (images.marktplaats.com) staat los van de zoek-API
en levert per URL verschillende formaten via de `rule`-parameter. Een klein
plaatje voor de lijst is ongeveer 2 kB, een groot voor de preview ongeveer 56 kB.

Alles loopt via een wachtrij in een eigen thread: downloaden op de GUI-thread
zou het venster bij elke nieuwe zoekopdracht een seconde laten staan. Wat
binnenkomt wordt als QImage doorgegeven, want een QPixmap mag alleen in de
GUI-thread bestaan.

Wat al opgehaald is blijft in een cache op schijf staan, zodat een refresh of
een herstart niets opnieuw hoeft te downloaden.
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

# Formaten die de CDN aanbiedt. Klein voor de lijst, groter voor de preview.
RULE_THUMBNAIL = "ecg_mp_eps$_14.jpg"
RULE_PREVIEW = "ecg_mp_eps$_84.jpg"

# Ruim boven het aantal rijen dat tegelijk in beeld staat.
MAX_DISK_CACHE_FILES = 600


def sized_url(url, rule):
    """Zet een afbeeldings-URL om naar het gevraagde formaat."""
    if not url:
        return ""
    return url.split("?")[0] + "?rule=" + rule


class ImageLoader(QThread):
    """Haalt afbeeldingen op in de achtergrond en geeft ze als QImage terug."""

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
        """Zet een URL in de wachtrij, tenzij die er al in staat."""
        if not url:
            return
        with self._lock:
            if url in self._gevraagd:
                return
            self._gevraagd.add(url)
        self._queue.put(url)

    def cached_image(self, url):
        """Lees meteen uit de schijfcache, of geef None als hij er niet staat."""
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
                # Mislukt: uit de lijst halen zodat een volgende poging mag.
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
            # Zonder schijfcache werkt alles nog, alleen minder zuinig.
            pass

    def _prune(self):
        """Gooi de oudste bestanden weg als de cache te groot wordt."""
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
