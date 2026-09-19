"""Opslag van de Telegram-token buiten het gewone instellingenbestand.

QSettings schrijft alles leesbaar weg, op Linux in ~/.config. Daar hoort een
bot-token niet: die komt mee in back-ups, is zichtbaar bij het delen van je
scherm en belandt zo in een repository als het instellingenbestand ooit in de
projectmap staat.

Deze module zet de token in de sleutelbos van het besturingssysteem
(Secret Service / GNOME Keyring of KWallet op Linux, Keychain op macOS,
Credential Manager op Windows). Die is versleuteld op schijf en wordt
ontgrendeld bij het inloggen.

Wat het niet oplost: software die onder jouw eigen account draait mag de
sleutelbos net zo goed uitlezen. Het beschermt tegen meelezen en per ongeluk
delen, niet tegen malware die al als jou draait.

Is er geen sleutelbos beschikbaar, dan valt de opslag terug op QSettings. Dat
wordt gemeld in plaats van stilzwijgend gedaan, zodat duidelijk is dat de token
dan gewoon leesbaar op schijf staat.
"""

SERVICE_NAME = "MIAW Marktplaats Monitor"
TOKEN_ENTRY = "telegram_bot_token"
LEGACY_SETTINGS_KEY = "telegram/bot_token"

try:
    import keyring
except Exception:  # pragma: no cover - keyring hoeft niet geïnstalleerd te zijn
    keyring = None


class SecretStore:
    """Leest en schrijft de Telegram-token, bij voorkeur via de sleutelbos."""

    def __init__(self, settings, service=SERVICE_NAME):
        self.settings = settings
        self.service = service
        self.backend_name = ""
        self.available = self._probe()

    def _probe(self):
        """Kijk of er echt een bruikbare sleutelbos is.

        Zonder grafische sessie kiest keyring een backend die bij elk gebruik
        een fout geeft, dus een enkele proeflezing is de betrouwbaarste test.
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

            # Oude installatie: token stond nog leesbaar in QSettings. Die
            # verhuist bij de eerste keer lezen naar de sleutelbos.
            legacy = self.settings.value(LEGACY_SETTINGS_KEY, "")
            if legacy:
                if self.set_token(legacy):
                    return legacy
            return ""

        return self.settings.value(LEGACY_SETTINGS_KEY, "") or ""

    def set_token(self, token):
        """Bewaar de token. Geeft True terug als dat via de sleutelbos ging."""
        token = (token or "").strip()

        if self.available:
            try:
                if token:
                    keyring.set_password(self.service, TOKEN_ENTRY, token)
                else:
                    self._delete()
                # Nooit een kopie laten staan in het leesbare bestand.
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
            # Niets te wissen is prima.
            pass
