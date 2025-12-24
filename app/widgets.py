from PyQt5.QtWidgets import QComboBox, QCompleter
from PyQt5.QtCore import Qt

class NoWheelComboBox(QComboBox):
    def wheelEvent(self, event):
        event.ignore()

def enable_combo_search(combo):
    combo.setEditable(True)
    combo.setInsertPolicy(QComboBox.NoInsert)
    completer = QCompleter(combo.model())
    completer.setCaseSensitivity(Qt.CaseInsensitive)
    if hasattr(completer, 'setFilterMode'):
        completer.setFilterMode(Qt.MatchContains)
    combo.setCompleter(completer)
