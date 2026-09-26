"""手册目录：从已核验索引的章节路径还原目录叶子与页段。

索引的 `section_path` 是**按页合并**的表示：同一物理页开启多个叶子时，末段写成
`「座椅 > 座椅通风 / 车辆安全 > 安全带」`——每个 ` / ` 之后的段替换前一条路径的同级层。
按这条规则还原出的叶子与 PDF 大纲逐条一致（2026-09-26 对账：187 = 187），所以在线
路由不需要 PDF，也不需要第二份目录声明：目录的权威就是获准索引本身。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class TocEntry:
    """一个目录叶子：稳定编号、完整路径、覆盖的物理页（首次出现页至下一叶子首次出现页）。"""

    entry_id: str
    path: tuple[str, ...]
    pages: tuple[int, ...]

    @property
    def label(self) -> str:
        return " > ".join(self.path)


def expand_section_path(section_path: Iterable[str]) -> list[tuple[str, ...]]:
    """`['座椅和安全', '座椅 > 座椅通风 / 车辆安全 > 安全带']`
    → `[('座椅和安全', '座椅', '座椅通风'), ('座椅和安全', '车辆安全', '安全带')]`。"""
    joined = " > ".join(str(part) for part in section_path)
    leaves: list[tuple[str, ...]] = []
    current: list[str] = []
    for index, segment in enumerate(joined.split(" / ")):
        parts = [part.strip() for part in segment.split(" > ") if part.strip()]
        if not parts:
            continue
        if index == 0 or not current:
            current = parts
        else:
            current = current[:max(0, len(current) - len(parts))] + parts
        leaves.append(tuple(current))
    return leaves


def build_table_of_contents(chunks: Iterable[Mapping[str, Any]]) -> list[TocEntry]:
    """按文档顺序列出叶子；一个叶子覆盖从首次出现页到下一个叶子首次出现页（含该页，
    因为下一个叶子可能从页中间开始）。"""
    ordered = sorted(chunks, key=lambda chunk: int(chunk["page_start"]))
    order: list[tuple[str, ...]] = []
    first_page: dict[tuple[str, ...], int] = {}
    last_page: dict[tuple[str, ...], int] = {}
    for chunk in ordered:
        for leaf in expand_section_path(chunk["section_path"]):
            if leaf not in first_page:
                first_page[leaf] = int(chunk["page_start"])
                order.append(leaf)
            last_page[leaf] = int(chunk["page_end"])
    indexed_pages = sorted({int(chunk["page_start"]) for chunk in ordered})
    entries: list[TocEntry] = []
    for position, leaf in enumerate(order):
        start = first_page[leaf]
        end = last_page[leaf]
        if position + 1 < len(order):
            end = max(end, first_page[order[position + 1]])
        entries.append(TocEntry(
            entry_id=f"T{position + 1:03d}",
            path=leaf,
            pages=tuple(page for page in indexed_pages if start <= page <= end),
        ))
    return entries
