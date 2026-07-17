"""非模态 Toast 通知：用于创建分支成功后提示是否切换。"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget,
)


class CheckoutToast(QFrame):
    """右上角悬浮通知，5 秒自动隐藏。

    点 [切换] 触发 on_checkout 并立即 hide；
    点 [×] 或超时只 hide。
    """

    DURATION_MS = 5000

    def __init__(self,
                 parent_window: QWidget,
                 on_checkout: Callable[[], None]) -> None:
        super().__init__(parent_window)
        self._on_checkout = on_checkout
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        self._build_ui()
        self._position_at_top_right(parent_window)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setStyleSheet('color: #222; font-size: 13px;')
        layout.addWidget(self._label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._checkout_btn = QPushButton('切换')
        self._checkout_btn.clicked.connect(self._handle_checkout)
        self._close_btn = QPushButton('×')
        self._close_btn.setFixedWidth(28)
        self._close_btn.clicked.connect(self.hide)
        btn_row.addWidget(self._checkout_btn)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

        self.setStyleSheet(
            'CheckoutToast {'
            '  background-color: #fff8dc;'
            '  border: 1px solid #d4b870;'
            '  border-radius: 6px;'
            '}'
        )
        self.setFixedWidth(360)

    def _position_at_top_right(self, window: QWidget) -> None:
        geo = window.geometry()
        screen_geo = QApplication.desktop().availableGeometry(window)
        # 相对屏幕，不相对父窗口（因为用了 Qt.Tool）
        x = screen_geo.x() + screen_geo.width() - self.width() - 24
        y = screen_geo.y() + 24
        self.move(x, y)

    def show_message(self, text: str, branch: Optional[str] = None) -> None:
        """显示通知。branch=None 时隐藏切换按钮（仅信息提示）。"""
        self._label.setText(text)
        self._checkout_btn.setVisible(branch is not None)
        self._branch = branch
        self.adjustSize()
        # 重新定位（宽度可能变化）
        parent = self.parentWidget()
        if parent is not None:
            self._position_at_top_right(parent)
        self.show()
        self._timer.start(self.DURATION_MS)

    def _handle_checkout(self) -> None:
        self._timer.stop()
        self.hide()
        try:
            self._on_checkout()
        except Exception:
            pass
