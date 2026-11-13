# -*- coding: utf-8 -*-
"""fcntl 文件锁与原子 JSON 状态存储。

锁文件永不替换，JSON 文件只在持锁时通过同目录临时文件 + os.replace 替换。
任何读取、解析、校验、加锁或写入异常都转成 CoordinatorStateError。
"""

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from .errors import CoordinatorStateError

try:
    import fcntl
except ImportError:  # Windows 没有可依赖的跨进程 flock，不做内存降级。
    fcntl = None


STATE_VERSION = 1
STATE_FILENAME = "coordinator-state.json"


def initial_state():
    return {"version": STATE_VERSION, "providers": {}}


class JsonStateStore:
    """一个小而可注入的 JSON 事务接口。

    path 可以是 JSON 文件路径，也可以是一个目录。目录模式下使用
    coordinator-state.json；锁位于同目录的 coordinator-state.lock。
    """

    def __init__(self, path):
        supplied = Path(path)
        self.state_path = (supplied / STATE_FILENAME
                           if supplied.suffix.lower() != ".json" else supplied)
        self.lock_path = self.state_path.with_suffix(".lock")
        self._thread_lock = threading.RLock()

    def _validate(self, state):
        if (not isinstance(state, dict) or state.get("version") != STATE_VERSION
                or not isinstance(state.get("providers"), dict)):
            raise CoordinatorStateError(
                f"协调器状态格式不受支持: {self.state_path}；为保护上游已拒绝请求")
        for name, provider in state["providers"].items():
            if not isinstance(name, str) or not isinstance(provider, dict):
                raise CoordinatorStateError("协调器 provider 状态损坏；已 fail-closed")
            for key in ("reserved_slots", "network_started", "responses", "consecutive_risk"):
                value = provider.get(key)
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise CoordinatorStateError(f"协调器计数 {name}.{key} 损坏；已 fail-closed")
            for key in ("reservations",):
                if not isinstance(provider.get(key), list):
                    raise CoordinatorStateError(f"协调器 {name}.{key} 损坏；已 fail-closed")
            if not isinstance(provider.get("in_flight"), dict):
                raise CoordinatorStateError(f"协调器 {name}.in_flight 损坏；已 fail-closed")
            if provider.get("circuit") not in ("CLOSED", "OPEN", "HALF_OPEN"):
                raise CoordinatorStateError(f"协调器 {name}.circuit 损坏；已 fail-closed")
        return state

    def _read_unlocked(self):
        if not self.state_path.exists():
            return initial_state()
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except CoordinatorStateError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError) as exc:
            raise CoordinatorStateError(
                f"无法读取协调器状态 {self.state_path}: {exc}；已 fail-closed") from exc
        return self._validate(state)

    def _write_unlocked(self, state):
        self._validate(state)
        tmp_name = None
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=self.state_path.name + ".", suffix=".tmp",
                dir=str(self.state_path.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                os.chmod(tmp_name, 0o600)
                json.dump(state, handle, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.state_path)
            tmp_name = None
            try:
                dir_fd = os.open(str(self.state_path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                # 某些文件系统不支持目录 fsync；rename 本身已原子完成。
                pass
        except (OSError, ValueError, TypeError) as exc:
            raise CoordinatorStateError(
                f"无法写入协调器状态 {self.state_path}: {exc}；已 fail-closed") from exc
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    @contextmanager
    def locked(self):
        """独占跨进程锁；同进程 RLock 防止线程绕过 flock 语义。"""

        if fcntl is None:
            raise CoordinatorStateError("当前平台无 fcntl，无法保证跨进程安全；已 fail-closed")
        with self._thread_lock:
            handle = None
            locked = False
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                handle = self.lock_path.open("a+", encoding="utf-8")
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                locked = True
                yield
            except CoordinatorStateError:
                raise
            except (OSError, ValueError, TypeError) as exc:
                raise CoordinatorStateError(
                    f"无法取得协调器锁 {self.lock_path}: {exc}；已 fail-closed") from exc
            finally:
                if handle is not None:
                    try:
                        if locked:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError as exc:
                        if locked:
                            raise CoordinatorStateError(
                                f"释放协调器锁失败: {exc}；已 fail-closed") from exc
                    finally:
                        handle.close()

    @contextmanager
    def transaction(self):
        """读取并校验状态，正常退出事务时原子提交。"""

        with self.locked():
            state = self._read_unlocked()
            yield state
            self._write_unlocked(state)

    def read(self):
        with self.locked():
            return self._read_unlocked()

    def write(self, state):
        with self.locked():
            self._write_unlocked(state)
