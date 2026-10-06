"""Search profiles stored in QSettings.

Booleans are written as "true"/"false" text, like the rest of the app, because
the native Windows store and the Linux .conf file would otherwise hand them back
in different types.
"""

from core.monitor import MIN_INTERVAL_SECONDS
from core.translations import tr

# Field -> default for profiles saved by older versions that lack it.
_DEFAULTS = {
    "term": "",
    "category_id": "",
    "region": "",
    "distance": 0,
    "max_price": 150.0,
    "interval": 60,
    "free_only": False,
    "hide_promoted": True,
}


def _as_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def load_profiles(settings):
    count = int(settings.value("profiles/count", 0) or 0)
    profiles = []
    for i in range(count):
        base = f"profiles/{i}/"
        value = lambda key: settings.value(base + key, _DEFAULTS.get(key))
        profiles.append(
            {
                "name": settings.value(base + "name") or f"{tr('profile_default')} {i + 1}",
                "term": value("term") or "",
                "category_id": value("category_id") or "",
                "region": value("region") or "",
                "distance": int(value("distance") or 0),
                "max_price": float(value("max_price") or 0),
                # Older versions allowed 10 seconds; the monitor never goes that low.
                "interval": max(MIN_INTERVAL_SECONDS, int(value("interval") or 60)),
                "free_only": _as_bool(settings.value(base + "free_only"), False),
                "hide_promoted": _as_bool(settings.value(base + "hide_promoted"), True),
            }
        )
    return profiles


def save_profiles(settings, profiles):
    settings.remove("profiles")
    settings.setValue("profiles/count", len(profiles))
    for i, p in enumerate(profiles):
        base = f"profiles/{i}/"
        settings.setValue(base + "name", p.get("name", ""))
        for key in ("term", "category_id", "region", "distance", "max_price", "interval"):
            settings.setValue(base + key, p.get(key, _DEFAULTS[key]))
        for key in ("free_only", "hide_promoted"):
            settings.setValue(base + key, str(bool(p.get(key, _DEFAULTS[key]))).lower())
