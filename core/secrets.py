"""Storing the Telegram token outside the ordinary settings file.

QSettings writes everything in readable form, on Linux under ~/.config. A bot
token does not belong there: it ends up in backups, is visible when sharing your
screen, and would land in a repository if the settings file ever sat inside the
project folder.

This module puts the token in the operating system's keyring (Secret Service /
GNOME Keyring or KWallet on Linux, Keychain on macOS, Credential Manager on
Windows). That store is encrypted on disk and unlocked when you log in.

What it does not solve: software running under your own account may read the
keyring just as well. It protects against being read over your shoulder and
against accidental sharing, not against malware already running as you.

If no keyring is available, storage falls back to QSettings. That is reported
rather than done silently, so it is clear the token then sits readable on disk.
"""

SERVICE_NAME = "MIAW Marktplaats Monitor"
TOKEN_ENTRY = "telegram_bot_token"
LEGACY_SETTINGS_KEY = "telegram/bot_token"

try:
    import keyring
except Exception:  # pragma: no cover - keyring need not be installed
    keyring = None


class SecretStore:
    """Reads and writes the Telegram token, preferably through the keyring."""

    def __init__(self, settings, service=SERVICE_NAME):
        self.settings = settings
        self.service = service
        self.backend_name = ""
        self.available = self._probe()

    def _probe(self):
        """Check whether a usable keyring is really present.

        Without a graphical session, keyring picks a backend that raises on every
        use, so a single trial read is the most reliable test.
        """
        if keyring is None:
            return False
        try:
            backend = keyring.get_keyring()
            self.backend_name = type(backend).__module__ + "." + type(backend).__name__
            keyring.get_password(self.service, TOKEN_ENTRY)
            return True
        except Exception:
            return False

    def describe(self):
        if self.available:
            return f"sleutelbos ({self.backend_name.split('.')[-2]})"
        return "instellingenbestand (leesbaar)"

    def get_token(self):
        if self.available:
            try:
                token = keyring.get_password(self.service, TOKEN_ENTRY)
            except Exception:
                token = None
            if token:
                return token

            # Older installation: the token was still readable in QSettings.
            # It moves to the keyring on the first read.
            legacy = self.settings.value(LEGACY_SETTINGS_KEY, "")
            if legacy:
                if self.set_token(legacy):
                    return legacy
            return ""

        return self.settings.value(LEGACY_SETTINGS_KEY, "") or ""

    def set_token(self, token):
        """Store the token. Returns True when it went through the keyring."""
        token = (token or "").strip()

        if self.available:
            try:
                if token:
                    keyring.set_password(self.service, TOKEN_ENTRY, token)
                else:
                    self._delete()
                # Never leave a copy behind in the readable file.
                self.settings.remove(LEGACY_SETTINGS_KEY)
                return True
            except Exception:
                self.available = False

        self.settings.setValue(LEGACY_SETTINGS_KEY, token)
        return False

    def _delete(self):
        try:
            keyring.delete_password(self.service, TOKEN_ENTRY)
        except Exception:
            # Nothing to delete is fine.
            pass
