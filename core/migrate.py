"""Moving settings and data from the old "PerplexityLocal" locations.

Versions before 4.1 stored everything under the organisation name
"PerplexityLocal", a leftover that had nothing to do with this app. 4.1 uses
"MIAW". Without this step an update would look like a fresh install: search
settings, Telegram chat ID, seen listings and saved lists would all seem gone.

Runs once at start-up, before anything reads or writes the new locations. It
only acts when the new location is still empty, so it never overwrites anything
and does nothing on the second start.
"""

import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, QStandardPaths

from core.appinfo import APP_NAME, LEGACY_ORG_NAMES, ORG_NAME
from core.paths import is_frozen


def migrate_settings(old, new):
    """Copy every key from `old` to `new` when `new` is empty. Returns the count.

    The old store is cleared afterwards, so the old name stops showing up. On
    Linux that store is a plain .conf file, which is removed together with its
    folder once empty; elsewhere (the Windows registry, macOS preferences) the
    keys are cleared and the system tidies the rest.
    """
    if new.allKeys() or not old.allKeys():
        return 0

    keys = old.allKeys()
    for key in keys:
        new.setValue(key, old.value(key))
    new.sync()

    old_file = Path(old.fileName())
    old.clear()
    old.sync()
    if old_file.suffix in (".conf", ".ini") and old_file.is_file():
        try:
            old_file.unlink()
            old_file.parent.rmdir()  # only succeeds when nothing else is in it
        except OSError:
            pass
    return len(keys)


def migrate_data_dir(old_dir, new_dir):
    """Move the contents of `old_dir` into `new_dir` when `new_dir` is empty.

    Returns the number of entries moved.
    """
    old_dir, new_dir = Path(old_dir), Path(new_dir)
    if not old_dir.is_dir():
        return 0
    if new_dir.exists() and any(new_dir.iterdir()):
        return 0

    new_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in list(old_dir.iterdir()):
        shutil.move(str(entry), str(new_dir / entry.name))
        moved += 1

    for folder in (old_dir, old_dir.parent):
        try:
            folder.rmdir()
        except OSError:
            break
    return moved


def migrate_legacy_locations():
    """Move settings and (in a bundled app) data from every legacy name."""
    report = []
    for legacy_org in LEGACY_ORG_NAMES:
        try:
            count = migrate_settings(
                QSettings(legacy_org, APP_NAME), QSettings(ORG_NAME, APP_NAME)
            )
            if count:
                report.append(f"settings: {count} keys from {legacy_org}")
        except Exception as exc:  # never let a migration stop the app
            print(f"Settings migration from {legacy_org} failed: {exc}", file=sys.stderr)

        if not is_frozen():
            # Running from source keeps its data in data/ next to the code.
            continue
        try:
            base = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.GenericDataLocation
            )
            if base:
                moved = migrate_data_dir(
                    Path(base) / legacy_org / APP_NAME, Path(base) / ORG_NAME / APP_NAME
                )
                if moved:
                    report.append(f"data: {moved} entries from {legacy_org}")
        except Exception as exc:
            print(f"Data migration from {legacy_org} failed: {exc}", file=sys.stderr)
    return report
