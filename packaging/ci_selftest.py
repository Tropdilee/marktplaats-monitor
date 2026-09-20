"""Runs the built app's --selftest and reports whether it started at all.

Lives in a file rather than inline in the workflow on purpose: an inline heredoc
only works on the bash runners, and the Windows runner uses PowerShell, where
`<<` is a redirection operator and the step fails to parse.

The Telegram and keyring checks cannot pass on a build machine, so only a crash
or a missing executable is treated as a failure here.
"""

import glob
import os
import subprocess
import sys

MARKERS = ("self-test", "zelftest")


def find_executable():
    candidates = glob.glob("dist/**/MIAW Marktplaats Monitor", recursive=True)
    candidates += glob.glob("dist/**/MIAW Marktplaats Monitor.exe", recursive=True)
    return [c for c in candidates if os.path.isfile(c)]


def main():
    executables = find_executable()
    if not executables:
        print("No built app found under dist/", file=sys.stderr)
        return 1

    executable = executables[0]
    print(f"Running: {executable} --selftest", flush=True)

    result = subprocess.run(
        [executable, "--selftest"], capture_output=True, text=True, timeout=300
    )
    output = result.stdout or ""
    print(output or result.stderr)

    if not any(marker in output for marker in MARKERS):
        print("The app did not start.", file=sys.stderr)
        return 1

    print("The app starts and reports its self-test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
