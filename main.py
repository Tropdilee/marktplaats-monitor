import sys


def main():
    if "--selftest" in sys.argv:
        from core.selftest import run

        return run()

    from ui.main_window import main as start_venster

    return start_venster()


if __name__ == "__main__":
    sys.exit(main())
