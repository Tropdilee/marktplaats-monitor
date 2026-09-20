# Packaging the app to pass it on

*[Nederlands](README.md) · English*

The recipient needs no Python installation: the bundle contains Python, PyQt6 and
every package. Unpacking and starting is enough.

## Important: you can only build on the target system

PyInstaller builds for the system it runs on. A Windows exe is therefore made on
Windows, a macOS app on macOS. There is no way to make all three from one
computer.

Two options:

1. **With GitHub Actions** — `.github/workflows/build.yml` builds all three at
   once on GitHub's machines. That is the simplest route if you do not own a
   Windows and a Mac machine yourself. Push a tag:

   ```bash
   git tag v4.0 && git push origin v4.0
   ```

   The three files then appear under Releases. Without a tag you can also start
   the workflow by hand from the Actions tab; the files are then attached to that
   run.

2. **Yourself, per system** — use the script for that system:

   | System | Script | Result |
   | --- | --- | --- |
   | Linux | `packaging/build_linux.sh` | `dist/MIAW-Marktplaats-Monitor-linux.tar.gz` |
   | Windows | `packaging\build_windows.bat` | `dist\MIAW-Marktplaats-Monitor-windows.zip` |
   | macOS | `packaging/build_macos.sh` | `dist/MIAW-Marktplaats-Monitor-macos.dmg` |

   Each script makes a separate build virtualenv (`.venv-build`), so your own
   `.venv` is left alone.

## What the recipient does

**Linux** — unpack and start:

```bash
tar xzf MIAW-Marktplaats-Monitor-linux.tar.gz
"./MIAW Marktplaats Monitor/MIAW Marktplaats Monitor"
```

If the app complains about the `xcb` platform, a system library is missing:

```bash
sudo apt install libxcb-cursor0
```

**Windows** — unpack the zip and start `MIAW Marktplaats Monitor.exe`. The bundle
is unsigned, so SmartScreen shows "Windows protected your PC": click *More info*
and then *Run anyway*.

**macOS** — open the dmg and drag the app to Applications. Unsigned here too: the
first time, right-click the app and choose *Open*, otherwise Gatekeeper refuses.

## Not working? Self-test

The app can examine itself without opening the window:

```bash
"./MIAW Marktplaats Monitor/MIAW Marktplaats Monitor" --selftest
```

On Windows, from PowerShell in the unpacked folder:

```powershell
& ".\MIAW Marktplaats Monitor.exe" --selftest
```

You get a per-item verdict, in whichever language the app is set to:

```
  [OK  ] Qt and window handling - Qt 6.11.0
  [OK  ] Data folder - /home/.../MIAW Marktplaats Monitor (bundled: yes)
  [OK  ] Keyring for the token - Keyring
  [OK  ] Connection to Marktplaats - fetched 3 listings
  [OK  ] Telegram bot - @yourbot
```

The report is also written to `selftest.txt` in the data folder, which helps on
Windows where no terminal window comes along.

## Where the data lives

In a bundled app, not next to the program but with the user:

| System | Folder |
| --- | --- |
| Linux | `~/.local/share/PerplexityLocal/MIAW Marktplaats Monitor/` |
| Windows | `%LOCALAPPDATA%\PerplexityLocal\MIAW Marktplaats Monitor\` |
| macOS | `~/Library/Application Support/PerplexityLocal/MIAW Marktplaats Monitor/` |

That is where `seen_ids.json`, `categories.json`, `image_cache/` and the saved
lists end up. Settings live separately in QSettings, and the Telegram token in
the system keyring.

When starting from source everything stays in `data/` next to the code, so
developing and using do not get in each other's way.

## Size

The Linux bundle is roughly 172 MB, mostly Qt. `packaging/miaw.spec` lists the Qt
components that are left out (QtQuick, QtWebEngine, multimedia and the like);
that already saves well over a hundred MB. Anyone wanting to trim further should
look there.

**Do not pass your token along.** It sits in your keyring, not in the bundle.
Whoever receives the app makes their own bot with BotFather; see the Telegram
chapter in the README.
