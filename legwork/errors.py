"""首版流程可以预见的拒绝原因。"""


class PolicyError(RuntimeError):
    """按配置或状态拒绝执行；不会产生平台副作用。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SendFailure(RuntimeError):
    """发送前的页面核对失败：不点击、不重复投递。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class HostError(RuntimeError):
    """宿主模型任务失败：不继续决策与发送。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
