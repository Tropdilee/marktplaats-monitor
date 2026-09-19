import queue
import random
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QSettings, QTime, QTimer, QObject, pyqtSignal, QThread
from PyQt6.QtGui import QAction, QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QSpinBox,
    QDoubleSpinBox,
    QCheckBox,
    QMessageBox,
    QComboBox,
    QSplitter,
    QGroupBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QSizePolicy,
    QTimeEdit,
)

from core.categories import ALL_CATEGORIES, CategoryStore
from core.monitor import MarktplaatsMonitor, RateLimited
from core.saved_lists import SavedListsManager
from core.settings_manager import load_profiles, save_profiles
from core.telegram_client import send_telegram_message
from core.translations import get_text
from ui.dialogs import SearchProfileDialog, AppearanceDialog
from ui.theme import ThemeConfig, build_stylesheet, system_font_family

APP_NAME = "MIAW Marktplaats Monitor"
ORG_NAME = "PerplexityLocal"
APP_VERSION = "4.0"
LAST_UPDATE = "2026-09-19"

ALL_SUBCATEGORIES = "Alle subcategorieën"

# Onder deze interval gaat de monitor niet. Marktplaats noemt zelf geen limiet,
# dus de veiligste koers is een tempo dat niet opvalt naast gewoon browsen.
MIN_INTERVAL_SECONDS = 30

# Nederlandse postcode: vier cijfers (niet met 0 beginnend), eventueel gevolgd
# door twee letters. Marktplaats neemt beide vormen aan.
POSTCODE_PATTERN = re.compile(r"^[1-9]\d{3}\s*([A-Za-z]{2})?$")


class MonitorWorker(QObject):
    finished = pyqtSignal(list, list, str)

    def __init__(
        self,
        monitor,
        term,
        max_price,
        limit,
        region=None,
        distance_km=None,
        free_only=False,
        category_id=None,
        subcategory_id=None,
        hide_promoted=True,
    ):
        super().__init__()
        self.monitor = monitor
        self.term = term
        self.max_price = max_price
        self.limit = limit
        self.region = region
        self.distance_km = distance_km
        self.free_only = free_only
        self.category_id = category_id
        self.subcategory_id = subcategory_id
        self.hide_promoted = hide_promoted

    def run(self):
        try:
            new_items, all_items = self.monitor.get_new_items(
                term=self.term,
                max_price=self.max_price,
                limit=self.limit,
                region=self.region,
                distance_km=self.distance_km,
                free_only=self.free_only,
                category_id=self.category_id,
                subcategory_id=self.subcategory_id,
                hide_promoted=self.hide_promoted,
            )
            self.finished.emit(new_items, all_items, "")
        except RateLimited as e:
            # Apart gemarkeerd zodat het venster de monitor kan stilleggen.
            self.finished.emit([], [], f"RATE_LIMIT|{e}")
        except Exception as e:
            self.finished.emit([], [], f"{type(e).__name__}: {e}")


class TelegramSender(QThread):
    """Verstuurt Telegram-berichten buiten de GUI-thread om.

    Meldingen gingen eerder direct vanuit de GUI-thread de deur uit, waardoor
    het venster bij een reeks nieuwe advertenties seconden bevroor. De wachtrij
    houdt er meteen rekening mee dat Telegram maar een beperkt aantal berichten
    per minuut naar dezelfde chat accepteert.
    """

    logged = pyqtSignal(str)

    SECONDS_BETWEEN_MESSAGES = 1.5

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue = queue.Queue()
        self._stop_event = threading.Event()

    def enqueue(self, token, chat_id, text):
        self._queue.put((token, chat_id, text))

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            try:
                token, chat_id, text = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                ok, data = send_telegram_message(token, chat_id, text)
            except Exception as exc:
                self.logged.emit(f"Telegram fout: {type(exc).__name__}: {exc}")
            else:
                if ok:
                    self.logged.emit("Telegram melding verstuurd.")
                else:
                    self.logged.emit(f"Telegram fout: {data}")

            if self._stop_event.wait(self.SECONDS_BETWEEN_MESSAGES):
                break


class MainWindow(QMainWindow):
    # Boven dit aantal gaat de rest van een cyclus als één samenvatting mee.
    MAX_TELEGRAM_MESSAGES_PER_CYCLE = 10

    # Variatie op de interval, zodat het opvragen geen exact ritme krijgt.
    INTERVAL_JITTER = 0.2

    # Hoeveel de interval per lege cyclus oploopt zolang er niets nieuws is.
    ADAPTIVE_GROWTH = 1.5

    # Standaardduur van de "Nieuw"-markering in de resultaten, in minuten.
    DEFAULT_NEW_MARKER_MINUTES = 15

    def __init__(self):
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.monitor = MarktplaatsMonitor()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_monitor_cycle)
        self.is_refreshing = False
        self.worker_thread = None
        self.worker = None

        self.category_store = CategoryStore()
        self.profiles = load_profiles(self.settings)
        self.notifications = []
        self.current_profile_name = None
        self.current_language = self.settings.value("ui/language", "Nederlands")
        self.saved_manager = SavedListsManager(
            Path(__file__).resolve().parents[1] / "data" / "saved_lists"
        )
        self.current_results = []
        self.current_saved_items = []
        # Aangevinkte advertenties overleven zo een refresh van de tabel.
        self.checked_ids = set()

        # {advertentie-id: moment waarop hij voor het eerst opdook}
        self.new_since = {}

        # Huidige wachttijd tussen twee checks; loopt op als er niets nieuws is.
        self.current_interval_seconds = None
        self.empty_cycles = 0
        self.in_quiet_period = False

        # De leeftijd in de markering loopt door, ook als er geen cyclus draait.
        self.marker_timer = QTimer(self)
        self.marker_timer.timeout.connect(self.refresh_new_markers)
        self.marker_timer.start(30000)

        self.telegram_sender = TelegramSender(self)
        self.telegram_sender.logged.connect(self.log)
        self.telegram_sender.start()

        self.setWindowTitle(APP_NAME)
        self.resize(1600, 960)

        self.build_ui()
        self.build_menu()
        self.load_settings_into_ui()
        self.apply_theme()
        self.apply_language()
        self.reload_saved_lists()
        self.update_status(False)

    def t(self, key):
        return get_text(self.current_language, key)

    def theme(self):
        d = ThemeConfig()
        vals = {k: self.settings.value(f"ui/{k}", getattr(d, k)) for k in d.__dict__.keys()}
        return ThemeConfig(**vals)

    def build_menu(self):
        self.menubar = self.menuBar()
        self.edit_menu = self.menubar.addMenu("Edit")
        self.language_menu = self.menubar.addMenu("Taal")
        self.help_menu = self.menubar.addMenu("Help")

        self.appearance_action = QAction(self)
        self.appearance_action.triggered.connect(self.open_appearance_dialog)
        self.edit_menu.addAction(self.appearance_action)

        self.language_group = []
        for lang in ["Nederlands", "English"]:
            action = QAction(lang, self, checkable=True)
            action.triggered.connect(lambda checked, l=lang: self.set_language(l))
            self.language_menu.addAction(action)
            self.language_group.append(action)

        self.info_action = QAction(self)
        self.info_action.triggered.connect(self.show_info)
        self.help_menu.addAction(self.info_action)

    def build_ui(self):
        root = QWidget()
        outer = QVBoxLayout(root)
        # Marges en spacing compacter, maar nog één lege regel rond de titel
        outer.setContentsMargins(0, 2, 0, 4)
        outer.setSpacing(2)

        self.title_label = QLabel()
        self.title_label.setObjectName("appTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = self.title_label.font()
        title_font.setPointSize(title_font.pointSize() + 2)
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("appSubtitle")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Statuslabel staat onder de startknop in de search-tab, niet meer in de header
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        outer.addWidget(self.title_label)
        outer.addWidget(self.subtitle_label)
        # Zonder deze twee eisen de labels een deel van de vrije ruimte op en
        # ontstaat er een leeg gat tussen de titel en de tabbladen.
        self.title_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self.subtitle_label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.left_tabs = QTabWidget()
        self.search_tab = self.build_search_tab()
        self.profiles_tab = self.build_profiles_tab()
        self.view_tab = self.build_view_tab()
        self.telegram_tab = self.build_telegram_tab()
        self.saved_tab = self.build_saved_tab()

        self.left_tabs.addTab(self.search_tab, "")
        self.left_tabs.addTab(self.profiles_tab, "")
        self.left_tabs.addTab(self.view_tab, "")
        self.left_tabs.addTab(self.telegram_tab, "")
        self.left_tabs.addTab(self.saved_tab, "")

        self.right_tabs = QTabWidget()
        self.results_tab = self.build_results_tab()
        self.notifications_tab = self.build_notifications_tab()
        self.log_tab = self.build_log_tab()

        self.right_tabs.addTab(self.results_tab, "")
        self.right_tabs.addTab(self.notifications_tab, "")
        self.right_tabs.addTab(self.log_tab, "")

        splitter.addWidget(self.left_tabs)
        splitter.addWidget(self.right_tabs)
        splitter.setSizes([620, 980])

        outer.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def build_search_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.search_group = QGroupBox()
        form = QGridLayout(self.search_group)

        self.search_term_label = QLabel()
        self.category_label = QLabel()
        self.subcategory_label = QLabel()
        self.region_label = QLabel()
        self.distance_label = QLabel()
        self.max_price_label = QLabel()
        self.interval_label = QLabel()
        self.results_limit_label = QLabel()

        self.search_term = QLineEdit()
        self.region = QLineEdit()

        # Hoofdcategorie komt uit de opgeslagen lijst; de subcategorieën horen
        # bij de zoekterm en worden na elke zoekopdracht bijgewerkt.
        self.category = QComboBox()
        self.category.addItems(self.category_store.names())
        self.category.currentTextChanged.connect(self.on_category_changed)

        self.subcategory = QComboBox()
        self.subcategory.addItem(ALL_SUBCATEGORIES)
        self.subcategory.setEnabled(False)

        # Afstand rondom regio (in km)
        self.distance = QSpinBox()
        self.distance.setRange(0, 250)
        self.distance.setSuffix(" km")
        self.distance.setSingleStep(5)

        self.max_price = QDoubleSpinBox()
        self.max_price.setMaximum(999999)
        self.max_price.setDecimals(2)
        self.max_price.setPrefix("€ ")

        self.interval = QSpinBox()
        self.interval.setRange(MIN_INTERVAL_SECONDS, 3600)
        self.interval.setSuffix(" sec")

        # Adaptieve interval: rustig aan als er niets gebeurt, meteen weer snel
        # zodra er een nieuwe advertentie langskomt.
        self.adaptive_enabled = QCheckBox()
        self.max_interval_label = QLabel()
        self.max_interval = QSpinBox()
        self.max_interval.setRange(1, 60)
        self.max_interval.setSuffix(" min")

        # Nachtpauze: in de uren dat je er toch niets mee doet hoeft er niets
        # opgevraagd te worden.
        self.quiet_enabled = QCheckBox()
        self.quiet_label = QLabel()
        self.quiet_from = QTimeEdit()
        self.quiet_to = QTimeEdit()
        for edit in (self.quiet_from, self.quiet_to):
            edit.setDisplayFormat("HH:mm")

        quiet_row = QHBoxLayout()
        quiet_row.setContentsMargins(0, 0, 0, 0)
        quiet_row.addWidget(self.quiet_from)
        quiet_row.addWidget(QLabel("→"))
        quiet_row.addWidget(self.quiet_to)
        quiet_row.addStretch(1)

        self.results_limit = QSpinBox()
        self.results_limit.setRange(10, 1000)
        self.results_limit.setSingleStep(10)

        self.use_notifications = QCheckBox()
        self.auto_mark = QCheckBox()
        self.new_marker_label = QLabel()
        self.new_marker_minutes = QSpinBox()
        self.new_marker_minutes.setRange(1, 120)
        self.new_marker_minutes.setSuffix(" min")
        self.new_marker_minutes.valueChanged.connect(self.on_new_marker_duration_changed)
        self.free_only = QCheckBox()
        self.hide_promoted = QCheckBox()

        form.addWidget(self.search_term_label, 0, 0)
        form.addWidget(self.search_term, 0, 1)
        form.addWidget(self.category_label, 1, 0)
        form.addWidget(self.category, 1, 1)
        form.addWidget(self.subcategory_label, 2, 0)
        form.addWidget(self.subcategory, 2, 1)
        form.addWidget(self.region_label, 3, 0)
        form.addWidget(self.region, 3, 1)
        form.addWidget(self.distance_label, 4, 0)
        form.addWidget(self.distance, 4, 1)
        form.addWidget(self.max_price_label, 5, 0)
        form.addWidget(self.max_price, 5, 1)
        form.addWidget(self.interval_label, 6, 0)
        form.addWidget(self.interval, 6, 1)
        form.addWidget(self.adaptive_enabled, 7, 0, 1, 2)
        form.addWidget(self.max_interval_label, 8, 0)
        form.addWidget(self.max_interval, 8, 1)
        form.addWidget(self.quiet_enabled, 9, 0, 1, 2)
        form.addWidget(self.quiet_label, 10, 0)
        form.addLayout(quiet_row, 10, 1)
        form.addWidget(self.results_limit_label, 11, 0)
        form.addWidget(self.results_limit, 11, 1)
        form.addWidget(self.use_notifications, 12, 0, 1, 2)
        form.addWidget(self.auto_mark, 13, 0, 1, 2)
        form.addWidget(self.new_marker_label, 14, 0)
        form.addWidget(self.new_marker_minutes, 14, 1)
        form.addWidget(self.free_only, 15, 0, 1, 2)
        form.addWidget(self.hide_promoted, 16, 0, 1, 2)

        layout.addWidget(self.search_group)

        # Een verkeerde regio of een afstand van 0 wordt door Marktplaats
        # stilzwijgend genegeerd: je krijgt dan resultaten uit het hele land
        # zonder dat er iets misgaat. Vandaar deze waarschuwing in beeld.
        self.region_warning = QLabel()
        self.region_warning.setObjectName("regionWarning")
        self.region_warning.setWordWrap(True)
        self.region_warning.hide()
        layout.addWidget(self.region_warning)

        self.region.textChanged.connect(self.update_region_warning)
        self.distance.valueChanged.connect(self.update_region_warning)

        row = QHBoxLayout()
        self.start_btn = QPushButton()
        self.stop_btn = QPushButton()
        self.check_now_btn = QPushButton()
        self.save_btn = QPushButton()

        self.start_btn.clicked.connect(self.start_monitor)
        self.stop_btn.clicked.connect(self.stop_monitor)
        self.check_now_btn.clicked.connect(self.run_manual_check)
        self.save_btn.clicked.connect(self.save_form_settings)

        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.check_now_btn)
        row.addWidget(self.save_btn)
        layout.addLayout(row)

        status_row = QHBoxLayout()
        status_row.addWidget(self.status_label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        layout.addStretch(1)
        return w

    def build_profiles_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.profiles_list = QListWidget()
        self.profiles_list.itemDoubleClicked.connect(lambda _: self.use_selected_profile())
        layout.addWidget(self.profiles_list)

        row = QHBoxLayout()
        self.new_btn = QPushButton()
        self.edit_btn = QPushButton()
        self.delete_btn = QPushButton()
        self.use_btn = QPushButton()

        self.new_btn.clicked.connect(self.add_profile)
        self.edit_btn.clicked.connect(self.edit_profile)
        self.delete_btn.clicked.connect(self.delete_profile)
        self.use_btn.clicked.connect(self.use_selected_profile)

        row.addWidget(self.new_btn)
        row.addWidget(self.edit_btn)
        row.addWidget(self.delete_btn)
        row.addWidget(self.use_btn)
        layout.addLayout(row)

        self.reload_profiles_list()
        return w

    def build_view_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.view_group = QGroupBox()
        form = QGridLayout(self.view_group)

        self.language_label = QLabel()
        self.show_prices = QCheckBox()
        self.show_location = QCheckBox()
        self.show_time = QCheckBox()
        self.compact_mode = QCheckBox()
        self.language_combo = QComboBox()
        self.language_combo.addItems(["Nederlands", "English"])
        self.language_combo.currentTextChanged.connect(self.set_language)

        form.addWidget(self.language_label, 0, 0)
        form.addWidget(self.language_combo, 0, 1)
        form.addWidget(self.show_prices, 1, 0, 1, 2)
        form.addWidget(self.show_location, 2, 0, 1, 2)
        form.addWidget(self.show_time, 3, 0, 1, 2)
        form.addWidget(self.compact_mode, 4, 0, 1, 2)

        layout.addWidget(self.view_group)
        layout.addStretch(1)
        return w

    def build_telegram_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        box = QGroupBox("Telegram")
        form = QGridLayout(box)

        self.telegram_enabled = QCheckBox()
        self.bot_token_label = QLabel("Bot token")
        self.chat_id_label = QLabel("Chat ID")
        self.prefix_label = QLabel("Prefix")
        self.bot_token = QLineEdit()
        self.chat_id = QLineEdit()
        self.message_prefix = QLineEdit()

        form.addWidget(self.telegram_enabled, 0, 0, 1, 2)
        form.addWidget(self.bot_token_label, 1, 0)
        form.addWidget(self.bot_token, 1, 1)
        form.addWidget(self.chat_id_label, 2, 0)
        form.addWidget(self.chat_id, 2, 1)
        form.addWidget(self.prefix_label, 3, 0)
        form.addWidget(self.message_prefix, 3, 1)

        layout.addWidget(box)

        row = QHBoxLayout()
        self.telegram_save_btn = QPushButton("Telegram opslaan")
        self.telegram_test_btn = QPushButton("Telegram test")
        self.telegram_save_btn.clicked.connect(self.save_telegram_settings)
        self.telegram_test_btn.clicked.connect(self.test_telegram)

        row.addWidget(self.telegram_save_btn)
        row.addWidget(self.telegram_test_btn)
        layout.addLayout(row)
        layout.addStretch(1)
        return w

    def build_saved_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        self.saved_lists_group = QGroupBox()
        saved_layout = QVBoxLayout(self.saved_lists_group)

        self.saved_lists_widget = QListWidget()
        self.saved_lists_widget.itemClicked.connect(self.load_selected_saved_list)
        saved_layout.addWidget(self.saved_lists_widget)

        row1 = QHBoxLayout()
        self.new_list_btn = QPushButton()
        self.load_list_btn = QPushButton()
        self.save_list_file_btn = QPushButton()
        self.share_list_btn = QPushButton()

        self.new_list_btn.clicked.connect(self.create_new_list)
        self.load_list_btn.clicked.connect(self.load_selected_saved_list)
        self.save_list_file_btn.clicked.connect(self.export_current_saved_list)
        self.share_list_btn.clicked.connect(self.share_current_saved_list)

        row1.addWidget(self.new_list_btn)
        row1.addWidget(self.load_list_btn)
        row1.addWidget(self.save_list_file_btn)
        row1.addWidget(self.share_list_btn)
        saved_layout.addLayout(row1)

        layout.addWidget(self.saved_lists_group)

        self.saved_items_group = QGroupBox()
        items_layout = QVBoxLayout(self.saved_items_group)
        self.saved_items_widget = QListWidget()
        self.saved_items_widget.itemClicked.connect(self.show_saved_item_preview)
        items_layout.addWidget(self.saved_items_widget)

        layout.addWidget(self.saved_items_group)
        return w

    def build_results_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        split = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        top_layout = QVBoxLayout(top)

        self.results_table = QTableWidget(0, 8)
        self.results_table.setHorizontalHeaderLabels(
            ["Sel", "ID", "Titel", "Prijs", "Locatie", "Tijd", "Status", "Link"]
        )

        header = self.results_table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # De titel is de kolom die je echt wilt lezen, dus die krijgt de ruimte.
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)

        self.results_table.setSortingEnabled(True)
        self.results_table.itemSelectionChanged.connect(self.show_selected_result_preview)
        self.results_table.cellDoubleClicked.connect(self.on_result_double_clicked)
        self.results_table.itemChanged.connect(self.on_result_item_changed)

        top_layout.addWidget(self.results_table)

        row = QHBoxLayout()
        self.save_selected_btn = QPushButton()
        self.open_link_btn = QPushButton()
        self.save_selected_btn.clicked.connect(self.save_selected_results_to_list)
        self.open_link_btn.clicked.connect(self.open_selected_listing)
        row.addWidget(self.save_selected_btn)
        row.addWidget(self.open_link_btn)
        row.addStretch(1)
        top_layout.addLayout(row)

        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        self.preview_group = QGroupBox()
        preview_layout = QVBoxLayout(self.preview_group)

        self.preview_box = QTextEdit()
        self.preview_box.setObjectName("previewBox")
        self.preview_box.setReadOnly(True)
        preview_layout.addWidget(self.preview_box)

        self.preview_open_btn = QPushButton()
        self.preview_open_btn.clicked.connect(self.open_selected_listing)
        preview_layout.addWidget(self.preview_open_btn)

        bottom_layout.addWidget(self.preview_group)

        split.addWidget(top)
        split.addWidget(bottom)
        split.setSizes([480, 360])

        layout.addWidget(split)
        return w

    def build_notifications_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.notifications_list = QListWidget()
        layout.addWidget(self.notifications_list)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(self.clear_notifications)
        layout.addWidget(self.clear_btn)
        return w

    def build_log_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)
        return w

    def region_problem(self):
        """Beschrijf wat er mis is met het locatiefilter, of geef "" als het klopt.

        Marktplaats accepteert alleen een postcode, en alleen samen met een
        afstand. Alles daarbuiten wordt genegeerd zonder foutmelding, dus zonder
        deze controle lijkt het filter te werken terwijl je resultaten uit het
        hele land binnenkrijgt.
        """
        region = self.region.text().strip()
        distance = self.distance.value()

        if not region and not distance:
            return ""
        if region and not distance:
            return self.t("region_needs_distance")
        if distance and not region:
            return self.t("distance_needs_region")
        if not POSTCODE_PATTERN.match(region):
            return self.t("region_not_postcode").format(region=region)
        return ""

    def update_region_warning(self):
        problem = self.region_problem()
        self.region_warning.setText(f"⚠  {problem}" if problem else "")
        self.region_warning.setVisible(bool(problem))

    def confirm_region_problem(self):
        """Vraag door als het locatiefilter niet gaat werken. True = toch zoeken."""
        problem = self.region_problem()
        if not problem:
            return True

        self.log(f"Let op: {problem}")
        answer = QMessageBox.question(
            self,
            self.t("region_warning_title"),
            f"{problem}\n\n{self.t('region_continue_question')}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def selected_category_id(self):
        return self.category_store.id_for_name(self.category.currentText())

    def selected_subcategory_id(self):
        if not self.selected_category_id():
            return None
        return self.subcategory.currentData()

    def reload_category_combo(self):
        """Ververs de hoofdcategorieën zonder de keuze van de gebruiker kwijt te raken."""
        current = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(self.category_store.names())
        self.category.setCurrentText(current)
        self.category.blockSignals(False)

    def on_category_changed(self, _name=None):
        # Subcategorieën horen bij één hoofdcategorie, dus bij het wisselen
        # daarvan is de oude lijst niet meer geldig.
        self.subcategory.clear()
        self.subcategory.addItem(ALL_SUBCATEGORIES)
        self.subcategory.setEnabled(False)

    def refresh_subcategories(self):
        """Vul de subcategorieën met wat de laatste zoekopdracht opleverde."""
        category_id = self.selected_category_id()
        if not category_id:
            return

        options = [
            s
            for s in getattr(self.monitor, "last_subcategories", [])
            if s.get("parent_id") == category_id
        ]
        if not options:
            return

        previous = self.subcategory.currentData()
        self.subcategory.blockSignals(True)
        self.subcategory.clear()
        self.subcategory.addItem(ALL_SUBCATEGORIES)
        for option in options:
            label = option["name"]
            if option.get("count"):
                label += f" ({option['count']})"
            self.subcategory.addItem(label, option["id"])
        if previous is not None:
            index = self.subcategory.findData(previous)
            if index >= 0:
                self.subcategory.setCurrentIndex(index)
        self.subcategory.blockSignals(False)
        self.subcategory.setEnabled(True)

    def apply_language(self):
        self.title_label.setText(self.t("title"))
        self.subtitle_label.setText("")

        self.left_tabs.setTabText(0, self.t("search_tab"))
        self.left_tabs.setTabText(1, self.t("profiles_tab"))
        self.left_tabs.setTabText(2, self.t("view_tab"))
        self.left_tabs.setTabText(3, self.t("telegram_tab"))
        self.left_tabs.setTabText(4, self.t("saved_tab"))

        self.right_tabs.setTabText(0, self.t("results_tab"))
        self.right_tabs.setTabText(1, self.t("notifications_tab"))
        self.right_tabs.setTabText(2, self.t("log_tab"))

        self.search_group.setTitle(self.t("active_search"))
        self.search_term_label.setText(self.t("search_term"))
        self.category_label.setText(self.t("category"))
        self.subcategory_label.setText(self.t("subcategory"))
        self.region_label.setText(self.t("region"))
        self.distance_label.setText(self.t("distance"))
        self.max_price_label.setText(self.t("max_price"))
        self.interval_label.setText(self.t("interval"))
        self.adaptive_enabled.setText(self.t("adaptive_toggle"))
        self.max_interval_label.setText(self.t("max_interval"))
        self.quiet_enabled.setText(self.t("quiet_toggle"))
        self.quiet_label.setText(self.t("quiet_hours"))
        self.results_limit_label.setText(self.t("results_limit"))
        self.use_notifications.setText(self.t("telegram_toggle"))
        self.auto_mark.setText(self.t("highlight_toggle"))
        self.new_marker_label.setText(self.t("new_marker_duration"))
        self.free_only.setText(self.t("free_only_toggle"))
        self.hide_promoted.setText(self.t("hide_promoted_toggle"))
        self.start_btn.setText(self.t("start"))
        self.stop_btn.setText(self.t("stop"))
        self.check_now_btn.setText(self.t("check_now"))
        self.save_btn.setText(self.t("save"))

        self.new_btn.setText(self.t("new"))
        self.edit_btn.setText(self.t("edit"))
        self.delete_btn.setText(self.t("delete"))
        self.use_btn.setText(self.t("use_profile"))

        self.view_group.setTitle(self.t("view_group"))
        self.language_label.setText(self.t("language"))
        self.show_prices.setText(self.t("show_price"))
        self.show_location.setText(self.t("show_location"))
        self.show_time.setText(self.t("show_time"))
        self.compact_mode.setText(self.t("compact"))

        self.telegram_enabled.setText(self.t("telegram_toggle"))
        self.clear_btn.setText(self.t("clear_notifications"))
        self.open_link_btn.setText(self.t("open_link"))
        self.preview_open_btn.setText(self.t("open_external"))
        self.save_selected_btn.setText(self.t("save_selected"))
        self.new_list_btn.setText(self.t("new_list"))
        self.load_list_btn.setText(self.t("load_list"))
        self.save_list_file_btn.setText(self.t("save_list_file"))
        self.share_list_btn.setText(self.t("share_list"))
        self.saved_lists_group.setTitle(self.t("saved_lists"))
        self.saved_items_group.setTitle(self.t("saved_items"))
        self.preview_group.setTitle(self.t("preview_title"))
        self.preview_box.setPlainText(self.t("preview_empty"))
        self.appearance_action.setText(self.t("appearance"))
        self.info_action.setText(self.t("info"))
        self.update_region_warning()

        self.results_table.setHorizontalHeaderLabels(
            [
                "Sel",
                "ID",
                "Titel" if self.current_language == "Nederlands" else "Title",
                "Prijs" if self.current_language == "Nederlands" else "Price",
                "Locatie" if self.current_language == "Nederlands" else "Location",
                "Tijd" if self.current_language == "Nederlands" else "Posted",
                "Status",
                "Link",
            ]
        )

    def apply_theme(self):
        t = self.theme()
        # Leeg betekent: laat Qt zelf het systeemlettertype kiezen.
        font_family = self.settings.value("ui/font_family", "") or system_font_family()
        font_size = int(self.settings.value("ui/font_size", 10))
        QApplication.instance().setFont(QFont(font_family, font_size))
        self.setStyleSheet(build_stylesheet(t))

    def load_settings_into_ui(self):
        self.search_term.setText(self.settings.value("search/term", "pokemon kaarten"))
        self.category.setCurrentText(
            self.category_store.name_for_id(self.settings.value("search/category_id", ""))
        )
        self.region.setText(self.settings.value("search/region", ""))
        self.distance.setValue(int(self.settings.value("search/distance", 0)))
        self.max_price.setValue(float(self.settings.value("search/max_price", 150)))
        self.interval.setValue(int(self.settings.value("search/interval", 60)))
        self.results_limit.setValue(int(self.settings.value("search/results_limit", 50)))
        self.adaptive_enabled.setChecked(
            self.settings.value("search/adaptive", "true") == "true"
        )
        self.max_interval.setValue(int(self.settings.value("search/max_interval", 5)))
        self.quiet_enabled.setChecked(
            self.settings.value("search/quiet", "false") == "true"
        )
        self.quiet_from.setTime(
            QTime.fromString(self.settings.value("search/quiet_from", "00:00"), "HH:mm")
        )
        self.quiet_to.setTime(
            QTime.fromString(self.settings.value("search/quiet_to", "07:00"), "HH:mm")
        )
        self.use_notifications.setChecked(
            self.settings.value("search/notifications", "false") == "true"
        )
        self.auto_mark.setChecked(
            self.settings.value("search/auto_mark", "true") == "true"
        )
        self.free_only.setChecked(
            self.settings.value("search/free_only", "false") == "true"
        )
        self.hide_promoted.setChecked(
            self.settings.value("search/hide_promoted", "true") == "true"
        )
        self.new_marker_minutes.setValue(
            int(
                self.settings.value(
                    "search/new_marker_minutes", self.DEFAULT_NEW_MARKER_MINUTES
                )
            )
        )
        self.show_prices.setChecked(
            self.settings.value("view/show_prices", "true") == "true"
        )
        self.show_location.setChecked(
            self.settings.value("view/show_location", "true") == "true"
        )
        self.show_time.setChecked(
            self.settings.value("view/show_time", "true") == "true"
        )
        self.compact_mode.setChecked(
            self.settings.value("view/compact", "false") == "true"
        )
        self.language_combo.setCurrentText(self.current_language)
        self.telegram_enabled.setChecked(
            self.settings.value("telegram/enabled", "false") == "true"
        )
        self.bot_token.setText(self.settings.value("telegram/bot_token", ""))
        self.chat_id.setText(self.settings.value("telegram/chat_id", ""))
        self.message_prefix.setText(
            self.settings.value("telegram/prefix", "Nieuwe Marktplaats advertentie")
        )
        self.apply_view_options()
        self.update_region_warning()
        self.results_table.setSortingEnabled(True)

    def save_form_settings(self):
        self.settings.setValue("search/term", self.search_term.text().strip())
        self.settings.setValue("search/category_id", self.selected_category_id() or "")
        self.settings.setValue("search/region", self.region.text().strip())
        self.settings.setValue("search/distance", self.distance.value())
        self.settings.setValue("search/max_price", self.max_price.value())
        self.settings.setValue("search/interval", self.interval.value())
        self.settings.setValue("search/results_limit", self.results_limit.value())
        self.settings.setValue(
            "search/adaptive", str(self.adaptive_enabled.isChecked()).lower()
        )
        self.settings.setValue("search/max_interval", self.max_interval.value())
        self.settings.setValue("search/quiet", str(self.quiet_enabled.isChecked()).lower())
        self.settings.setValue("search/quiet_from", self.quiet_from.time().toString("HH:mm"))
        self.settings.setValue("search/quiet_to", self.quiet_to.time().toString("HH:mm"))
        self.settings.setValue(
            "search/notifications", str(self.use_notifications.isChecked()).lower()
        )
        self.settings.setValue(
            "search/auto_mark", str(self.auto_mark.isChecked()).lower()
        )
        self.settings.setValue(
            "search/free_only", str(self.free_only.isChecked()).lower()
        )
        self.settings.setValue(
            "search/hide_promoted", str(self.hide_promoted.isChecked()).lower()
        )
        self.settings.setValue(
            "search/new_marker_minutes", self.new_marker_minutes.value()
        )
        self.settings.setValue(
            "view/show_prices", str(self.show_prices.isChecked()).lower()
        )
        self.settings.setValue(
            "view/show_location", str(self.show_location.isChecked()).lower()
        )
        self.settings.setValue("view/show_time", str(self.show_time.isChecked()).lower())
        self.settings.setValue("view/compact", str(self.compact_mode.isChecked()).lower())
        self.settings.setValue("ui/language", self.current_language)
        self.apply_view_options()
        self.log(self.t("settings_saved"))

    def save_telegram_settings(self):
        self.settings.setValue(
            "telegram/enabled", str(self.telegram_enabled.isChecked()).lower()
        )
        self.settings.setValue("telegram/bot_token", self.bot_token.text().strip())
        self.settings.setValue("telegram/chat_id", self.chat_id.text().strip())
        self.settings.setValue("telegram/prefix", self.message_prefix.text().strip())
        self.log("Telegram instellingen opgeslagen.")

    def test_telegram(self):
        self.save_telegram_settings()
        token = self.bot_token.text().strip()
        chat_id = self.chat_id.text().strip()
        if not token or not chat_id:
            QMessageBox.warning(self, "Telegram", self.t("telegram_test_fill"))
            return
        ok, data = send_telegram_message(
            token,
            chat_id,
            f"Testbericht vanuit {APP_NAME} v{APP_VERSION}",
        )
        if ok:
            QMessageBox.information(self, "Telegram", self.t("telegram_test_ok"))
        else:
            QMessageBox.warning(
                self, "Telegram", f"{self.t('telegram_test_fail')}: {data}"
            )

    def apply_view_options(self):
        self.results_table.setColumnHidden(3, not self.show_prices.isChecked())
        self.results_table.setColumnHidden(4, not self.show_location.isChecked())
        self.results_table.setColumnHidden(5, not self.show_time.isChecked())
        self.results_table.verticalHeader().setDefaultSectionSize(
            26 if self.compact_mode.isChecked() else 34
        )

    def set_language(self, lang):
        self.current_language = lang
        self.settings.setValue("ui/language", lang)
        for action in self.language_group:
            action.setChecked(action.text() == lang)
        if self.language_combo.currentText() != lang:
            self.language_combo.setCurrentText(lang)
        self.apply_language()

    def show_info(self):
        changes_nl = (
            "\n\nNieuw in deze versie:\n"
            "- Zoekt via de zoek-API van Marktplaats, waardoor prijs, afstand\n"
            "  en categorie echt gefilterd worden\n"
            "- Sorteert op nieuwste eerst, zodat verse advertenties opvallen\n"
            "- Promotie-advertenties (Dagtopper) kunnen verborgen worden\n"
            "- Gezien-advertenties worden bewaard: geen meldingenvloed bij het starten\n"
            "- Adaptieve interval en nachtpauze beperken het aantal verzoeken\n"
            "- Telegram verstuurt op de achtergrond, zonder het venster te blokkeren\n"
            "- Waarschuwing als de regio geen postcode is\n"
            "- Nieuwe advertenties blijven een instelbare tijd gemarkeerd"
        )
        changes_en = (
            "\n\nNew in this version:\n"
            "- Searches through the Marktplaats search API, so price, distance\n"
            "  and category are actually applied\n"
            "- Sorts newest first, so fresh listings stand out\n"
            "- Promoted listings (Dagtopper) can be hidden\n"
            "- Seen listings are remembered: no flood of alerts on startup\n"
            "- Adaptive interval and quiet hours reduce the number of requests\n"
            "- Telegram sends in the background without freezing the window\n"
            "- Warns when the region is not a postcode\n"
            "- New listings stay marked for a configurable time"
        )
        changes = changes_nl if self.current_language == "Nederlands" else changes_en

        QMessageBox.information(
            self,
            self.t("info"),
            f"{APP_NAME}\n{self.t('version')} {APP_VERSION}\n"
            f"{self.t('updated')}: {LAST_UPDATE}{changes}",
        )

    def log(self, message):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {message}")

    def update_status(self, running):
        self.status_label.setText(
            self.t("status_running") if running else self.t("status_stopped")
        )

    def add_notification(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        full = f"[{ts}] {text}"
        self.notifications.append(full)
        self.notifications_list.addItem(full)

    def clear_notifications(self):
        self.notifications.clear()
        self.notifications_list.clear()
        self.log(self.t("all_notifications_cleared"))

    def start_monitor(self):
        if not self.confirm_region_problem():
            return

        self.save_form_settings()
        self.save_telegram_settings()

        limit = self.results_limit.value()
        if limit <= 100:
            # Elke start begint weer op de ingestelde snelheid.
            self.reset_interval()
            self.in_quiet_period = False

            self.timer.stop()
            self.timer.start(self.next_interval_ms())
            self.update_status(True)

            details = f"Interval {self.interval.value()}s (±{int(self.INTERVAL_JITTER * 100)}%)"
            if self.adaptive_enabled.isChecked():
                details += f", adaptief tot {self.max_interval.value()} min"
            if self.quiet_enabled.isChecked():
                details += (
                    f", nachtpauze {self.quiet_from.time().toString('HH:mm')}"
                    f"-{self.quiet_to.time().toString('HH:mm')}"
                )
            self.log(f"{self.t('monitor_started')} {details}.")
            self.run_monitor_cycle()
        else:
            self.timer.stop()
            self.update_status(True)
            self.log(self.t("monitor_started") + " (eenmalige bulk-scan)")
            self.run_monitor_cycle()

    def next_interval_ms(self):
        """Interval met wat ruis erop.

        Precies elke 60 seconden een verzoek is een patroon dat een mens nooit
        produceert; met wat spreiding lijkt het op gewoon gebruik.
        """
        base = self.current_interval_seconds or max(
            MIN_INTERVAL_SECONDS, self.interval.value()
        )
        spread = base * self.INTERVAL_JITTER
        return int(random.uniform(base - spread, base + spread) * 1000)

    def reset_interval(self):
        """Zet de wachttijd terug op wat de gebruiker heeft ingesteld."""
        self.current_interval_seconds = max(
            MIN_INTERVAL_SECONDS, self.interval.value()
        )
        self.empty_cycles = 0

    def adapt_interval(self, found_new):
        """Pas de wachttijd aan op hoe vaak er echt iets nieuws verschijnt.

        Een smalle zoekopdracht levert misschien een paar advertenties per dag
        op. Daar elke minuut voor terugkomen is verspilde moeite, dus bij stilte
        loopt de wachttijd op. Zodra er wél iets nieuws is, gaat hij meteen weer
        terug naar de ingestelde snelheid.
        """
        base = max(MIN_INTERVAL_SECONDS, self.interval.value())

        if not self.adaptive_enabled.isChecked():
            self.current_interval_seconds = base
            self.empty_cycles = 0
            return

        if found_new:
            if self.current_interval_seconds and self.current_interval_seconds > base:
                self.log(f"Nieuwe advertentie gevonden: interval terug naar {base}s.")
            self.current_interval_seconds = base
            self.empty_cycles = 0
            return

        ceiling = self.max_interval.value() * 60
        previous = self.current_interval_seconds or base
        self.current_interval_seconds = min(ceiling, previous * self.ADAPTIVE_GROWTH)
        self.empty_cycles += 1

        if self.current_interval_seconds > previous:
            self.log(
                f"Niets nieuws ({self.empty_cycles}x): interval nu "
                f"{int(self.current_interval_seconds)}s."
            )

    def quiet_period_active(self, now=None):
        """Valt dit moment binnen de ingestelde nachtpauze?"""
        if not self.quiet_enabled.isChecked():
            return False

        now = now or QTime.currentTime()
        start = self.quiet_from.time()
        end = self.quiet_to.time()

        if start == end:
            return False
        if start < end:
            return start <= now < end
        # De pauze loopt over middernacht heen, bijvoorbeeld 23:00 tot 07:00.
        return now >= start or now < end

    def seconds_until_quiet_end(self, now=None):
        now = now or QTime.currentTime()
        seconds = now.secsTo(self.quiet_to.time())
        if seconds <= 0:
            seconds += 24 * 3600
        return seconds

    def stop_monitor(self):
        self.timer.stop()
        self.update_status(False)
        self.log(self.t("monitor_stopped"))

    def run_manual_check(self):
        if not self.confirm_region_problem():
            return

        self.save_form_settings()
        self.save_telegram_settings()
        self.run_monitor_cycle()

    def run_monitor_cycle(self):
        if self.is_refreshing:
            self.log("Refresh overgeslagen: vorige cyclus loopt nog.")
            return

        term = self.search_term.text().strip()
        if not term:
            self.log(self.t("no_term"))
            return

        # Tijdens de nachtpauze niets opvragen. De timer wordt in één keer op het
        # einde van de pauze gezet, zodat er ook geen wekkers blijven afgaan.
        if self.timer.isActive() and self.quiet_period_active():
            wait = self.seconds_until_quiet_end()
            self.timer.start(int(wait * 1000))
            if not self.in_quiet_period:
                self.in_quiet_period = True
                self.update_status(True)
                self.log(
                    f"Nachtpauze actief tot {self.quiet_to.time().toString('HH:mm')}; "
                    f"volgende check over {wait // 3600}u {(wait % 3600) // 60}m."
                )
            return

        if self.in_quiet_period:
            self.in_quiet_period = False
            self.log("Nachtpauze voorbij, monitor hervat.")

        self.is_refreshing = True
        self.start_btn.setEnabled(False)
        self.check_now_btn.setEnabled(False)

        self.worker_thread = QThread()
        self.worker = MonitorWorker(
            self.monitor,
            term,
            self.max_price.value(),
            self.results_limit.value(),
            region=self.region.text().strip() or None,
            distance_km=self.distance.value() or None,
            free_only=self.free_only.isChecked(),
            category_id=self.selected_category_id(),
            subcategory_id=self.selected_subcategory_id(),
            hide_promoted=self.hide_promoted.isChecked(),
        )
        self.worker.moveToThread(self.worker_thread)
        self.worker.finished.connect(self.on_monitor_finished)
        self.worker_thread.started.connect(self.worker.run)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def on_monitor_finished(self, new_items, all_items, error):
        if error.startswith("RATE_LIMIT|"):
            message = error.split("|", 1)[1]
            # Doorgaan zou de blokkade alleen verlengen.
            self.timer.stop()
            self.update_status(False)
            self.log(f"Monitor gestopt: {message}")
            QMessageBox.warning(self, self.t("monitor_error"), message)
        elif error:
            self.log(f"{self.t('monitor_error')}: {error}")
            # Tijdens het monitoren alleen loggen: een modaal venster per mislukte
            # cyclus stapelt zich op zodra het internet even wegvalt.
            if not self.timer.isActive():
                QMessageBox.warning(self, self.t("monitor_error"), error)
        else:
            self.current_results = all_items
            self.populate_results_table(all_items, new_items)
            self.refresh_subcategories()
            if self.category_store.update_from_response(self.monitor.last_category_options):
                self.reload_category_combo()

            self.log(
                f"{self.t('results_found')}: {len(all_items)}. "
                f"{self.t('new_items')}: {len(new_items)}"
            )

            if getattr(self.monitor, "last_search_exhausted", False):
                self.log(
                    "Deze zoekterm staat vrijwel helemaal vol met promotie-advertenties. "
                    "Maak de zoekterm specifieker of kies een categorie, anders blijft er "
                    "na het filteren weinig over."
                )

            if getattr(self.monitor, "last_scan_was_priming", False):
                self.log(
                    f"Eerste scan voor deze zoekterm: {len(all_items)} bestaande "
                    "advertenties onthouden, geen meldingen verstuurd. "
                    "Vanaf nu krijg je alleen nieuwe advertenties."
                )
                # Een eerste scan levert per definitie geen nieuwe advertenties
                # op. Dat is geen stilte, dus de interval hoort op de ingestelde
                # snelheid te blijven staan in plaats van meteen op te lopen.
                self.reset_interval()
            else:
                self.notify_new_items(new_items)
                self.adapt_interval(bool(new_items))

        self.is_refreshing = False
        self.start_btn.setEnabled(True)
        self.check_now_btn.setEnabled(True)

        if self.timer.isActive():
            # Elke cyclus een nieuwe spreiding op de interval.
            self.timer.start(self.next_interval_ms())

        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait()

        self.worker_thread = None
        self.worker = None

    def notify_new_items(self, new_items):
        """Meld nieuwe advertenties in de app en, als dat aanstaat, via Telegram."""
        for item in new_items:
            self.add_notification(f"{self.t('new_ad')}: {item['title']}")

        if not new_items:
            return
        if not (
            self.use_notifications.isChecked()
            and self.settings.value("telegram/enabled", "false") == "true"
        ):
            return

        for item in new_items[: self.MAX_TELEGRAM_MESSAGES_PER_CYCLE]:
            self.send_telegram(item)

        overflow = len(new_items) - self.MAX_TELEGRAM_MESSAGES_PER_CYCLE
        if overflow > 0:
            # Een brede zoekterm kan in één cyclus tientallen nieuwe advertenties
            # opleveren. Die één voor één sturen loopt tegen de limieten van
            # Telegram aan, dus de rest gaat als één samenvatting mee.
            summary = "\n".join(
                f"- {i['title']} ({i['price']}) {i['url']}"
                for i in new_items[self.MAX_TELEGRAM_MESSAGES_PER_CYCLE :]
            )
            self.queue_telegram(f"En nog {overflow} nieuwe advertenties:\n\n{summary}")

    def remember_new_items(self, new_items):
        """Onthoud wanneer een advertentie voor het eerst langskwam.

        De markering in de Statuskolom hing eerder aan één cyclus: keek je net
        niet op dat moment, dan was er niets meer aan te zien. Met dit geheugen
        blijft een verse advertentie een tijdje herkenbaar, inclusief hoe lang
        geleden hij verscheen.
        """
        now = time.time()
        for item in new_items:
            self.new_since.setdefault(item["id"], now)

        cutoff = now - self.new_marker_seconds()
        for item_id in [k for k, t in self.new_since.items() if t < cutoff]:
            del self.new_since[item_id]

    def new_marker_seconds(self):
        return self.new_marker_minutes.value() * 60

    def on_new_marker_duration_changed(self):
        """Korter zetten moet meteen zichtbaar zijn, niet pas na de volgende cyclus."""
        cutoff = time.time() - self.new_marker_seconds()
        for item_id in [k for k, t in self.new_since.items() if t < cutoff]:
            del self.new_since[item_id]
        self.refresh_new_markers()

    def new_marker(self, item_id):
        """Tekst voor de Statuskolom, of "" als de advertentie niet vers meer is."""
        first_seen = self.new_since.get(item_id)
        if first_seen is None:
            return ""

        minutes = int((time.time() - first_seen) // 60)
        if minutes < 1:
            return self.t("new")
        return f"{self.t('new')} ({minutes} min)"

    def populate_results_table(self, items, new_items):
        self.remember_new_items(new_items)
        selected_id = self.selected_result_id()

        self.results_table.setSortingEnabled(False)
        # Tijdens het opnieuw vullen mag itemChanged de vinkjes niet bijwerken.
        self.results_table.blockSignals(True)
        self.results_table.setRowCount(0)

        for item in items:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)

            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            checkbox_item.setCheckState(
                Qt.CheckState.Checked
                if item.get("id") in self.checked_ids
                else Qt.CheckState.Unchecked
            )
            self.results_table.setItem(row, 0, checkbox_item)

            vals = [
                item.get("id", ""),
                item.get("title", ""),
                item.get("price", ""),
                item.get("location", ""),
                item.get("time", ""),
                self.new_marker(item.get("id", "")),
                item.get("url", ""),
            ]

            for offset, val in enumerate(vals, start=1):
                twi = QTableWidgetItem(str(val))
                if offset == 6 and val and self.auto_mark.isChecked():
                    twi.setForeground(QColor(self.theme().success))
                self.results_table.setItem(row, offset, twi)

        self.results_table.blockSignals(False)
        self.apply_view_options()

        if items:
            # Blijf op de advertentie staan die de gebruiker aan het bekijken was.
            row = self.row_for_id(selected_id)
            self.results_table.selectRow(row)
            self.show_result_preview(row)
        else:
            self.preview_box.setPlainText(self.t("preview_empty"))

        self.results_table.setSortingEnabled(True)

    def selected_result_id(self):
        row = self.results_table.currentRow()
        if row < 0:
            return None
        item = self.results_table.item(row, 1)
        return item.text() if item else None

    def row_for_id(self, item_id):
        if item_id:
            for row in range(self.results_table.rowCount()):
                cell = self.results_table.item(row, 1)
                if cell and cell.text() == item_id:
                    return row
        return 0

    def on_result_item_changed(self, item):
        if item.column() != 0:
            return
        id_cell = self.results_table.item(item.row(), 1)
        if not id_cell:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self.checked_ids.add(id_cell.text())
        else:
            self.checked_ids.discard(id_cell.text())

    def refresh_new_markers(self):
        """Werk alleen de Statuskolom bij, zodat de leeftijd blijft kloppen.

        Ook als er niets meer te markeren valt moet deze lus draaien: anders
        blijft een verlopen markering in beeld staan.
        """
        if self.is_refreshing:
            return

        self.results_table.blockSignals(True)
        for row in range(self.results_table.rowCount()):
            id_cell = self.results_table.item(row, 1)
            status_cell = self.results_table.item(row, 6)
            if not id_cell or not status_cell:
                continue
            marker = self.new_marker(id_cell.text())
            if status_cell.text() != marker:
                status_cell.setText(marker)
                if marker and self.auto_mark.isChecked():
                    status_cell.setForeground(QColor(self.theme().success))
                else:
                    # Kleur terugzetten, anders houdt een lege cel de opmaak
                    # van de verlopen markering.
                    status_cell.setForeground(QColor(self.theme().text))
        self.results_table.blockSignals(False)

    def show_selected_result_preview(self):
        row = self.results_table.currentRow()
        if row >= 0:
            self.show_result_preview(row)

    def result_by_id(self, item_id):
        for item in self.current_results:
            if item.get("id") == item_id:
                return item
        return None

    def show_result_preview(self, row):
        def txt(col):
            cell = self.results_table.item(row, col)
            return cell.text() if cell else ""

        item = self.result_by_id(txt(1)) or {}
        lines = [
            f"Titel: {txt(2)}",
            "",
            f"Prijs: {txt(3)}",
            f"Locatie: {txt(4)}",
            f"Tijd: {txt(5)}",
        ]
        if item.get("seller"):
            lines.append(f"Verkoper: {item['seller']}")
        lines.append(f"Status: {txt(6)}")
        if item.get("description"):
            lines += ["", "Omschrijving:", item["description"]]
        lines += ["", f"Link: {txt(7)}"]

        self.preview_box.setPlainText("\n".join(lines))

    def get_selected_result_items(self):
        """De aangevinkte advertenties, met alle velden uit het zoekresultaat."""
        selected = []
        for row in range(self.results_table.rowCount()):
            checkbox = self.results_table.item(row, 0)
            if not checkbox or checkbox.checkState() != Qt.CheckState.Checked:
                continue

            id_cell = self.results_table.item(row, 1)
            item = self.result_by_id(id_cell.text()) if id_cell else None
            if item:
                selected.append(dict(item))
        return selected

    def save_selected_results_to_list(self):
        selected = self.get_selected_result_items()
        if not selected:
            QMessageBox.information(self, APP_NAME, self.t("no_selection"))
            return

        name, ok = QInputDialog.getText(self, APP_NAME, self.t("choose_list_name"))
        if not ok or not name.strip():
            return

        existing = self.saved_manager.load_list(name.strip())
        existing_ids = {i.get("id") for i in existing}
        for item in selected:
            if item.get("id") not in existing_ids:
                existing.append(item)

        self.saved_manager.save_list(name.strip(), existing)
        self.reload_saved_lists()
        self.log(f"{self.t('selected_saved')}: {name.strip()}")

    def reload_saved_lists(self):
        self.saved_lists_widget.clear()
        for name in self.saved_manager.list_names():
            self.saved_lists_widget.addItem(name)

    def create_new_list(self):
        name, ok = QInputDialog.getText(self, APP_NAME, self.t("choose_list_name"))
        if ok and name.strip():
            self.saved_manager.save_list(name.strip(), [])
            self.reload_saved_lists()

    def load_selected_saved_list(self):
        item = self.saved_lists_widget.currentItem()
        if not item:
            return

        name = item.text()
        self.current_saved_items = self.saved_manager.load_list(name)
        self.saved_items_widget.clear()

        for entry in self.current_saved_items:
            self.saved_items_widget.addItem(entry.get("title", "Onbekend"))

        self.log(f"{self.t('list_loaded')}: {name}")

    def show_saved_item_preview(self):
        idx = self.saved_items_widget.currentRow()
        if idx < 0 or idx >= len(self.current_saved_items):
            return

        item = self.current_saved_items[idx]
        preview = (
            f"Titel: {item.get('title', '')}\n\n"
            f"Prijs: {item.get('price', '')}\n"
            f"Locatie: {item.get('location', '')}\n"
            f"Tijd: {item.get('time', '')}\n\n"
            f"Link: {item.get('url', '')}"
        )
        self.preview_box.setPlainText(preview)
        self.right_tabs.setCurrentIndex(0)

    def export_current_saved_list(self):
        item = self.saved_lists_widget.currentItem()
        if not item:
            return

        name = item.text()
        items = self.saved_manager.load_list(name)
        json_path, txt_path = self.saved_manager.save_list(name, items)
        QMessageBox.information(
            self,
            APP_NAME,
            f"{self.t('list_saved_file')}:\n{json_path}\n{txt_path}",
        )

    def share_current_saved_list(self):
        item = self.saved_lists_widget.currentItem()
        if not item:
            return
        QMessageBox.information(self, APP_NAME, self.t("share_info"))

    def open_selected_listing(self):
        row = self.results_table.currentRow()
        if row < 0:
            return

        item = self.results_table.item(row, 7)
        if item and item.text().strip():
            webbrowser.open(item.text().strip())

    def on_result_double_clicked(self, row, column):
        if column in (2, 7):
            self.open_selected_listing()

    def queue_telegram(self, text):
        token = self.bot_token.text().strip()
        chat_id = self.chat_id.text().strip()

        if not token or not chat_id:
            self.log(self.t("telegram_missing"))
            return

        self.telegram_sender.enqueue(token, chat_id, text)

    def send_telegram(self, item):
        prefix = self.message_prefix.text().strip() or "Nieuwe Marktplaats advertentie"

        lines = [
            prefix,
            "",
            f"Titel: {item.get('title', 'Onbekend')}",
            f"Prijs: {item.get('price', 'Onbekend')}",
        ]
        if item.get("location"):
            lines.append(f"Locatie: {item['location']}")
        lines.append(f"Tijd: {item.get('time', 'Onbekend')}")
        lines.append(f"Link: {item.get('url', '')}")

        self.queue_telegram("\n".join(lines))

    def closeEvent(self, event):
        self.timer.stop()
        self.marker_timer.stop()
        self.telegram_sender.stop()
        self.telegram_sender.wait(3000)

        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait(5000)

        super().closeEvent(event)

    def reload_profiles_list(self):
        self.profiles_list.clear()
        for p in self.profiles:
            item = QListWidgetItem(p.get("name", "Profiel"))
            item.setData(Qt.ItemDataRole.UserRole, p)
            self.profiles_list.addItem(item)

    def add_profile(self):
        dlg = SearchProfileDialog(self)
        if dlg.exec():
            p = dlg.get_data()
            self.profiles.append(p)
            save_profiles(self.settings, self.profiles)
            self.reload_profiles_list()
            self.log(f"{self.t('profile_added')}: {p.get('name')}")

    def edit_profile(self):
        item = self.profiles_list.currentItem()
        if not item:
            return

        idx = self.profiles_list.row(item)
        dlg = SearchProfileDialog(self, self.profiles[idx])
        if dlg.exec():
            self.profiles[idx] = dlg.get_data()
            save_profiles(self.settings, self.profiles)
            self.reload_profiles_list()
            self.log(self.t("profile_updated"))

    def delete_profile(self):
        item = self.profiles_list.currentItem()
        if not item:
            return

        idx = self.profiles_list.row(item)
        name = self.profiles[idx].get("name", "Profiel")
        if (
            QMessageBox.question(self, "Confirm", f"Delete profile '{name}'?")
            == QMessageBox.StandardButton.Yes
        ):
            self.profiles.pop(idx)
            save_profiles(self.settings, self.profiles)
            self.reload_profiles_list()
            self.log(f"{self.t('profile_removed')}: {name}")

    def use_selected_profile(self):
        item = self.profiles_list.currentItem()
        if not item:
            return

        p = item.data(Qt.ItemDataRole.UserRole)
        self.current_profile_name = p.get("name")
        self.search_term.setText(p.get("term", ""))
        self.category.setCurrentText(
            self.category_store.name_for_id(p.get("category_id")) or ALL_CATEGORIES
        )
        self.region.setText(p.get("region", ""))
        self.max_price.setValue(float(p.get("max_price", 150)))
        self.interval.setValue(int(p.get("interval", 60)))
        self.log(f"{self.t('profile_loaded')}: {self.current_profile_name}")

    def open_appearance_dialog(self):
        dlg = AppearanceDialog(self, self.settings)
        if dlg.exec():
            self.apply_theme()


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
