from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

from coverage import Coverage


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class CoverageTarget:
    name: str
    relative_path: str
    functions: tuple[str, ...] | None = None


TARGETS = (
    CoverageTarget("下载输入识别与 torrent 解析", "backend/app/engines/inputs.py"),
    CoverageTarget("aria2 下载适配器", "backend/app/engines/aria2.py"),
    CoverageTarget("qBittorrent Web API 适配器", "backend/app/engines/qbittorrent.py"),
    CoverageTarget("下载引擎任务执行链", "backend/app/services/engine_tasks.py"),
    CoverageTarget("SQLite 初始化与连接恢复", "backend/app/core/storage.py"),
    CoverageTarget("任务事件总线", "backend/app/core/events.py"),
    CoverageTarget(
        "文件安全、维护和个人媒体操作",
        "backend/app/server.py",
        (
            "validate_url",
            "resolve_download_dir",
            "ensure_disk_capacity",
            "validate_directory_pattern",
            "normalize_library_dirs",
            "safe_directory_value",
            "formatted_download_dir",
            "send_system_notification",
            "related_video_files",
            "incomplete_task_files",
            "path_tree_size",
            "incomplete_residue_preview",
            "cleanup_incomplete_residues",
            "maintenance_cleanup_preview",
            "run_maintenance_cleanup",
            "direct_download_file_name",
            "version_parts",
            "check_ytdlp_update",
            "safe_media_title",
            "replace_output_path",
            "rename_video",
        ),
    ),
)


def statement_lines(coverage: Coverage, source_path: Path) -> tuple[set[int], set[int]]:
    _filename, statements, _excluded, missing, _formatted = coverage.analysis2(str(source_path))
    return set(statements), set(missing)


def function_ranges(source_path: Path, function_names: tuple[str, ...]) -> set[int]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    nodes = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing_names = sorted(set(function_names) - nodes.keys())
    if missing_names:
        raise RuntimeError(f"关键函数不存在：{', '.join(missing_names)}")
    lines: set[int] = set()
    for name in function_names:
        node = nodes[name]
        lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="检查明确列出的关键代码行覆盖率。")
    parser.add_argument("--data-file", default=str(PROJECT_ROOT / ".coverage"))
    parser.add_argument("--fail-under", type=float, default=98.0)
    args = parser.parse_args()

    coverage = Coverage(data_file=args.data_file)
    coverage.load()
    failed = False
    total_statements = 0
    total_covered = 0

    print("关键代码覆盖率：")
    for target in TARGETS:
        source_path = PROJECT_ROOT / target.relative_path
        statements, missing = statement_lines(coverage, source_path)
        if target.functions is not None:
            selected_lines = function_ranges(source_path, target.functions)
            statements &= selected_lines
            missing &= selected_lines
        covered = len(statements - missing)
        percent = covered * 100 / len(statements) if statements else 100.0
        total_statements += len(statements)
        total_covered += covered
        status = "PASS" if percent >= args.fail_under else "FAIL"
        print(f"  {status}  {target.name}: {covered}/{len(statements)} ({percent:.2f}%)")
        if missing:
            print(f"        未覆盖行：{', '.join(str(line) for line in sorted(missing))}")
        failed = failed or percent < args.fail_under

    total_percent = total_covered * 100 / total_statements if total_statements else 100.0
    status = "PASS" if total_percent >= args.fail_under else "FAIL"
    print(f"  {status}  关键代码合计: {total_covered}/{total_statements} ({total_percent:.2f}%)")
    failed = failed or total_percent < args.fail_under
    if failed:
        print(f"关键代码覆盖率门禁未达到 {args.fail_under:.2f}%。")
        return 1
    print(f"关键代码覆盖率门禁已达到 {args.fail_under:.2f}%。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
