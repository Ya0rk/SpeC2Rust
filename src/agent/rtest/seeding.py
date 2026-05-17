"""首轮根据 flag / 关键字主动注入 C 源码片段 + Rust 文件。

不针对任何具体项目做特殊化，全部按打分排序。
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

from .source_loader import CSourceIndex


def seed_c_sources(
    flags: Sequence[str],
    index: CSourceIndex,
    keywords: Optional[Sequence[str]] = None,
    limit: int = 4,
) -> List[Dict]:
    keywords = list(keywords or [])
    flags = list(flags or [])
    if not index or (not flags and not keywords):
        return []

    scored: List[Tuple[int, Dict]] = []
    for rec in index.records:
        src = rec.get("source", "") or ""
        if not src:
            continue
        score = _score_c_record(src, rec, flags, keywords)
        if score > 0:
            scored.append((score, rec))
    scored.sort(key=lambda item: -item[0])
    seen_ids: set = set()
    out: List[Dict] = []
    for _, rec in scored:
        if id(rec) in seen_ids:
            continue
        seen_ids.add(id(rec))
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def _score_c_record(
    src: str, rec: Dict, flags: Sequence[str], keywords: Sequence[str]
) -> int:
    score = 0
    rec_name = str(rec.get("name", "")).lower()
    rec_file = str(rec.get("file", "")).replace("\\", "/").lower()
    for flag in flags:
        if f'"{flag}"' in src or f"'{flag}'" in src:
            score += 6
        if len(flag) == 2 and flag.startswith("-"):
            ch = flag[1]
            if f"case '{ch}'" in src or f"'{ch}':" in src:
                score += 4
        if flag.startswith("--"):
            long_stem = flag[2:]
            if f'"{long_stem}"' in src:
                score += 4
            snake = long_stem.replace("-", "_")
            if snake and re.search(rf"\b{re.escape(snake)}\b", src):
                score += 2
    for kw in keywords:
        if not kw:
            continue
        if kw == rec_name:
            score += 5
        elif kw in rec_name:
            score += 3
        elif kw in rec_file:
            score += 2
        elif re.search(rf"\b{re.escape(kw)}\b", src, re.IGNORECASE):
            score += 1
    return score


def seed_rust_files(
    flags: Sequence[str],
    project_dir: str,
    keywords: Optional[Sequence[str]] = None,
    limit: int = 3,
) -> Dict[str, str]:
    keywords = list(keywords or [])
    flags = list(flags or [])
    if not flags and not keywords:
        return {}
    src_dir = os.path.join(project_dir, "src")
    if not os.path.isdir(src_dir):
        return {}

    scored: List[Tuple[int, str, str]] = []
    for root, _, files in os.walk(src_dir):
        for name in files:
            if not name.endswith(".rs"):
                continue
            full = os.path.join(root, name)
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except OSError:
                continue
            score = _score_rust_file(text, name, flags, keywords)
            if score > 0:
                rel = os.path.relpath(full, project_dir).replace("\\", "/")
                scored.append((score, rel, text))
    scored.sort(key=lambda item: -item[0])
    return {rel: text for _, rel, text in scored[:limit]}


def _score_rust_file(
    text: str, name: str, flags: Sequence[str], keywords: Sequence[str]
) -> int:
    score = 0
    file_stem = name[:-3].lower()
    for flag in flags:
        if f'"{flag}"' in text:
            score += 6
        if flag.startswith("--"):
            long_stem = flag[2:]
            if f'"{long_stem}"' in text:
                score += 4
            snake = long_stem.replace("-", "_")
            if snake and re.search(rf"\b{re.escape(snake)}\b", text):
                score += 2
        if len(flag) == 2 and flag.startswith("-"):
            ch = flag[1]
            if f"'{ch}'" in text:
                score += 2
    for kw in keywords:
        if not kw:
            continue
        if kw == file_stem:
            score += 5
        elif kw in file_stem:
            score += 3
        elif re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE):
            score += 1
    return score
