from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QDoubleSpinBox,
    QSpinBox,
    QDialogButtonBox,
)

from core.categories import CategoryStore, all_categories_label
from core.monitor import MIN_INTERVAL_SECONDS
from core.translations import tr
from ui.theme import AppearanceDialog as ThemeAppearanceDialog


class SearchProfileDialog(QDialog):
    def __init__(self, parent, profile=None):
        super().__init__(parent)
        self.setWindowTitle(tr("profile_dialog_title"))
        self.resize(430, 470)
        p = profile or {}

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name = QLineEdit(p.get("name", ""))
        self.term = QLineEdit(p.get("term", ""))
        self.category_store = CategoryStore()
        self.category = QComboBox()
        self.category.addItems(self.category_store.names())
        self.category.setCurrentText(
            self.category_store.name_for_id(p.get("category_id")) or all_categories_label()
        )
        self.region = QLineEdit(p.get("region", ""))

        self.distance = QSpinBox()
        self.distance.setRange(0, 250)
        self.distance.setSingleStep(5)
        self.distance.setSuffix(" km")
        self.distance.setValue(int(p.get("distance", 0) or 0))

        self.max_price = QDoubleSpinBox()
        self.max_price.setMaximum(999999)
        self.max_price.setDecimals(2)
        self.max_price.setPrefix("€ ")
        self.max_price.setValue(float(p.get("max_price", 150)))

        self.interval = QSpinBox()
        self.interval.setRange(MIN_INTERVAL_SECONDS, 3600)
        self.interval.setSuffix(" sec")
        self.interval.setValue(int(p.get("interval", 60)))

        self.free_only = QCheckBox(tr("free_only_toggle"))
        self.free_only.setChecked(bool(p.get("free_only", False)))
        self.hide_promoted = QCheckBox(tr("hide_promoted_toggle"))
        self.hide_promoted.setChecked(bool(p.get("hide_promoted", True)))

        form.addRow(tr("profile_name"), self.name)
        form.addRow(tr("search_term"), self.term)
        form.addRow(tr("category"), self.category)
        form.addRow(tr("region"), self.region)
        form.addRow(tr("distance_short"), self.distance)
        form.addRow(tr("max_price"), self.max_price)
        form.addRow(tr("interval"), self.interval)
        form.addRow(self.free_only)
        form.addRow(self.hide_promoted)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_data(self):
        return {
            "name": self.name.text().strip() or tr("profile_default"),
            "term": self.term.text().strip(),
            "category_id": self.category_store.id_for_name(self.category.currentText()) or "",
            "region": self.region.text().strip(),
            "distance": self.distance.value(),
            "max_price": self.max_price.value(),
            "interval": self.interval.value(),
            "free_only": self.free_only.isChecked(),
            "hide_promoted": self.hide_promoted.isChecked(),
        }


class AppearanceDialog(ThemeAppearanceDialog):
    """Thin wrapper around ThemeAppearanceDialog so existing imports keep working."""

    def __init__(self, parent, settings):
        super().__init__(parent, settings)
