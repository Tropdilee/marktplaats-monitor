def load_profiles(settings):
    count = int(settings.value("profiles/count", 0))
    profiles = []
    for i in range(count):
        base = f"profiles/{i}/"
        profiles.append({
            "name": settings.value(base + "name", f"Profiel {i+1}"),
            "term": settings.value(base + "term", ""),
            "category_id": settings.value(base + "category_id", ""),
            "region": settings.value(base + "region", ""),
            "max_price": float(settings.value(base + "max_price", 150)),
            "interval": int(settings.value(base + "interval", 60)),
        })
    return profiles

def save_profiles(settings, profiles):
    settings.setValue("profiles/count", len(profiles))
    for i, p in enumerate(profiles):
        base = f"profiles/{i}/"
        for key in ["name", "term", "category_id", "region", "max_price", "interval"]:
            settings.setValue(base + key, p.get(key, ""))
