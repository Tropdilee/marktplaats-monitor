"""Saved lists of listings, stored as JSON with a readable TXT copy beside it.

The list name becomes part of a file name, so it is cleaned first. Taken as
typed, a name such as "fiets/racefiets" pointed into a folder that does not
exist and crashed the app, and "../../x" wrote outside the lists folder.
"""

import json
import re
from pathlib import Path

from core.translations import tr

# Letters (including accented ones), digits, underscore, hyphen and space.
_NOT_ALLOWED = re.compile(r"[^\w\- ]", re.UNICODE)

# Names Windows refuses as file names, whatever the extension.
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_NAME_LENGTH = 80


class InvalidListName(ValueError):
    """The name holds nothing usable once cleaned."""


class CorruptList(ValueError):
    """The list file exists but cannot be read as a list."""


def clean_list_name(name):
    """Turn user input into a safe file name, or "" when nothing usable is left.

    Anything other than letters, digits, spaces, "-" and "_" becomes "_". Dots
    are not allowed at all, so ".." can never appear.
    """
    cleaned = _NOT_ALLOWED.sub("_", (name or "").strip())
    cleaned = cleaned.strip(" _")[:MAX_NAME_LENGTH].strip()
    if cleaned.upper() in _WINDOWS_RESERVED:
        cleaned += "_"
    return cleaned


class SavedListsManager:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name, suffix):
        # A list that already exists keeps its exact name. Lists saved by older
        # versions may contain characters the cleaning now replaces, such as
        # "fietsen (2026)"; cleaning those would point at a different, empty
        # file. The name can only match if the file is directly in the folder,
        # so this cannot be used to reach anywhere else.
        if name in self.list_names():
            return self.base_dir / f"{name}{suffix}"

        cleaned = clean_list_name(name)
        if not cleaned:
            raise InvalidListName(tr("invalid_list_name"))
        path = self.base_dir / f"{cleaned}{suffix}"
        # Belt and braces: whatever happens to the cleaning rules, a list file
        # never ends up anywhere but directly inside the lists folder.
        if path.resolve().parent != self.base_dir.resolve():
            raise InvalidListName(tr("invalid_list_name"))
        return path

    def list_names(self):
        return sorted(p.stem for p in self.base_dir.glob("*.json"))

    def load_list(self, name):
        """The items in a list, [] when it does not exist yet.

        A damaged file raises CorruptList rather than reading as empty: the
        caller would otherwise add to that "empty" list and save it, wiping out
        whatever was still recoverable in the original.
        """
        path = self._path(name, ".json")
        if not path.exists():
            return []
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CorruptList(tr("list_corrupt").format(name=path.stem)) from exc
        if not isinstance(items, list):
            raise CorruptList(tr("list_corrupt").format(name=path.stem))
        return [item for item in items if isinstance(item, dict)]

    def save_list(self, name, items):
        """Write the list as JSON and TXT. Raises OSError when the disk refuses."""
        path = self._path(name, ".json")
        txt_path = path.with_suffix(".txt")

        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

        lines = []
        for item in items:
            lines.append(f"{tr('label_title')}: {item.get('title', '')}")
            lines.append(f"{tr('label_price')}: {item.get('price', '')}")
            lines.append(f"{tr('label_location')}: {item.get('location', '')}")
            lines.append(f"{tr('label_time')}: {item.get('time', '')}")
            lines.append(f"{tr('label_link')}: {item.get('url', '')}")
            lines.append("")
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return path, txt_path
