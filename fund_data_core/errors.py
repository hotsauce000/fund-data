# -*- coding: utf-8 -*-
"""共享请求协调器的异常类型。

RiskControlError 与 CircuitOpenError 保持 fund_data.py 的公开语义：
风控错误不触发降级，熔断错误表示上游调用已被保护性拒绝。
"""


class RiskControlError(RuntimeError):
    """上游返回风控信号，或协调器为保护上游而拒绝请求。"""


class CircuitOpenError(RuntimeError):
    """熔断器处于 OPEN/HALF_OPEN，当前请求不得访问网络。"""

    reason = "risk"

    def __init__(self, message, provider=None, retry_after=None):
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after


class BackpressureError(RiskControlError):
    """在允许的背压时间内无法取得共享配额。"""

    def __init__(self, message, provider=None, retry_after=None):
        super().__init__(message)
        self.provider = provider
        self.retry_after = retry_after


class UnknownHostError(RiskControlError, ValueError):
    """URL 不在显式 provider 白名单内。"""


class CoordinatorStateError(RiskControlError):
    """锁或持久化状态不可用；所有此类异常均 fail-closed。"""


class LeaseError(RuntimeError):
    """许可不存在、已过期、已取消或生命周期操作顺序错误。"""


class LockBackendError(RuntimeError):
    """无可用文件锁后端（flock 与字节范围锁机制均不可用）；fail-closed。"""


class LockTimeoutError(RuntimeError):
    """在 timeout 期限内未取得文件锁。

    仅对「有界后端」（acquire 自身会放弃）生效；无界后端（如 flock）
    阻塞在系统调用内，deadline 只在两次 acquire 之间检查。
    """

    def __init__(self, message, path=None, timeout=None):
        super().__init__(message)
        self.path = path
        self.timeout = timeout
