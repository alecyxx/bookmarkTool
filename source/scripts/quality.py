"""统一质量门禁（BM-V1-005）。

本地与 CI 共用一条命令：

    python -m scripts.quality

依次执行：静态检查(ruff) -> 格式检查(ruff format) -> 模板检查 -> 单元/集成测试与覆盖率。
任何一步失败都以非零状态退出。

可选参数：
    --no-cov    跳过覆盖率统计（快速验证）
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
STEPS: list[tuple[str, list[str]]] = [
    ("静态检查 ruff check", [sys.executable, "-m", "ruff", "check", "app", "tests", "scripts"]),
    (
        "格式检查 ruff format --check",
        [sys.executable, "-m", "ruff", "format", "--check", "app", "tests", "scripts"],
    ),
    ("模板检查 check_templates", [sys.executable, "-m", "scripts.check_templates"]),
]


def main() -> int:
    run_steps = list(STEPS)
    run_steps.append(
        (
            "测试 pytest（含覆盖率）",
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--cov=app",
                "--cov-report=term-missing",
                "--cov-report=xml:coverage.xml",
            ],
        )
    )
    if "--no-cov" in sys.argv:
        run_steps[-1] = ("测试 pytest", [sys.executable, "-m", "pytest", "-q"])

    failed = False
    for name, cmd in run_steps:
        print(f"\n===== {name} =====")
        result = subprocess.run(cmd, cwd=APP_ROOT)
        if result.returncode != 0:
            print(f"!!!!! 质量门禁失败：{name}")
            failed = True
            break
    if not failed:
        print("\n===== 质量门禁全部通过 =====")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
