from PyQt5.QtWidgets import QApplication

def read_stylesheet():
    try:
        with open('styles.qss', 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return ''

def apply_global_styles():
    ss = read_stylesheet()
    if ss:
        QApplication.instance().setStyleSheet(ss)
