# -*- coding: utf-8 -*-
"""fund-data 跨进程共享请求协调核心（Gateway/Coordinator/Cache 一套部署）。"""

from .cache import (CacheError, CacheRepository, CacheResult,
                    CacheRetryAfterError, EmptyLoaderResult, StaleDataError)
from .cache_policies import CachePolicy
from .coordinator import BACKPRESSURE_SECONDS, Coordinator, RequestPermit
from .errors import (BackpressureError, CircuitOpenError, CoordinatorStateError,
                     LeaseError, LockBackendError, LockTimeoutError,
                     RiskControlError, UnknownHostError)
from .gateway import CurlHTTPError, CurlResponse, Gateway
from .locks import (ByteRangeBackend, FlockBackend, LockBackend,
                    drop_extended_prefix, file_lock, resolve_backend)
from .policies import (DEFAULT_POLICIES, ProviderPolicy, normalize_provider,
                       provider_for_host, provider_for_url, resolve_provider)
from .state import JsonStateStore

__all__ = [
    "BACKPRESSURE_SECONDS", "BackpressureError", "CacheError", "CachePolicy",
    "CacheRepository", "CacheResult", "CacheRetryAfterError", "CircuitOpenError",
    "Coordinator", "CoordinatorStateError", "CurlHTTPError", "CurlResponse",
    "DEFAULT_POLICIES", "EmptyLoaderResult", "FlockBackend", "Gateway",
    "JsonStateStore", "LeaseError", "LockBackend", "LockBackendError",
    "LockTimeoutError", "ByteRangeBackend", "ProviderPolicy", "RequestPermit",
    "RiskControlError", "StaleDataError", "UnknownHostError",
    "drop_extended_prefix", "file_lock", "normalize_provider",
    "provider_for_host", "provider_for_url", "resolve_backend", "resolve_provider",
]
