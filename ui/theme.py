from dataclasses import dataclass
from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QGridLayout, QLabel, QPushButton, QComboBox, QFontComboBox, QSpinBox, QColorDialog


def system_font_family():
    """Het lettertype dat het systeem zelf voorschrijft.

    Gebruikt als standaard in plaats van een vaste naam: "Segoe UI" bestaat
    alleen op Windows, en elders verving Qt dat stilletjes door iets anders.
    Bewust niet via QApplication.font(), want die geeft het lettertype terug
    dat de app zelf al heeft ingesteld - dan zou "standaard herstellen" de
    huidige keuze teruggeven in plaats van de echte standaard.
    """
    return QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()


@dataclass
class ThemeConfig:
    accent: str = "#ec038d"
    accent_hover: str = "#f00192"
    bg: str = "#121218"
    panel: str = "#1a1a23"
    panel_alt: str = "#21212c"
    border: str = "#2b2b36"
    text: str = "#f6f2f7"
    text_muted: str = "#b9b2bd"
    success: str = "#3ddc97"
    warning: str = "#f5b342"


def build_stylesheet(t: ThemeConfig) -> str:
    return f"""
    QMainWindow, QWidget {{ background: {t.bg}; color: {t.text}; }}
    QMenuBar, QMenuBar::item, QMenu {{ background: {t.panel}; color: {t.text}; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {t.accent}; }}
    QLabel#appTitle {{ font-size: 26px; font-weight: 700; color: {t.accent}; }}
    QLabel#appSubtitle {{ color: {t.text_muted}; padding-bottom: 6px; }}
    QGroupBox {{ border: 1px solid {t.border}; border-radius: 10px; margin-top: 10px; padding-top: 12px; font-weight: 600; background: {t.panel}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; color: {t.accent}; }}
    QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTableWidget, QListWidget, QTabWidget::pane {{ background: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border}; border-radius: 8px; padding: 6px; }}
    QHeaderView::section {{ background: {t.panel}; color: {t.text}; border: 1px solid {t.border}; padding: 6px; }}
    QPushButton {{ background: {t.accent}; color: white; border: 1px solid {t.accent_hover}; border-radius: 8px; padding: 8px 12px; font-weight: 600; }}
    QPushButton:hover {{ background: {t.accent_hover}; }}
    QPushButton:disabled {{ background: #4b4b57; color: #c8c3cb; border-color: #4b4b57; }}
    QTabBar::tab {{ background: {t.panel}; color: {t.text_muted}; padding: 9px 14px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; }}
    QTabBar::tab:selected {{ background: {t.accent}; color: white; }}
    QTableWidget::item:selected {{ background: {t.accent}; color: white; }}
    QLabel#regionWarning {{ color: {t.warning}; padding: 2px 4px; }}
    QTextEdit#previewBox {{ background: {t.panel_alt}; border: 1px solid {t.border}; border-radius: 8px; }}
    """


class AppearanceDialog(QDialog):
    """Eenvoudige editor om uiterlijk aan te passen: thema presets, accentkleur en lettergrootte."""

    def __init__(self, parent, settings):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Uiterlijk aanpassen")
        self.resize(520, 360)

        self.current_theme = ThemeConfig(
            accent=self.settings.value("ui/accent", ThemeConfig.accent),
            accent_hover=self.settings.value("ui/accent_hover", ThemeConfig.accent_hover),
            bg=self.settings.value("ui/bg", ThemeConfig.bg),
            panel=self.settings.value("ui/panel", ThemeConfig.panel),
            panel_alt=self.settings.value("ui/panel_alt", ThemeConfig.panel_alt),
            border=self.settings.value("ui/border", ThemeConfig.border),
            text=self.settings.value("ui/text", ThemeConfig.text),
            text_muted=self.settings.value("ui/text_muted", ThemeConfig.text_muted),
            success=self.settings.value("ui/success", ThemeConfig.success),
            warning=self.settings.value("ui/warning", ThemeConfig.warning),
        )

        self.build_ui()

    def build_ui(self):
        root = QVBoxLayout(self)

        # Thema-presets
        presets_box = QGroupBox("Thema")
        presets_layout = QGridLayout(presets_box)
        presets_label = QLabel("Kies een basisthema")
        self.presets_combo = QComboBox()
        self.presets_combo.addItems([
            "MIAW Magenta (standaard)",
            "Midnight Blue",
            "Light Mode",
        ])
        self.presets_combo.currentIndexChanged.connect(self.apply_preset)
        presets_layout.addWidget(presets_label, 0, 0)
        presets_layout.addWidget(self.presets_combo, 0, 1)
        root.addWidget(presets_box)

        # Kleuren
        colors_box = QGroupBox("Kleuren")
        colors_layout = QGridLayout(colors_box)

        self.accent_btn = QPushButton("Accentkleur…")
        self.accent_btn.clicked.connect(lambda: self.pick_color("accent"))
        self.bg_btn = QPushButton("Achtergrond…")
        self.bg_btn.clicked.connect(lambda: self.pick_color("bg"))
        self.panel_btn = QPushButton("Panel…")
        self.panel_btn.clicked.connect(lambda: self.pick_color("panel"))

        colors_layout.addWidget(QLabel("Belangrijkste kleur (knoppen, tabs)"), 0, 0)
        colors_layout.addWidget(self.accent_btn, 0, 1)
        colors_layout.addWidget(QLabel("Hoofdachtergrond"), 1, 0)
        colors_layout.addWidget(self.bg_btn, 1, 1)
        colors_layout.addWidget(QLabel("Panels (groepvakken, tab-achtergrond)"), 2, 0)
        colors_layout.addWidget(self.panel_btn, 2, 1)

        root.addWidget(colors_box)

        # Lettertype-grootte
        font_box = QGroupBox("Lettertype")
        font_layout = QGridLayout(font_box)

        self.font_family_combo = QFontComboBox()
        # "Sans Serif" is een alias; de combo maakt er de echte familie van
        # (bijvoorbeeld "Noto Sans"). Die uitkomst bewaren we, zodat we bij het
        # opslaan kunnen zien of de gebruiker nog op de systeemkeuze staat.
        self.font_family_combo.setCurrentFont(QFont(system_font_family()))
        self.system_family = self.font_family_combo.currentFont().family()

        opgeslagen = self.settings.value("ui/font_family", "")
        if opgeslagen:
            self.font_family_combo.setCurrentFont(QFont(opgeslagen))

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 16)
        self.font_size_spin.setValue(int(self.settings.value("ui/font_size", 10)))

        font_layout.addWidget(QLabel("Lettertype"), 0, 0)
        font_layout.addWidget(self.font_family_combo, 0, 1)
        font_layout.addWidget(QLabel("Basis lettergrootte"), 1, 0)
        font_layout.addWidget(self.font_size_spin, 1, 1)
        root.addWidget(font_box)

        # Onderste knoppen
        buttons_row = QHBoxLayout()
        self.reset_btn = QPushButton("Standaard herstellen")
        self.reset_btn.clicked.connect(self.reset_defaults)
        self.cancel_btn = QPushButton("Annuleren")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Opslaan en toepassen")
        self.ok_btn.clicked.connect(self.accept)

        buttons_row.addStretch(1)
        buttons_row.addWidget(self.reset_btn)
        buttons_row.addWidget(self.cancel_btn)
        buttons_row.addWidget(self.ok_btn)

        root.addLayout(buttons_row)

    # Preset-logica
    def apply_preset(self, index: int):
        if index == 0:  # MIAW Magenta
            self.current_theme = ThemeConfig()
        elif index == 1:  # Midnight Blue
            self.current_theme = ThemeConfig(
                accent="#0078d4",
                accent_hover="#1490ff",
                bg="#0b1220",
                panel="#111827",
                panel_alt="#1f2937",
                border="#374151",
                text="#e5e7eb",
                text_muted="#9ca3af",
                success="#22c55e",
                warning="#facc15",
            )
        elif index == 2:  # Light Mode
            self.current_theme = ThemeConfig(
                accent="#2563eb",
                accent_hover="#1d4ed8",
                bg="#f3f4f6",
                panel="#ffffff",
                panel_alt="#e5e7eb",
                border="#cbd5f5",
                text="#111827",
                text_muted="#6b7280",
                success="#16a34a",
                warning="#ea580c",
            )

    def pick_color(self, field: str):
        # Huidige kleur als startpunt
        start_hex = getattr(self.current_theme, field)
        color = QColor(start_hex)
        chosen = QColorDialog.getColor(color, self, "Kies kleur")
        if chosen.isValid():
            hex_val = chosen.name()
            setattr(self.current_theme, field, hex_val)
            if field == "accent":
                # Accent-hover iets donkerder / feller maken
                darker = chosen.darker(115)
                self.current_theme.accent_hover = darker.name()

    def reset_defaults(self):
        self.current_theme = ThemeConfig()
        self.font_size_spin.setValue(10)
        self.font_family_combo.setCurrentFont(QFont(system_font_family()))
        self.presets_combo.setCurrentIndex(0)

    def accept(self):
        # Thema opslaan in settings
        self.settings.setValue("ui/accent", self.current_theme.accent)
        self.settings.setValue("ui/accent_hover", self.current_theme.accent_hover)
        self.settings.setValue("ui/bg", self.current_theme.bg)
        self.settings.setValue("ui/panel", self.current_theme.panel)
        self.settings.setValue("ui/panel_alt", self.current_theme.panel_alt)
        self.settings.setValue("ui/border", self.current_theme.border)
        self.settings.setValue("ui/text", self.current_theme.text)
        self.settings.setValue("ui/text_muted", self.current_theme.text_muted)
        self.settings.setValue("ui/success", self.current_theme.success)
        self.settings.setValue("ui/warning", self.current_theme.warning)
        self.settings.setValue("ui/font_size", self.font_size_spin.value())
        # Staat de keuze gelijk aan het systeemlettertype, dan slaan we niets
        # op. De app volgt dan het systeem, ook op een andere computer met
        # andere lettertypen.
        familie = self.font_family_combo.currentFont().family()
        self.settings.setValue(
            "ui/font_family", "" if familie == self.system_family else familie
        )
        super().accept()
