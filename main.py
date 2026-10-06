import sys


def main():
    # Before anything touches the settings or the data folder: an update from a
    # version that used the old "PerplexityLocal" name would otherwise start
    # with empty settings.
    from core.migrate import migrate_legacy_locations

    migrate_legacy_locations()

    if "--selftest" in sys.argv:
        from core.selftest import run

        return run()

    from ui.main_window import main as start_window

    return start_window()


if __name__ == "__main__":
    sys.exit(main())
