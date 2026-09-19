import json
from pathlib import Path

class SavedListsManager:
    def __init__(self, base_dir):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def list_names(self):
        return sorted([p.stem for p in self.base_dir.glob("*.json")])

    def load_list(self, name):
        path = self.base_dir / f"{name}.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def save_list(self, name, items):
        path = self.base_dir / f"{name}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        txt_path = self.base_dir / f"{name}.txt"
        lines = []
        for item in items:
            lines.append(f"Titel: {item.get('title', '')}")
            lines.append(f"Prijs: {item.get('price', '')}")
            lines.append(f"Locatie: {item.get('location', '')}")
            lines.append(f"Tijd: {item.get('time', '')}")
            lines.append(f"Link: {item.get('url', '')}")
            lines.append("")
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return path, txt_path
