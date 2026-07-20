"""带 loading 转圈动画的按钮。

loading 启动后：
- 按钮左侧持续画一个旋转圆弧
- 按钮禁用，文字可替换为提示文案
- stop_loading 后恢复原文字和可用状态
"""
from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import QPainter, QColor, QPen
from PyQt5.QtWidgets import QPushButton


class LoadingButton(QPushButton):
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self._loading = False
        self._angle = 0
        self._original_text = text
        self._loading_text = text
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def start_loading(self, loading_text=None):
        self._loading = True
        self._loading_text = loading_text if loading_text is not None else self._original_text
        super().setText(self._loading_text)
        self.setEnabled(False)
        if not self._timer.isActive():
            self._timer.start()
        self.update()

    def stop_loading(self):
        self._loading = False
        super().setText(self._original_text)
        self.setEnabled(True)
        if self._timer.isActive():
            self._timer.stop()
        self.update()

    def setText(self, text):
        self._original_text = text
        if not self._loading:
            super().setText(text)

    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._loading:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = min(self.height() - 10, 18)
        if size < 8:
            return
        x = 8
        y = (self.height() - size) // 2
        rect = QRectF(x, y, size, size)
        track_pen = QPen(QColor(255, 255, 255, 80), max(2, size // 8))
        track_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(rect, 0, 360 * 16)
        arc_pen = QPen(QColor(255, 255, 255, 230), max(2, size // 8))
        arc_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, -int(self._angle * 16), 90 * 16)
        painter.end()
