# -*- coding: utf-8 -*-
"""平台硬编码守卫：生产代码里不允许出现操作系统分支。

规则：平台差异只允许存在于 fund_data_core/locks.py（后端选择=能力检测）。
其余源文件不得引用 sys.platform / os.name / platform 模块，也不得在代码
（非注释/文档字符串）中出现 "win32"/"darwin"/"windows"/"macos" 字符串
字面量。按 ast 扫描，注释与文档字符串天然豁免。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PRODUCTION_FILES = [REPO_ROOT / "fund_data.py"] + sorted(
    (REPO_ROOT / "fund_data_core").glob("*.py"))
EXEMPT = {"locks.py"}                       # 平台差异的唯一定居点
FORBIDDEN_MODULES = {"platform"}
FORBIDDEN_CONSTANTS = {"win32", "darwin", "windows", "macos", "linux"}
# 常量按「整串相等」比对：docstring/注释天然豁免（ast 只看代码节点）；
# 平台名出现在代码字符串里（哪怕只作展示）也应改走文档，故一并禁。


class NoPlatformHardcodingTests(unittest.TestCase):
    def test_production_code_has_no_os_branches(self):
        violations = []
        for path in PRODUCTION_FILES:
            if path.name in EXEMPT:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for found in ast.walk(tree):
                if isinstance(found, ast.Import):
                    for alias in found.names:
                        if alias.name.split(".")[0] in FORBIDDEN_MODULES:
                            violations.append(f"{path.name}: import {alias.name}")
                elif isinstance(found, ast.ImportFrom):
                    root = (found.module or "").split(".")[0]
                    if root in FORBIDDEN_MODULES:
                        violations.append(f"{path.name}: from {found.module} import")
                elif isinstance(found, ast.Attribute):
                    if found.attr in ("platform", "name") and isinstance(
                            found.value, ast.Name) and found.value.id in ("sys", "os"):
                        violations.append(f"{path.name}: {found.value.id}.{found.attr}")
                elif isinstance(found, ast.Constant) and isinstance(found.value, str):
                    if found.value.lower() in FORBIDDEN_CONSTANTS:
                        violations.append(
                            f"{path.name}: 字符串字面量 {found.value!r}")
        self.assertEqual(violations, [],
                         "平台差异必须收敛到 locks.py（能力检测），发现:\n"
                         + "\n".join(violations))

    def test_locks_module_declares_both_mechanisms(self):
        """locks.py 是豁免点，也必须真的只做能力检测（两个后端都定义）。"""
        import fund_data_core.locks as locks
        self.assertTrue(hasattr(locks, "FlockBackend"))
        self.assertTrue(hasattr(locks, "ByteRangeBackend"))
        self.assertNotIn("sys.platform", Path(locks.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
