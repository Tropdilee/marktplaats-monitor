"""Categorieën van Marktplaats, met een cache op schijf.

De hoofdcategorieën veranderen zelden, dus ze worden één keer opgehaald en
daarna bewaard. Elke zoekopdracht levert de lijst opnieuw mee, dus in de praktijk
ververst de cache zichzelf zonder extra verkeer richting Marktplaats.
"""

import json
from pathlib import Path

ALL_CATEGORIES = "Alle categorieën"


class CategoryStore:
    def __init__(self, path=None):
        if path is None:
            path = Path(__file__).resolve().parents[1] / "data" / "categories.json"
        self.path = Path(path)
        self.categories = self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [
            {"id": int(c["id"]), "name": str(c["name"])}
            for c in raw
            if isinstance(c, dict) and "id" in c and "name" in c
        ]

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.categories, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def update_from_response(self, options):
        """Neem de categorielijst over uit een zoekantwoord."""
        if not isinstance(options, list):
            return False

        fresh = []
        for opt in options:
            if isinstance(opt, dict) and opt.get("id") and opt.get("name"):
                fresh.append({"id": int(opt["id"]), "name": str(opt["name"])})

        if fresh and fresh != self.categories:
            self.categories = fresh
            self.save()
            return True
        return False

    def names(self):
        return [ALL_CATEGORIES] + [c["name"] for c in self.categories]

    def id_for_name(self, name):
        if not name or name == ALL_CATEGORIES:
            return None
        for c in self.categories:
            if c["name"] == name:
                return c["id"]
        return None

    def name_for_id(self, category_id):
        if not category_id:
            return ALL_CATEGORIES
        for c in self.categories:
            if c["id"] == int(category_id):
                return c["name"]
        return ALL_CATEGORIES
