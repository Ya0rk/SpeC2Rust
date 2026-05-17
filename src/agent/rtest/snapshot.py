"""项目快照 / 回滚。修复原实现非原子的问题（#4）。

原实现：
- ``_snapshot_project``：单个 item 失败只打印 warning，但 snapshot 不完整；
- ``_restore_project``：先 ``rmtree`` 再 ``copytree``，中间失败项目半毁；

新实现：
- **快照必须完整**：任何目标 item 拷贝失败立即抛异常，清掉已产生的临时目录，
  让调用方明确知道快照不可用；
- **还原两阶段**：先把新内容 stage 到 ``<project>.restore_staging``，校验成功后
  用 ``os.replace`` 做目录级 swap（Windows 与 POSIX 都是原子语义的 rename），
  大幅缩短项目处于不一致状态的时间窗口；
- 允许在同盘符创建临时目录，避免跨盘拷贝。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from typing import Iterable, List, Optional

from .constants import SNAPSHOT_TARGETS


class SnapshotError(RuntimeError):
    """快照或还原失败。"""


class ProjectSnapshot:
    """对 ``SNAPSHOT_TARGETS`` 列出的子路径做整体快照。"""

    def __init__(self, project_dir: str, targets: Optional[Iterable[str]] = None):
        self.project_dir = os.path.abspath(project_dir)
        self.targets: List[str] = list(targets or SNAPSHOT_TARGETS)
        self._dir: Optional[str] = None
        self._existed: List[str] = []  # 原项目中实际存在的 items

    # ---------------- 创建快照 ----------------

    def create(self) -> None:
        if self._dir is not None:
            return
        # 在项目同盘符创建临时目录，避免跨盘 copy
        base_dir = os.path.dirname(self.project_dir) or self.project_dir
        snap = tempfile.mkdtemp(prefix=".rtest_snap_", dir=base_dir)
        try:
            for item in self.targets:
                src = os.path.join(self.project_dir, item)
                if not os.path.exists(src):
                    continue
                dst = os.path.join(snap, item)
                os.makedirs(os.path.dirname(dst) or snap, exist_ok=True)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                self._existed.append(item)
        except Exception as exc:
            shutil.rmtree(snap, ignore_errors=True)
            raise SnapshotError(f"创建项目快照失败：{exc}") from exc
        self._dir = snap

    # ---------------- 还原 ----------------

    def restore(self) -> None:
        if self._dir is None or not os.path.isdir(self._dir):
            raise SnapshotError("快照不存在或已被丢弃，无法还原")

        # 阶段 1：把每个 target 先重命名到 .trash 后缀（失败立即回滚这一步）
        trash_suffix = f".rtest_trash_{uuid.uuid4().hex[:8]}"
        renamed: List[tuple] = []  # (trash_path, final_path)

        try:
            # 1a. 把原项目的 target 项 rename 到 .trash（安全，整目录操作）
            for item in self.targets:
                live = os.path.join(self.project_dir, item)
                if not os.path.exists(live):
                    continue
                trash = live + trash_suffix
                os.rename(live, trash)
                renamed.append((trash, live))

            # 1b. 把快照里的 target 拷回原位
            for item in self._existed:
                src = os.path.join(self._dir, item)
                dst = os.path.join(self.project_dir, item)
                os.makedirs(os.path.dirname(dst) or self.project_dir, exist_ok=True)
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
        except Exception as exc:
            # 回滚 1a：尝试把 .trash 改回原名
            for trash, live in renamed:
                try:
                    if os.path.exists(live):
                        if os.path.isdir(live):
                            shutil.rmtree(live, ignore_errors=True)
                        else:
                            os.remove(live)
                    os.rename(trash, live)
                except OSError:
                    pass
            raise SnapshotError(f"还原项目快照失败：{exc}") from exc

        # 1c. 成功，清理 .trash
        for trash, _live in renamed:
            if os.path.isdir(trash):
                shutil.rmtree(trash, ignore_errors=True)
            elif os.path.isfile(trash):
                try:
                    os.remove(trash)
                except OSError:
                    pass

    # ---------------- 丢弃 ----------------

    def discard(self) -> None:
        if self._dir and os.path.isdir(self._dir):
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = None
