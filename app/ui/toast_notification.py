"""非模态 Toast 通知：用于创建分支成功后提示是否切换。"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
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
        self._apply_shadow()
        self._position_at_top_right(parent_window)

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 左侧成功色条
        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet('background-color: #10b981; border: none;')
        outer.addWidget(accent)

        body = QFrame()
        body.setStyleSheet('QFrame { background-color: #ffffff; border: none; }')
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 12, 12, 12)
        body_layout.setSpacing(6)

        # 顶部行：图标 + 文案 + 关闭按钮
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self._icon_label = QLabel('✓')
        self._icon_label.setFixedSize(20, 20)
        self._icon_label.setAlignment(Qt.AlignCenter)
        self._icon_label.setStyleSheet(
            'background-color: #10b981;'
            'color: #ffffff;'
            'border-radius: 10px;'
            'font-weight: bold;'
            'font-size: 12px;'
            'border: none;'
        )
        top_row.addWidget(self._icon_label, alignment=Qt.AlignTop)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            'color: #1f2937; font-size: 13px; background: transparent; border: none;'
        )
        self._label.setTextFormat(Qt.RichText)
        top_row.addWidget(self._label, stretch=1)

        self._close_btn = QPushButton('×')
        self._close_btn.setFixedSize(22, 22)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setToolTip('关闭')
        self._close_btn.setStyleSheet(
            'QPushButton {'
            '  background: transparent;'
            '  border: none;'
            '  color: #9ca3af;'
            '  font-size: 18px;'
            '  padding: 0;'
            '}'
            'QPushButton:hover { color: #4b5563; }'
        )
        self._close_btn.clicked.connect(self.hide)
        top_row.addWidget(self._close_btn, alignment=Qt.AlignTop)

        body_layout.addLayout(top_row)

        # 底部按钮行
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(28, 0, 0, 0)
        btn_row.addStretch()

        self._checkout_btn = QPushButton('切换到该分支')
        self._checkout_btn.setCursor(Qt.PointingHandCursor)
        self._checkout_btn.setStyleSheet(
            'QPushButton {'
            '  background-color: #3498db;'
            '  color: #ffffff;'
            '  border: none;'
            '  border-radius: 4px;'
            '  padding: 6px 16px;'
            '  font-size: 12px;'
            '  font-weight: 600;'
            '}'
            'QPushButton:hover { background-color: #2980b9; }'
            'QPushButton:pressed { background-color: #21618c; }'
        )
        self._checkout_btn.clicked.connect(self._handle_checkout)
        btn_row.addWidget(self._checkout_btn)
        body_layout.addLayout(btn_row)

        outer.addWidget(body, stretch=1)

        self.setStyleSheet(
            'CheckoutToast {'
            '  background-color: #ffffff;'
            '  border: 1px solid #e5e7eb;'
            '  border-radius: 8px;'
            '}'
        )
        self.setFixedWidth(360)

    def _apply_shadow(self) -> None:
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 40))
        shadow.setOffset(0, 4)
        # 对最外层 QFrame 应用阴影；子控件自带背景需 transparent 才不挡
        self.setGraphicsEffect(shadow)

    def _position_at_top_right(self, window: QWidget) -> None:
        screen_geo = QApplication.desktop().availableGeometry(window)
        # 相对屏幕，不相对父窗口（因为用了 Qt.Tool）
        x = screen_geo.x() + screen_geo.width() - self.width() - 24
        y = screen_geo.y() + 24
        self.move(x, y)

    def show_message(self, text: str, branch: Optional[str] = None) -> None:
        """显示通知。branch=None 时隐藏切换按钮（仅信息提示）。"""
        self._label.setText(text)
        self._checkout_btn.setVisible(branch is not None)
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
