"""
异步工具模块 - 使用 QThreadPool + QRunnable 实现后台任务
"""
from typing import Any, Callable, Optional, TypeVar
from PyQt5.QtCore import QObject, pyqtSignal, QRunnable, QThreadPool


T = TypeVar('T')


class BlockingWorker(QRunnable):
    """
    阻塞函数工作器 - 在后台线程池中运行阻塞函数
    """

    class Signals(QObject):
        finished = pyqtSignal(object)
        error = pyqtSignal(Exception)

    def __init__(self, func: Callable, args: tuple):
        super().__init__()
        self._func = func
        self._args = args
        self.signals = BlockingWorker.Signals()

    def run(self):
        """运行阻塞函数"""
        try:
            result = self._func(*self._args)
            self.signals.finished.emit(result)
        except Exception as e:
            self.signals.error.emit(e)


def run_blocking(func: Callable[..., T],
                 on_success: Optional[Callable[[T], None]] = None,
                 on_error: Optional[Callable[[Exception], None]] = None,
                 parent: Optional[QObject] = None,
                 *args) -> QThreadPool:
    """
    在后台线程池中执行阻塞函数

    Args:
        func: 要执行的阻塞函数
        on_success: 成功回调，接收函数返回值
        on_error: 错误回调，接收异常对象
        parent: 父对象
        *args: 函数参数

    Returns:
        使用的 QThreadPool 实例

    Example:
        def blocking_io():
            time.sleep(1)
            return "done"

        run_blocking(blocking_io,
                    on_success=lambda r: print(r),
                    parent=self)
    """
    worker = BlockingWorker(func, args)

    if on_success:
        worker.signals.finished.connect(on_success)
    if on_error:
        worker.signals.error.connect(on_error)

    QThreadPool.globalInstance().start(worker)
    return QThreadPool.globalInstance()
