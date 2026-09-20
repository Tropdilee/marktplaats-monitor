import queue
import random
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime

from PyQt6.QtCore import Qt, QSettings, QSize, QTime, QTimer, QObject, pyqtSignal, QThread
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPixmap
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

from core.appinfo import APP_NAME, APP_VERSION, LAST_UPDATE, ORG_NAME
from core.categories import ALL_CATEGORIES, CategoryStore
from core.images import RULE_PREVIEW, RULE_THUMBNAIL, ImageLoader, sized_url
from core.monitor import MarktplaatsMonitor, RateLimited
from core.paths import data_file
from core.saved_lists import SavedListsManager
from core.secrets import SecretStore
from core.settings_manager import load_profiles, save_profiles
from core.telegram_client import (
    describe_error,
    find_chat_ids,
    get_bot_info,
    looks_like_token,
    send_telegram_message,
)
from core.translations import get_text, set_language, tr
from ui.dialogs import SearchProfileDialog, AppearanceDialog
from ui.theme import ThemeConfig, build_stylesheet, system_font_family



ALL_SUBCATEGORIES = "Alle subcategorieën"

# The monitor never goes below this interval. Marktplaats names no limit itself,
# so the safest course is a pace that does not stand out next to ordinary
# browsing.
MIN_INTERVAL_SECONDS = 30

# Dutch postcode: four digits (not starting with 0), optionally followed by two
# letters. Marktplaats accepts both forms.
POSTCODE_PATTERN = re.compile(r"^[1-9]\d{3}\s*([A-Za-z]{2})?$")


class MaskedTokenEdit(QLineEdit):
    """Shows the bot token as asterisks, with only the last characters readable.

    Clicking into the field brings the real token back so it can be pasted or
    edited; outside that, nothing usable is on screen. The real value is tracked
    separately, so saving never accidentally writes the asterisks.
    """

    ZICHTBARE_TEKENS = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._waarde = ""
        self._gemaskeerd = False

    def setValue(self, tekst):
        self._waarde = (tekst or "").strip()
        if self.hasFocus():
            super().setText(self._waarde)
            self._gemaskeerd = False
        else:
            self._toon_masker()

    def value(self):
        """The real token, even while the field shows asterisks."""
        if self._gemaskeerd:
            return self._waarde
        return self.text().strip()

    def _toon_masker(self):
        if not self._waarde:
            super().setText("")
            self._gemaskeerd = False
            return
        staart = self._waarde[-self.ZICHTBARE_TEKENS :]
        verborgen = max(0, len(self._waarde) - len(staart))
        super().setText("*" * verborgen + staart)
        self._gemaskeerd = True

    def focusInEvent(self, event):
        if self._gemaskeerd:
            super().setText(self._waarde)
            self._gemaskeerd = False
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        if not self._gemaskeerd:
            self._waarde = self.text().strip()
        super().focusOutEvent(event)
        self._toon_masker()


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
            # Flagged separately so the window can stop the monitor.
            self.finished.emit([], [], f"RATE_LIMIT|{e}")
        except Exception as e:
            self.finished.emit([], [], f"{type(e).__name__}: {e}")


class TelegramSender(QThread):
    """Sends Telegram messages off the GUI thread.

    Notifications used to go out straight from the GUI thread, which froze the
    window for seconds on a run of new listings. The queue also accounts for
    Telegram accepting only a limited number of messages per minute to the same
    chat.
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
                self.logged.emit(tr("log_telegram_error").format(fout=f"{type(exc).__name__}: {exc}"))
            else:
                if ok:
                    self.logged.emit(tr("log_telegram_sent"))
                else:
                    self.logged.emit(tr("log_telegram_error").format(fout=data))

            if self._stop_event.wait(self.SECONDS_BETWEEN_MESSAGES):
                break


class MainWindow(QMainWindow):
    # Above this count the rest of a cycle goes out as a single summary.
    MAX_TELEGRAM_MESSAGES_PER_CYCLE = 10

    # Jitter on the interval, so requests never fall into an exact rhythm.
    INTERVAL_JITTER = 0.2

    # How much the interval grows per empty cycle while nothing is new.
    ADAPTIVE_GROWTH = 1.5

    # Default lifetime of the "new" marker in the results, in minutes.
    DEFAULT_NEW_MARKER_MINUTES = 15

    # Thumbnail size in the results list.
    THUMBNAIL_SIZE = QSize(64, 48)

    def __init__(self):
        super().__init__()
        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.monitor = MarktplaatsMonitor()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.run_monitor_cycle)
        self.is_refreshing = False
        self.worker_thread = None
        self.worker = None

        self.secret_store = SecretStore(self.settings)
        self.category_store = CategoryStore()
        self.profiles = load_profiles(self.settings)
        self.notifications = []
        self.current_profile_name = None
        self.current_language = self.settings.value("ui/language", "Nederlands")
        set_language(self.current_language)
        self.saved_manager = SavedListsManager(
            data_file("saved_lists")
        )
        self.current_results = []
        self.current_saved_items = []
        # Ticked listings survive a refresh of the table this way.
        self.checked_ids = set()

        # {listing id: moment it first showed up}
        self.new_since = {}

        # Current wait between two checks; grows while nothing is new.
        self.current_interval_seconds = None
        self.empty_cycles = 0
        self.in_quiet_period = False

        # The age in the marker keeps running, even with no cycle in progress.
        self.marker_timer = QTimer(self)
        self.marker_timer.timeout.connect(self.refresh_new_markers)
        self.marker_timer.start(30000)

        # url -> QPixmap, only to be touched from the GUI thread.
        self.image_cache = {}
        self.image_loader = ImageLoader(parent=self)
        self.image_loader.loaded.connect(self.on_image_loaded)
        self.image_loader.start()

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

        if not self.secret_store.available:
            self.log(self.t("log_no_keyring"))

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
        # Tighter margins and spacing, but still one blank line around the title
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

        # The status label sits under the start button on the search tab, no longer in the header
        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft)

        outer.addWidget(self.title_label)
        outer.addWidget(self.subtitle_label)
        # Without these two the labels claim part of the free space, leaving an
        # empty gap between the title and the tabs.
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

        # The main category comes from the stored list; subcategories belong to
        # the search term and are refreshed after every search.
        self.category = QComboBox()
        self.category.addItems(self.category_store.names())
        self.category.currentTextChanged.connect(self.on_category_changed)

        self.subcategory = QComboBox()
        self.subcategory.addItem(ALL_SUBCATEGORIES)
        self.subcategory.setEnabled(False)

        # Distance around the region (in km)
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

        # Adaptive interval: take it easy while nothing happens, back to full
        # speed the moment a new listing appears.
        self.adaptive_enabled = QCheckBox()
        self.max_interval_label = QLabel()
        self.max_interval = QSpinBox()
        self.max_interval.setRange(1, 60)
        self.max_interval.setSuffix(" min")

        # Quiet hours: during the hours you would not act on a find anyway,
        # nothing needs to be requested.
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
        self.use_notifications.toggled.connect(self.set_telegram_enabled)
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

        # A wrong region or a distance of 0 is silently ignored by Marktplaats:
        # you then get results from the whole country without anything visibly
        # going wrong. Hence this warning on screen.
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
        self.show_images = QCheckBox()
        self.show_images.toggled.connect(self.on_show_images_toggled)
        self.language_combo = QComboBox()
        self.language_combo.addItems(["Nederlands", "English"])
        self.language_combo.currentTextChanged.connect(self.set_language)

        form.addWidget(self.language_label, 0, 0)
        form.addWidget(self.language_combo, 0, 1)
        form.addWidget(self.show_prices, 1, 0, 1, 2)
        form.addWidget(self.show_location, 2, 0, 1, 2)
        form.addWidget(self.show_time, 3, 0, 1, 2)
        form.addWidget(self.compact_mode, 4, 0, 1, 2)
        form.addWidget(self.show_images, 5, 0, 1, 2)

        layout.addWidget(self.view_group)
        layout.addStretch(1)
        return w

    def build_telegram_tab(self):
        w = QWidget()
        layout = QVBoxLayout(w)

        box = QGroupBox("Telegram")
        form = QGridLayout(box)

        self.telegram_enabled = QCheckBox()
        self.telegram_enabled.toggled.connect(self.set_telegram_enabled)
        self.bot_token_label = QLabel()
        self.chat_id_label = QLabel()
        self.prefix_label = QLabel()
        self.bot_token = MaskedTokenEdit()
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
        self.telegram_save_btn = QPushButton()
        self.telegram_test_btn = QPushButton()
        self.telegram_chatid_btn = QPushButton()
        self.telegram_save_btn.clicked.connect(self.save_telegram_settings)
        self.telegram_test_btn.clicked.connect(self.test_telegram)
        self.telegram_chatid_btn.clicked.connect(self.fetch_chat_id)

        row.addWidget(self.telegram_save_btn)
        row.addWidget(self.telegram_test_btn)
        row.addWidget(self.telegram_chatid_btn)
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
        # The title is the column you actually want to read, so it gets the space.
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        # Keep it narrow: the link opens by double click and by the button, and
        # the full URL is in the preview.
        self.results_table.setColumnWidth(7, 90)

        # Without this, long titles and links wrap across several lines and the
        # rows grow so tall that only a handful fit on screen.
        self.results_table.setWordWrap(False)
        self.results_table.setTextElideMode(Qt.TextElideMode.ElideRight)

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

        self.preview_image = QLabel()
        self.preview_image.setObjectName("previewImage")
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_image.setMaximumSize(QSize(260, 190))
        self.preview_image.setMinimumHeight(0)
        self.preview_image.hide()
        preview_layout.addWidget(self.preview_image, alignment=Qt.AlignmentFlag.AlignHCenter)

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
        """Describe what is wrong with the location filter, or "" when it is fine.

        Marktplaats accepts a postcode only, and only together with a distance.
        Anything else is ignored without an error, so without this check the
        filter looks like it works while results come in from the whole country.
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
        """Ask first when the location filter will not work. True = search anyway."""
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
        """Refresh the main categories without losing the user's choice."""
        current = self.category.currentText()
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems(self.category_store.names())
        self.category.setCurrentText(current)
        self.category.blockSignals(False)

    def on_category_changed(self, _name=None):
        # Subcategories belong to one main category, so switching that one makes
        # the old list invalid.
        self.subcategory.clear()
        self.subcategory.addItem(ALL_SUBCATEGORIES)
        self.subcategory.setEnabled(False)

    def refresh_subcategories(self):
        """Fill the subcategories with what the last search returned."""
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
        self.show_images.setText(self.t("show_images"))

        self.telegram_enabled.setText(self.t("telegram_toggle"))
        self.bot_token_label.setText(self.t("bot_token"))
        self.chat_id_label.setText(self.t("chat_id"))
        self.prefix_label.setText(self.t("prefix"))
        self.telegram_save_btn.setText(self.t("telegram_save_btn"))
        self.telegram_test_btn.setText(self.t("telegram_test_btn"))
        self.telegram_chatid_btn.setText(self.t("telegram_chatid_btn"))
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
        # Empty means: let Qt pick the system font itself.
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
        # One setting, two checkboxes; telegram_enabled is set further below.
        self.use_notifications.setChecked(
            self.settings.value("telegram/enabled", "false") == "true"
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
        self.show_images.setChecked(
            self.settings.value("view/show_images", "true") == "true"
        )
        self.language_combo.setCurrentText(self.current_language)
        self.telegram_enabled.setChecked(
            self.settings.value("telegram/enabled", "false") == "true"
        )
        self.bot_token.setValue(self.secret_store.get_token())
        self.chat_id.setText(self.settings.value("telegram/chat_id", ""))
        self.message_prefix.setText(
            self.settings.value("telegram/prefix", self.t("default_prefix"))
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
        self.settings.setValue("view/show_images", str(self.show_images.isChecked()).lower())
        self.settings.setValue("ui/language", self.current_language)
        self.apply_view_options()
        self.log(self.t("settings_saved"))

    def save_telegram_settings(self):
        self.settings.setValue(
            "telegram/enabled", str(self.telegram_enabled.isChecked()).lower()
        )
        self.secret_store.set_token(self.bot_token.value())
        self.settings.setValue("telegram/chat_id", self.chat_id.text().strip())
        self.settings.setValue("telegram/prefix", self.message_prefix.text().strip())
        self.log(self.t("log_telegram_saved").format(opslag=self.secret_store.describe()))

    def set_telegram_enabled(self, aan):
        """Keep the two checkboxes in step.

        The same checkbox appears on the Search tab and on the Telegram tab.
        They used to be two separate settings that both had to be on, carrying
        the same label - so you tick one and nothing happens.
        """
        for vakje in (self.use_notifications, self.telegram_enabled):
            if vakje.isChecked() != aan:
                vakje.blockSignals(True)
                vakje.setChecked(aan)
                vakje.blockSignals(False)
        self.settings.setValue("telegram/enabled", str(bool(aan)).lower())

    def test_telegram(self):
        """Check the token first, and only then the chat ID.

        Otherwise every failure produces the same vague message and you cannot
        tell which of the two fields needs looking at.
        """
        self.save_telegram_settings()
        token = self.bot_token.value()
        chat_id = self.chat_id.text().strip()

        if not token or not chat_id:
            QMessageBox.warning(self, "Telegram", self.t("telegram_test_fill"))
            return

        if not looks_like_token(token):
            uitleg = self.t("token_wrong_shape").format(
                aantal=len(token),
                extra=self.t("token_with_space") if " " in token else "",
            )
            self.log(f"Telegram test: {uitleg.splitlines()[0]}")
            QMessageBox.warning(self, "Telegram", uitleg)
            return

        ok, data = get_bot_info(token)
        if not ok:
            uitleg = describe_error(data)
            self.log(f"Telegram test mislukt: {uitleg}")
            QMessageBox.warning(self, "Telegram", uitleg)
            return

        botnaam = (data.get("result") or {}).get("username", "?")

        ok, data = send_telegram_message(
            token, chat_id, f"{self.t('test_message')} {APP_NAME} v{APP_VERSION}"
        )
        if ok:
            self.log(f"Telegram test geslaagd via @{botnaam}.")
            QMessageBox.information(
                self,
                "Telegram",
                f"{self.t('telegram_test_ok')}\n\n{self.t('telegram_bot_label')}: @{botnaam}",
            )
        else:
            uitleg = describe_error(data)
            self.log(f"Telegram test mislukt: {uitleg}")
            QMessageBox.warning(
                self,
                "Telegram",
                self.t("token_works_but").format(bot=botnaam, fout=uitleg),
            )

    def fetch_chat_id(self):
        """Look up the chat ID from the messages the bot just received."""
        self.save_telegram_settings()
        token = self.bot_token.value()

        if not looks_like_token(token):
            QMessageBox.warning(
                self, "Telegram", self.t("enter_valid_token")
            )
            return

        ok, data, chats = find_chat_ids(token)
        if not ok:
            QMessageBox.warning(self, "Telegram", describe_error(data))
            return

        if not chats:
            QMessageBox.information(
                self,
                "Telegram",
                self.t("no_recent_messages"),
            )
            return

        if len(chats) == 1:
            chat_id, naam = chats[0]
            self.chat_id.setText(chat_id)
            self.save_telegram_settings()
            QMessageBox.information(
                self,
                "Telegram",
                self.t("chat_id_filled").format(id=chat_id, naam=naam),
            )
            return

        keuze, akkoord = QInputDialog.getItem(
            self,
            "Telegram",
            self.t("multiple_chats"),
            [f"{naam} — {chat_id}" for chat_id, naam in chats],
            0,
            False,
        )
        if akkoord and keuze:
            self.chat_id.setText(keuze.rsplit("—", 1)[-1].strip())
            self.save_telegram_settings()

    def apply_view_options(self):
        self.results_table.setColumnHidden(3, not self.show_prices.isChecked())
        self.results_table.setColumnHidden(4, not self.show_location.isChecked())
        self.results_table.setColumnHidden(5, not self.show_time.isChecked())
        toont_beeld = self.show_images.isChecked()
        self.results_table.setIconSize(
            self.THUMBNAIL_SIZE if toont_beeld else QSize(0, 0)
        )
        hoogte = 26 if self.compact_mode.isChecked() else 34
        if toont_beeld:
            # The row must fit the thumbnail, with a few pixels of breathing room.
            hoogte = max(hoogte, self.THUMBNAIL_SIZE.height() + 8)
        self.results_table.verticalHeader().setDefaultSectionSize(hoogte)

    def set_language(self, lang):
        self.current_language = lang
        # The separate windows and the sender thread take their text from here too.
        set_language(lang)
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
            # Every start begins at the configured speed again.
            self.reset_interval()
            self.in_quiet_period = False

            self.timer.stop()
            self.timer.start(self.next_interval_ms())
            self.update_status(True)

            details = self.t("detail_interval").format(
                seconden=self.interval.value(),
                spreiding=int(self.INTERVAL_JITTER * 100),
            )
            if self.adaptive_enabled.isChecked():
                details += self.t("detail_adaptive").format(
                    minuten=self.max_interval.value()
                )
            if self.quiet_enabled.isChecked():
                details += self.t("detail_quiet").format(
                    van=self.quiet_from.time().toString("HH:mm"),
                    tot=self.quiet_to.time().toString("HH:mm"),
                )
            self.log(self.t("log_monitor_started").format(details=details))
            self.run_monitor_cycle()
        else:
            self.timer.stop()
            self.update_status(True)
            self.log(self.t("log_monitor_started").format(details=self.t("bulk_scan")))
            self.run_monitor_cycle()

    def next_interval_ms(self):
        """Interval with some noise on it.

        A request exactly every 60 seconds is a pattern no person ever produces;
        with some spread it looks like ordinary use.
        """
        base = self.current_interval_seconds or max(
            MIN_INTERVAL_SECONDS, self.interval.value()
        )
        spread = base * self.INTERVAL_JITTER
        return int(random.uniform(base - spread, base + spread) * 1000)

    def reset_interval(self):
        """Reset the wait to whatever the user configured."""
        self.current_interval_seconds = max(
            MIN_INTERVAL_SECONDS, self.interval.value()
        )
        self.empty_cycles = 0

    def adapt_interval(self, found_new):
        """Adapt the wait to how often something new actually appears.

        A narrow search might yield a few listings a day. Coming back every
        minute for that is wasted effort, so during silence the wait grows. As
        soon as something new does turn up, it returns to the configured speed
        immediately.
        """
        base = max(MIN_INTERVAL_SECONDS, self.interval.value())

        if not self.adaptive_enabled.isChecked():
            self.current_interval_seconds = base
            self.empty_cycles = 0
            return

        if found_new:
            if self.current_interval_seconds and self.current_interval_seconds > base:
                self.log(self.t("log_interval_reset").format(seconden=base))
            self.current_interval_seconds = base
            self.empty_cycles = 0
            return

        ceiling = self.max_interval.value() * 60
        previous = self.current_interval_seconds or base
        self.current_interval_seconds = min(ceiling, previous * self.ADAPTIVE_GROWTH)
        self.empty_cycles += 1

        if self.current_interval_seconds > previous:
            self.log(
                self.t("log_nothing_new").format(
                    keer=self.empty_cycles,
                    seconden=int(self.current_interval_seconds),
                )
            )

    def quiet_period_active(self, now=None):
        """Does this moment fall within the configured quiet hours?"""
        if not self.quiet_enabled.isChecked():
            return False

        now = now or QTime.currentTime()
        start = self.quiet_from.time()
        end = self.quiet_to.time()

        if start == end:
            return False
        if start < end:
            return start <= now < end
        # The pause runs across midnight, for example 23:00 to 07:00.
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
            self.log(self.t("log_refresh_skipped"))
            return

        term = self.search_term.text().strip()
        if not term:
            self.log(self.t("no_term"))
            return

        # Request nothing during quiet hours. The timer is set straight to the
        # end of the pause, so no alarms keep going off either.
        if self.timer.isActive() and self.quiet_period_active():
            wait = self.seconds_until_quiet_end()
            self.timer.start(int(wait * 1000))
            if not self.in_quiet_period:
                self.in_quiet_period = True
                self.update_status(True)
                self.log(
                    self.t("log_quiet_active").format(
                        tot=self.quiet_to.time().toString("HH:mm"),
                        uren=wait // 3600,
                        minuten=(wait % 3600) // 60,
                    )
                )
            return

        if self.in_quiet_period:
            self.in_quiet_period = False
            self.log(self.t("log_quiet_over"))

        self.is_refreshing = True
        self.start_btn.setEnabled(False)
        self.check_now_btn.setEnabled(False)
        # Because of the wait between requests a cycle can take a few seconds.
        # Without this the window looks like it has hung.
        self.status_label.setText(self.t("status_searching"))

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
            # Carrying on would only prolong the block.
            self.timer.stop()
            self.update_status(False)
            self.log(self.t("log_monitor_blocked").format(reden=message))
            QMessageBox.warning(self, self.t("monitor_error"), message)
        elif error:
            self.log(f"{self.t('monitor_error')}: {error}")
            # Only log while monitoring: one modal window per failed cycle piles
            # up the moment the connection drops for a while.
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
                self.log(self.t("log_saturated"))

            if getattr(self.monitor, "last_scan_was_priming", False):
                self.log(self.t("log_first_scan").format(aantal=len(all_items)))
                # A first scan yields no new listings by definition. That is not
                # silence, so the interval should stay at the configured speed
                # instead of growing straight away.
                self.reset_interval()
            else:
                self.notify_new_items(new_items)
                self.adapt_interval(bool(new_items))

        self.is_refreshing = False
        self.start_btn.setEnabled(True)
        self.check_now_btn.setEnabled(True)
        self.update_status(self.timer.isActive())

        if self.timer.isActive():
            # Fresh jitter on the interval for every cycle.
            self.timer.start(self.next_interval_ms())

        if self.worker_thread is not None:
            self.worker_thread.quit()
            self.worker_thread.wait()

        self.worker_thread = None
        self.worker = None

    def notify_new_items(self, new_items):
        """Report new listings in the app and, when enabled, through Telegram."""
        for item in new_items:
            self.add_notification(f"{self.t('new_ad')}: {item['title']}")

        if not new_items:
            return
        if not self.telegram_enabled.isChecked():
            self.log(self.t("log_telegram_off"))
            return

        for item in new_items[: self.MAX_TELEGRAM_MESSAGES_PER_CYCLE]:
            self.send_telegram(item)

        overflow = len(new_items) - self.MAX_TELEGRAM_MESSAGES_PER_CYCLE
        if overflow > 0:
            # A broad search can produce dozens of new listings in one cycle.
            # Sending those one by one runs into Telegram's limits, so the rest
            # goes out as a single summary.
            summary = "\n".join(
                f"- {i['title']} ({i['price']}) {i['url']}"
                for i in new_items[self.MAX_TELEGRAM_MESSAGES_PER_CYCLE :]
            )
            self.queue_telegram(
                self.t("summary_more").format(aantal=overflow) + "\n\n" + summary
            )

    def remember_new_items(self, new_items):
        """Remember when a listing first came past.

        The marker in the status column used to last a single cycle: if you
        happened not to be looking at that moment, there was nothing left to
        see. With this memory a fresh listing stays recognisable for a while,
        including how long ago it appeared.
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
        """Shortening it must show immediately, not only after the next cycle."""
        cutoff = time.time() - self.new_marker_seconds()
        for item_id in [k for k, t in self.new_since.items() if t < cutoff]:
            del self.new_since[item_id]
        self.refresh_new_markers()

    def new_marker(self, item_id):
        """Text for the status column, or "" when the listing is no longer fresh."""
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
        # While repopulating, itemChanged must not update the ticked set.
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
                if offset == 2:
                    # The thumbnail hangs off the title cell, leaving the
                    # existing column layout untouched.
                    self.attach_thumbnail(twi, item.get("image", ""))
                if offset == 6 and val and self.auto_mark.isChecked():
                    twi.setForeground(QColor(self.theme().success))
                self.results_table.setItem(row, offset, twi)

        self.results_table.blockSignals(False)
        self.apply_view_options()

        if items:
            # Stay on the listing the user was looking at.
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

    def on_show_images_toggled(self, aan):
        self.apply_view_options()
        if self.current_results:
            self.populate_results_table(self.current_results, [])
        if not aan:
            self.preview_image.clear()
            self.preview_image.hide()

    def attach_thumbnail(self, cell, image_url):
        """Put the thumbnail on a cell, or request it when not yet available."""
        if not image_url or not self.show_images.isChecked():
            return

        url = sized_url(image_url, RULE_THUMBNAIL)
        pixmap = self.image_cache.get(url)
        if pixmap is not None:
            cell.setIcon(QIcon(pixmap))
        else:
            self.image_loader.request(url)

    def on_image_loaded(self, url, image):
        """An image has arrived; store it and put it on screen."""
        pixmap = QPixmap.fromImage(image).scaled(
            self.THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_cache[url] = pixmap

        if not self.show_images.isChecked():
            return

        # Both the list and the preview may be waiting for this image.
        self.results_table.blockSignals(True)
        for row in range(self.results_table.rowCount()):
            id_cell = self.results_table.item(row, 1)
            title_cell = self.results_table.item(row, 2)
            if not id_cell or not title_cell:
                continue
            item = self.result_by_id(id_cell.text())
            if item and sized_url(item.get("image", ""), RULE_THUMBNAIL) == url:
                title_cell.setIcon(QIcon(pixmap))
        self.results_table.blockSignals(False)

        if url == getattr(self, "pending_preview_url", None):
            self.preview_image.setPixmap(
                pixmap.scaled(
                    self.preview_image.maximumSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def show_preview_image(self, image_url):
        """Show the larger image above the preview text."""
        if not image_url or not self.show_images.isChecked():
            self.preview_image.clear()
            self.preview_image.hide()
            self.pending_preview_url = None
            return

        url = sized_url(image_url, RULE_PREVIEW)
        self.pending_preview_url = url
        self.preview_image.show()

        pixmap = self.image_cache.get(url)
        if pixmap is None:
            image = self.image_loader.cached_image(url)
            if image is not None:
                pixmap = QPixmap.fromImage(image)
                self.image_cache[url] = pixmap

        if pixmap is None:
            self.preview_image.setText(self.t("image_loading"))
            self.image_loader.request(url)
            return

        self.preview_image.setPixmap(
            pixmap.scaled(
                self.preview_image.maximumSize(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def refresh_new_markers(self):
        """Update the status column only, so the age stays correct.

        This loop must run even when there is nothing left to mark: otherwise an
        expired marker stays on screen.
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
                    # Reset the colour, otherwise an emptied cell keeps the
                    # styling of the expired marker.
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
        self.show_preview_image(item.get("image", ""))
        lines = [
            f"{self.t('label_title')}: {txt(2)}",
            "",
            f"{self.t('label_price')}: {txt(3)}",
            f"{self.t('label_location')}: {txt(4)}",
            f"{self.t('label_time')}: {txt(5)}",
        ]
        if item.get("seller"):
            lines.append(f"{self.t('label_seller')}: {item['seller']}")
        lines.append(f"{self.t('label_status')}: {txt(6)}")
        if item.get("description"):
            lines += ["", f"{self.t('label_description')}:", item["description"]]
        lines += ["", f"{self.t('label_link')}: {txt(7)}"]

        self.preview_box.setPlainText("\n".join(lines))

    def get_selected_result_items(self):
        """The ticked listings, with every field from the search result."""
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
        token = self.bot_token.value()
        chat_id = self.chat_id.text().strip()

        if not token or not chat_id:
            self.log(self.t("telegram_missing"))
            return

        self.telegram_sender.enqueue(token, chat_id, text)

    def send_telegram(self, item):
        prefix = self.message_prefix.text().strip() or self.t("default_prefix")

        lines = [
            prefix,
            "",
            f"{self.t('label_title')}: {item.get('title', '?')}",
            f"{self.t('label_price')}: {item.get('price', '?')}",
        ]
        if item.get("location"):
            lines.append(f"{self.t('label_location')}: {item['location']}")
        lines.append(f"{self.t('label_time')}: {item.get('time', '?')}")
        lines.append(f"{self.t('label_link')}: {item.get('url', '')}")

        self.queue_telegram("\n".join(lines))

    def closeEvent(self, event):
        self.timer.stop()
        self.marker_timer.stop()
        self.telegram_sender.stop()
        self.telegram_sender.wait(3000)
        self.image_loader.stop()
        self.image_loader.wait(3000)

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
