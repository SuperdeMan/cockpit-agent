"""Strict schema and inventory checks for end-to-end test declarations."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = REPO_ROOT / "test" / "e2e_manifest.yaml"

_TOP_LEVEL_KEYS = frozenset({
    "version",
    "planned_paths",
    "privacy",
    "canonical_inputs",
    "runner_dependencies",
    "non_secret_config_keys",
    "cases",
})
_CASE_KEYS = frozenset({
    "id",
    "path",
    "command",
    "group",
    "lanes",
    "timeout_s",
    "profile",
    "skip_reasons",
    "signed_identity",
    "persistent_data",
    "memory_sessions",
    "nightly",
    "fixture_pre_step",
})
_REQUIRED_CASE_KEYS = _CASE_KEYS - {"nightly", "fixture_pre_step"}
_GROUPS = frozenset({
    "default",
    "security",
    "provider_probe",
    "acoustic_probe",
    "manual_inspection",
})
_LANES = frozenset({"ci", "nightly", "milestone"})
_PROFILES = frozenset({"root", "auth", "mtls", "real", "acoustic"})
_SKIP_POLICIES = frozenset({
    ("forbid",),
    ("credential_unavailable", "provider_unavailable"),
    (
        "credential_unavailable",
        "provider_unavailable",
        "profile_unavailable",
    ),
    (
        "credential_unavailable",
        "provider_unavailable",
        "profile_unavailable",
        "data_unavailable",
    ),
    ("hardware_unavailable",),
    (
        "hardware_unavailable",
        "provider_unavailable",
        "data_unavailable",
    ),
    ("manual_review_required",),
})
_PLANNED_PATH_ALLOWLIST = frozenset({"test/e2e_protocol_smoke.py"})
_CASE_ID_RE = re.compile(r"e2e_[a-z0-9_]+\Z")
_STABLE_ID_RE = re.compile(r"[a-z][a-z0-9_]*\Z")
_PRIVACY_KEYS = frozenset({
    "owner_columns",
    "personal_content_columns",
    "sql_sources",
    "registry_symbol",
    "targets",
})
_PRIVACY_TARGET_KEYS = frozenset({
    "id",
    "backend",
    "adapter_key",
    "adapter",
    "storage_variants",
    "lifecycle",
    "enforced_from",
    "owner_fields",
    "seed_case",
    "count_probe",
    "read_probe",
    "verify_case",
    "delete_action",
    "retention_reason",
    "retain_or_redact_action",
    "policy_flags",
})
_PRIVACY_TARGET_COMMON_KEYS = frozenset({
    "id",
    "backend",
    "adapter_key",
    "adapter",
    "storage_variants",
    "lifecycle",
    "enforced_from",
    "owner_fields",
    "seed_case",
    "count_probe",
    "read_probe",
    "verify_case",
})
_PRIVACY_PROBE_FIELDS = (
    "seed_case",
    "count_probe",
    "read_probe",
    "verify_case",
)
_LIFECYCLES = frozenset({
    "deletable",
    "retained_audit",
    "external_reference",
})
_MILESTONE_ORDER = {
    "M-A": 0,
    "M-B": 1,
    "M-C": 2,
    "M-D": 3,
}
_MILESTONE_CASE_PREFIX = {
    "M-A": "gdpr_ma_",
    "M-B": "gdpr_mb_",
    "M-C": "gdpr_mc_",
    "M-D": "gdpr_md_",
}
_OWNER_COLUMNS = ("user_id", "occupant_id")
_PERSONAL_CONTENT_COLUMNS = (
    "user_text",
    "speech",
    "prompt_tail",
    "content_head",
    "msg",
    "attrs",
    "note",
    "error",
)
_SQL_SOURCES = (
    "**/*.sql",
    "**/migrations/**/*.py",
    "**/pg_store.py",
    "**/db.py",
    "**/store.py",
)
_REGISTRY_SYMBOL = "PERSONAL_DATA_TARGETS"
_PRIVACY_EXCLUDED_DIRS = frozenset({
    ".git",
    ".worktrees",
    "gen",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
})
_SQL_IDENTIFIER = (
    r'(?:[A-Za-z_][A-Za-z0-9_$]*|"(?:[^"]|"")*"|'
    r"`(?:[^`]|``)*`|\[(?:[^\]]|\]\])*\])"
)
_SQL_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    rf"(?P<table>{_SQL_IDENTIFIER}(?:\s*\.\s*{_SQL_IDENTIFIER})*)"
    r"\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_SQL_IDENTIFIER_PART_RE = re.compile(_SQL_IDENTIFIER)
_SQL_COLUMN_RE = re.compile(
    rf"(?:^|,)\s*(?P<column>{_SQL_IDENTIFIER})\s+[A-Za-z]",
    re.MULTILINE,
)
_CONFIG_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SENSITIVE_CONFIG_TERMS = frozenset({
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "API_KEY",
    "ACCESS_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "AUTHORIZATION",
    "COOKIE",
})
MAX_CANONICAL_INPUT_BYTES = 64 * 1024 * 1024
MAX_CANONICAL_TOTAL_BYTES = 512 * 1024 * 1024
MAX_CANONICAL_REPORT_BYTES = 16 * 1024 * 1024
MAX_REPORT_JOURNAL_BYTES = 64 * 1024
_CANONICAL_REPORT_PATHS = frozenset({
    "docs/reviews/eval/journeys_report.json",
    "docs/reviews/eval/journeys_report.md",
})
_REQUIRED_DIGESTS = frozenset({
    "journey_corpus",
    "e2e_manifest",
    "runner",
    "tracked_inputs",
    "non_secret_config",
})


def _is_sensitive_config_key(key: str) -> bool:
    normalized = f"_{key.upper().strip('_')}_"
    return any(f"_{term}_" in normalized for term in _SENSITIVE_CONFIG_TERMS)


def _reject_duplicate_json_keys(
    pairs: Sequence[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError("JSON object contains duplicate keys")
        result[key] = value
    return result


def strict_json_loads(value: str | bytes) -> Any:
    return json.loads(value, object_pairs_hook=_reject_duplicate_json_keys)


def _read_text_bounded(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError as exc:
        raise ManifestError("bounded text input cannot be read") from exc
    if len(raw) > limit:
        raise ManifestError("bounded text input exceeds size limit")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise ManifestError("bounded text input is not UTF-8") from exc


class ManifestError(ValueError):
    """The manifest is malformed or does not match the e2e file inventory."""


class ArchitectureGuardError(ValueError):
    """Architecture vocabulary or guarded source cannot be proven safe."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        if not isinstance(key_node, yaml.ScalarNode):
            raise ManifestError("YAML mapping keys must be strings")
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ManifestError("YAML mapping keys must be strings")
        if key in mapping:
            raise ManifestError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def strict_yaml_load(content: str, *, where: str) -> Any:
    try:
        return yaml.load(content, Loader=_UniqueKeyLoader)
    except ManifestError as exc:
        raise ManifestError(f"cannot parse YAML {where}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"cannot parse YAML {where}") from exc


@dataclass(frozen=True)
class NightlySelection:
    """A mock-safe nightly invocation and its exact capability subset."""

    all: bool
    args: tuple[str, ...]
    memory_sessions: int | None = None


@dataclass(frozen=True)
class E2ECase:
    id: str
    path: str
    command: tuple[str, ...]
    group: str
    lanes: tuple[str, ...]
    timeout_s: int
    profile: str
    skip_reasons: tuple[str, ...]
    signed_identity: bool
    persistent_data: bool
    memory_sessions: int
    nightly: NightlySelection | None
    fixture_pre_step: str | None = None


@dataclass(frozen=True)
class PrivacyTarget:
    id: str
    backend: str
    adapter_key: str
    adapter: str
    storage_variants: tuple[str, ...]
    lifecycle: str
    enforced_from: str
    owner_fields: tuple[str, ...]
    seed_case: str
    count_probe: str
    read_probe: str
    verify_case: str
    delete_action: str
    retention_reason: str
    retain_or_redact_action: str
    policy_flags: tuple[str, ...]


@dataclass(frozen=True)
class PrivacyConfig:
    owner_columns: tuple[str, ...]
    personal_content_columns: tuple[str, ...]
    sql_sources: tuple[str, ...]
    registry_symbol: str
    targets: tuple[PrivacyTarget, ...]

    @property
    def by_id(self) -> dict[str, PrivacyTarget]:
        return {target.id: target for target in self.targets}


@dataclass(frozen=True)
class PrivacyCandidate:
    id: str
    storage_variants: tuple[str, ...]
    sql_variants: tuple[str, ...]
    variant_constants: tuple[tuple[str, str], ...]
    source: str


@dataclass(frozen=True)
class E2EManifest:
    version: int
    planned_paths: tuple[str, ...]
    privacy: PrivacyConfig
    canonical_inputs: tuple[str, ...]
    runner_dependencies: tuple[str, ...]
    non_secret_config_keys: tuple[str, ...]
    cases: tuple[E2ECase, ...]

    @property
    def by_id(self) -> dict[str, E2ECase]:
        return {case.id: case for case in self.cases}


@dataclass(frozen=True)
class CanonicalSnapshot:
    """Recomputable repository state used by report eligibility and freshness."""

    digests: Mapping[str, str]
    tracked_input_count: int
    dirty_paths: tuple[str, ...]
    untracked_input_paths: tuple[str, ...]
    runner_paths: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(self.dirty_paths or self.untracked_input_paths)

    def to_dict(self) -> dict[str, Any]:
        return {
            "digests": dict(self.digests),
            "tracked_input_count": self.tracked_input_count,
            "canonical_input_state": {
                "dirty": self.dirty,
                "dirty_paths": list(self.dirty_paths),
                "untracked_input_paths": list(self.untracked_input_paths),
            },
            "runner_paths": list(self.runner_paths),
        }


@dataclass(frozen=True)
class CanonicalFreshness:
    stale: bool
    reasons: tuple[str, ...]
    runtime_freshness: str
    report_code_sha: str = ""
    current_code_sha: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stale": self.stale,
            "reasons": list(self.reasons),
            "runtime_freshness": self.runtime_freshness,
            "report_code_sha": self.report_code_sha,
            "current_code_sha": self.current_code_sha,
        }


@dataclass(frozen=True)
class ArchitectureVocabulary:
    """Business words discovered from declarations and proactive producers."""

    domain_terms: frozenset[str]
    proactive_types: frozenset[str]
    identifier_terms: frozenset[str]
    ambiguous_identifier_terms: frozenset[str]

    @property
    def all_terms(self) -> frozenset[str]:
        return self.domain_terms | self.proactive_types


@dataclass(frozen=True)
class ArchitectureViolation:
    """One executable use of declaration-owned business vocabulary."""

    path: str
    line: int
    column: int
    term: str
    node_kind: str
    function: str

    def diagnostic(self) -> str:
        scope = f" in {self.function}" if self.function else ""
        return (
            f"{self.path}:{self.line}:{self.column}: executable business term "
            f"{self.term!r} in {self.node_kind}{scope}"
        )


def _resolve_path(path: Path) -> Path:
    return path.resolve(strict=True)


def _resolved_repo_and_test_roots(
    repo_root: Path | str,
) -> tuple[Path, Path | None]:
    try:
        root = _resolve_path(Path(repo_root))
    except (OSError, RuntimeError) as exc:
        raise ManifestError(f"repository root does not exist: {repo_root}") from exc
    if not root.is_dir():
        raise ManifestError(f"repository root is not a directory: {root}")

    test_candidate = root / "test"
    if not test_candidate.exists() and not test_candidate.is_symlink():
        return root, None
    try:
        test_root = _resolve_path(test_candidate)
        test_root.relative_to(root)
    except ValueError as exc:
        raise ManifestError(
            f"resolved test root escapes repository root: {test_root}",
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise ManifestError(
            f"test root does not resolve: {test_candidate}",
        ) from exc
    if not test_root.is_dir():
        raise ManifestError(f"test root is not a directory: {test_root}")
    return root, test_root


def _resolve_test_file(
    candidate: Path,
    *,
    test_root: Path,
    where: str,
) -> Path:
    try:
        resolved = _resolve_path(candidate)
    except (OSError, RuntimeError) as exc:
        raise ManifestError(f"{where} does not exist: {candidate}") from exc
    try:
        resolved.relative_to(test_root)
    except ValueError as exc:
        raise ManifestError(
            f"{where} escapes the resolved test root: {resolved}",
        ) from exc
    if not resolved.is_file():
        raise ManifestError(f"{where} is not a file: {resolved}")
    return resolved


def discover_e2e_files(repo_root: Path | str = REPO_ROOT) -> tuple[str, ...]:
    """Return every ``test/e2e_*.py`` file; there are no exclusions."""

    root, test_root = _resolved_repo_and_test_roots(repo_root)
    if test_root is None:
        return ()
    test_dir = root / "test"
    discovered: list[str] = []
    for path in sorted(test_dir.glob("e2e_*.py")):
        _resolve_test_file(
            path,
            test_root=test_root,
            where=f"discovered path {path}",
        )
        discovered.append(path.relative_to(root).as_posix())
    return tuple(discovered)


def _controlled_privacy_source_paths(
    root: Path,
    patterns: tuple[str, ...],
) -> tuple[Path, ...]:
    """Resolve, bound and deterministically order privacy inventory sources."""

    try:
        resolved_root = _resolve_path(root)
    except (OSError, RuntimeError) as exc:
        raise ManifestError(f"privacy repository root does not resolve: {root}") from exc
    paths: dict[str, Path] = {}
    for pattern in patterns:
        for path in resolved_root.glob(pattern):
            relative = path.relative_to(resolved_root)
            if any(
                part in _PRIVACY_EXCLUDED_DIRS
                for part in relative.parts[:-1]
            ):
                continue
            if path.is_dir():
                continue
            try:
                resolved = _resolve_path(path)
                bounded = resolved.relative_to(resolved_root)
            except ValueError as exc:
                raise ManifestError(
                    f"privacy source escapes repository root: {path}",
                ) from exc
            except (OSError, RuntimeError) as exc:
                raise ManifestError(
                    f"privacy source does not resolve: {path}",
                ) from exc
            if not resolved.is_file():
                continue
            paths[bounded.as_posix()] = resolved
    return tuple(paths[key] for key in sorted(paths))


def _privacy_source_paths(
    root: Path,
    patterns: tuple[str, ...],
) -> tuple[Path, ...]:
    return _controlled_privacy_source_paths(root, patterns)


def _normalize_sql_identifier(identifier: str) -> str:
    """Return the final component, folding only unquoted identifiers."""

    parts = _SQL_IDENTIFIER_PART_RE.findall(identifier)
    if not parts:
        raise ManifestError(f"cannot normalize SQL identifier: {identifier!r}")
    name = parts[-1].strip()
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('""', '"')
    if name.startswith("`") and name.endswith("`"):
        return name[1:-1].replace("``", "`")
    if name.startswith("[") and name.endswith("]"):
        return name[1:-1].replace("]]", "]")
    return name.lower()


def _python_string_literals_without_docstrings(
    text: str,
    *,
    path: Path,
) -> tuple[str, ...]:
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise ManifestError(
            f"cannot parse privacy SQL source {path}: {exc}",
        ) from exc
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return tuple(
        node.value
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        )
    )


def _strip_sql_comments_and_single_quoted_text(text: str) -> str:
    """Blank comments/string bodies while preserving SQL token boundaries."""

    output: list[str] = []
    index = 0
    state = "normal"
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "normal":
            if char == '"':
                output.append(char)
                index += 1
                state = "double_identifier"
                continue
            if char == "`":
                output.append(char)
                index += 1
                state = "backtick_identifier"
                continue
            if char == "[":
                output.append(char)
                index += 1
                state = "bracket_identifier"
                continue
            if char == "-" and following == "-":
                output.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if char == "/" and following == "*":
                output.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            if char == "'":
                output.append(" ")
                index += 1
                state = "single_quote"
                continue
            output.append(char)
            index += 1
            continue
        if state == "line_comment":
            output.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\n":
                state = "normal"
            continue
        if state == "double_identifier":
            output.append(char)
            index += 1
            if char == '"' and following == '"':
                output.append(following)
                index += 1
            elif char == '"':
                state = "normal"
            continue
        if state == "backtick_identifier":
            output.append(char)
            index += 1
            if char == "`" and following == "`":
                output.append(following)
                index += 1
            elif char == "`":
                state = "normal"
            continue
        if state == "bracket_identifier":
            output.append(char)
            index += 1
            if char == "]" and following == "]":
                output.append(following)
                index += 1
            elif char == "]":
                state = "normal"
            continue
        if state == "block_comment":
            if char == "*" and following == "/":
                output.extend((" ", " "))
                index += 2
                state = "normal"
                continue
            output.append("\n" if char == "\n" else " ")
            index += 1
            continue
        if char == "'" and following == "'":
            output.extend((" ", " "))
            index += 2
            continue
        output.append("\n" if char == "\n" else " ")
        index += 1
        if char == "'":
            state = "normal"
    return "".join(output)


def _sql_fragments(text: str, *, path: Path) -> tuple[str, ...]:
    if path.suffix.lower() == ".py":
        literals = _python_string_literals_without_docstrings(text, path=path)
    else:
        literals = (text,)
    return tuple(
        _strip_sql_comments_and_single_quoted_text(literal)
        for literal in literals
    )


def _sql_personal_tables(
    root: Path,
    privacy: PrivacyConfig,
) -> dict[str, set[str]]:
    relevant_columns = (
        set(privacy.owner_columns)
        | set(privacy.personal_content_columns)
    )
    found: dict[str, set[str]] = {}
    for path in _privacy_source_paths(root, privacy.sql_sources):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ManifestError(
                f"cannot scan privacy SQL source {path}: {exc}",
            ) from exc
        for fragment in _sql_fragments(text, path=path):
            for match in _SQL_CREATE_TABLE_RE.finditer(fragment):
                table = _normalize_sql_identifier(match.group("table"))
                body = match.group("body")
                columns = {
                    _normalize_sql_identifier(column.group("column"))
                    for column in _SQL_COLUMN_RE.finditer(
                        body,
                    )
                }
                if columns & relevant_columns:
                    found.setdefault(table, set()).add(
                        path.relative_to(root).as_posix(),
                    )
    return found


def _target_contains_symbol(target: ast.AST, symbol: str) -> bool:
    return any(
        isinstance(node, ast.Name) and node.id == symbol
        for node in ast.walk(target)
    )


def _is_symbol_mutation(node: ast.AST, symbol: str) -> bool:
    if isinstance(node, ast.AugAssign):
        return _target_contains_symbol(node.target, symbol)
    if isinstance(node, ast.NamedExpr):
        return _target_contains_symbol(node.target, symbol)
    if isinstance(node, ast.Delete):
        return any(
            _target_contains_symbol(target, symbol)
            for target in node.targets
        )
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            node.func.attr in {"append", "extend", "update"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == symbol
        )
    return False


def _single_top_level_static_assignment(
    tree: ast.AST,
    symbol: str,
) -> ast.AST | None:
    top_level = set(tree.body) if isinstance(tree, ast.Module) else set()
    assignments: list[ast.AST] = []
    saw_invalid = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            _target_contains_symbol(target, symbol)
            for target in node.targets
        ):
            valid = (
                node in top_level
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == symbol
            )
            if valid:
                assignments.append(node.value)
            else:
                saw_invalid = True
        elif isinstance(node, ast.AnnAssign) and _target_contains_symbol(
            node.target,
            symbol,
        ):
            valid = (
                node in top_level
                and isinstance(node.target, ast.Name)
                and node.target.id == symbol
                and node.value is not None
            )
            if valid:
                assignments.append(node.value)
            else:
                saw_invalid = True
        elif _is_symbol_mutation(node, symbol):
            saw_invalid = True
        elif (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and any(
                (alias.asname or alias.name.rsplit(".", 1)[-1]) == symbol
                for alias in node.names
            )
        ):
            saw_invalid = True
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and node.name == symbol
        ):
            saw_invalid = True
    if not assignments and not saw_invalid:
        return None
    if saw_invalid or len(assignments) != 1:
        raise ManifestError(
            f"{symbol} must have exactly one top-level static assignment",
        )
    return assignments[0]


def _parse_variant_constants(
    value: Any,
    *,
    where: str,
) -> tuple[tuple[str, str], ...]:
    if (
        not isinstance(value, (list, tuple))
        or isinstance(value, (str, bytes))
    ):
        raise ManifestError(f"{where} must be a list or tuple")
    refs: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        item_where = f"{where}[{index}]"
        if (
            not isinstance(item, (list, tuple))
            or isinstance(item, (str, bytes))
            or len(item) != 2
        ):
            raise ManifestError(
                f"{item_where} must be a (repository path, symbol) pair",
            )
        path, symbol = item
        path = _require_string(path, f"{item_where}.path").replace("\\", "/")
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ManifestError(
                f"{item_where}.path must be repository-relative",
            )
        symbol = _require_string(symbol, f"{item_where}.symbol")
        if not symbol.isidentifier():
            raise ManifestError(
                f"{item_where}.symbol must be a Python identifier",
            )
        refs.append((path, symbol))
    if len(refs) != len(set(refs)):
        raise ManifestError(f"{where} has duplicate references")
    return tuple(refs)


def _parse_candidate_literal(
    value: ast.AST,
    *,
    source: str,
) -> tuple[PrivacyCandidate, ...]:
    try:
        raw = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ManifestError(
            f"{source} PERSONAL_DATA_TARGETS must be a static literal",
        ) from exc
    if not isinstance(raw, tuple):
        raise ManifestError(
            f"{source} PERSONAL_DATA_TARGETS must be a tuple literal",
        )

    candidates: list[PrivacyCandidate] = []
    for index, item in enumerate(raw):
        where = f"{source} PERSONAL_DATA_TARGETS[{index}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where} must be a mapping")
        if any(not isinstance(key, str) for key in item):
            raise ManifestError(f"{where} keys must be strings")
        unknown = set(item) - {
            "id",
            "storage_variants",
            "sql_variants",
            "variant_constants",
        }
        if unknown:
            raise ManifestError(f"{where} has unknown keys: {sorted(unknown)}")
        missing = {"id", "storage_variants"} - set(item)
        if missing:
            raise ManifestError(f"{where} is missing keys: {sorted(missing)}")
        target_id = _require_stable_id(item["id"], f"{where}.id")
        variants = _require_string_list(
            item["storage_variants"],
            f"{where}.storage_variants",
            allow_empty=False,
            reject_duplicates=False,
        )
        if len(variants) != len(set(variants)):
            raise ManifestError(
                f"duplicate storage_variants in privacy candidate "
                f"{target_id!r} ({source})",
            )
        sql_variants = _require_string_list(
            item.get("sql_variants", []),
            f"{where}.sql_variants",
            allow_empty=True,
        )
        undeclared_sql = set(sql_variants) - set(variants)
        if undeclared_sql:
            raise ManifestError(
                f"{where}.sql_variants are absent from storage_variants: "
                f"{sorted(undeclared_sql)}",
            )
        variant_constants = _parse_variant_constants(
            item.get("variant_constants", ()),
            where=f"{where}.variant_constants",
        )
        candidates.append(
            PrivacyCandidate(
                id=target_id,
                storage_variants=variants,
                sql_variants=sql_variants,
                variant_constants=variant_constants,
                source=source,
            ),
        )
    return tuple(candidates)


def _static_privacy_candidates(
    root: Path,
    symbol: str,
) -> tuple[PrivacyCandidate, ...]:
    candidates: list[PrivacyCandidate] = []
    for path in _controlled_privacy_source_paths(root, ("**/*.py",)):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ManifestError(
                f"cannot scan privacy registry source {path}: {exc}",
            ) from exc
        if symbol not in text:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            raise ManifestError(
                f"cannot parse privacy registry source {path}: {exc}",
            ) from exc
        source = path.relative_to(root).as_posix()
        value = _single_top_level_static_assignment(tree, symbol)
        if value is not None:
            if not isinstance(value, ast.Tuple):
                raise ManifestError(
                    f"{source} {symbol} must be a module-level tuple literal",
                )
            candidates.extend(
                _parse_candidate_literal(value, source=source),
            )

    ids = [candidate.id for candidate in candidates]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    if duplicate_ids:
        raise ManifestError(
            f"duplicate privacy candidate entries: {duplicate_ids}",
        )
    return tuple(candidates)


def _static_string_constant(
    root: Path,
    *,
    path: str,
    symbol: str,
    candidate_id: str,
) -> str:
    source_path = root / path
    try:
        resolved = _resolve_path(source_path)
        resolved.relative_to(root)
    except ValueError as exc:
        raise ManifestError(
            f"privacy candidate {candidate_id} variant constant path "
            f"escapes repository root: {path}",
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise ManifestError(
            f"privacy candidate {candidate_id} variant constant source "
            f"does not resolve: {path}",
        ) from exc
    if not resolved.is_file():
        raise ManifestError(
            f"privacy candidate {candidate_id} variant constant source "
            f"is not a file: {path}",
        )
    try:
        text = resolved.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(resolved))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ManifestError(
            f"cannot read privacy variant constant source {path}: {exc}",
        ) from exc
    assignment = _single_top_level_static_assignment(tree, symbol)
    if assignment is None:
        raise ManifestError(
            f"privacy candidate {candidate_id} variant constant "
            f"{path}:{symbol} must have exactly one static assignment",
        )
    try:
        value = ast.literal_eval(assignment)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ManifestError(
            f"privacy candidate {candidate_id} variant constant "
            f"{path}:{symbol} must be a static string",
        ) from exc
    if not isinstance(value, str) or not value:
        raise ManifestError(
            f"privacy candidate {candidate_id} variant constant "
            f"{path}:{symbol} must be a static string",
        )
    return value


def _validate_variant_constants(
    root: Path,
    candidate: PrivacyCandidate,
) -> None:
    if not candidate.variant_constants:
        return
    values = tuple(
        _static_string_constant(
            root,
            path=path,
            symbol=symbol,
            candidate_id=candidate.id,
        )
        for path, symbol in candidate.variant_constants
    )
    if values != candidate.storage_variants:
        raise ManifestError(
            f"privacy candidate {candidate.id} variant constants "
            f"storage_variants drift: {values!r} != "
            f"{candidate.storage_variants!r}",
        )


def discover_privacy_candidates(
    repo_root: Path | str,
    privacy: PrivacyConfig,
) -> tuple[PrivacyCandidate, ...]:
    """Statically discover SQL and declared non-SQL personal-data candidates."""

    root, _ = _resolved_repo_and_test_roots(repo_root)
    declared = _static_privacy_candidates(root, privacy.registry_symbol)
    sql_tables = _sql_personal_tables(root, privacy)
    synthetic = tuple(
        PrivacyCandidate(
            id=table,
            storage_variants=(table,),
            sql_variants=(table,),
            variant_constants=(),
            source=", ".join(sorted(sources)),
        )
        for table, sources in sorted(sql_tables.items())
    )
    return declared + synthetic


def _validate_privacy_inventory(
    privacy: PrivacyConfig,
    *,
    repo_root: Path,
) -> None:
    declared = _static_privacy_candidates(
        repo_root,
        privacy.registry_symbol,
    )
    sql_tables = _sql_personal_tables(repo_root, privacy)
    targets = privacy.by_id

    for candidate in declared:
        _validate_variant_constants(repo_root, candidate)
        target = targets.get(candidate.id)
        if target is None:
            raise ManifestError(
                f"unclassified privacy candidate: {candidate.id} "
                f"({candidate.source})",
            )
        if candidate.storage_variants != target.storage_variants:
            raise ManifestError(
                f"privacy candidate {candidate.id} storage_variants drift: "
                f"{candidate.storage_variants!r} != {target.storage_variants!r}",
            )

    declared_ids = {candidate.id for candidate in declared}
    stale = sorted(set(targets) - declared_ids)
    if stale:
        raise ManifestError(f"stale privacy target entries: {stale}")

    discovered_sql = set(sql_tables)
    for candidate in declared:
        missing_sql = sorted(set(candidate.sql_variants) - discovered_sql)
        if missing_sql:
            raise ManifestError(
                f"stale SQL storage variant for privacy target "
                f"{candidate.id}: {missing_sql}",
            )

    classifications: dict[str, list[str]] = {}
    for target in privacy.targets:
        for variant in target.storage_variants:
            classifications.setdefault(variant, []).append(target.id)
    for table, sources in sorted(sql_tables.items()):
        owners = classifications.get(table, [])
        if not owners:
            raise ManifestError(
                f"unclassified privacy SQL table: {table} "
                f"({', '.join(sorted(sources))})",
            )
        if len(owners) != 1:
            raise ManifestError(
                f"multiple privacy classifications for SQL table "
                f"{table!r}: {owners}",
            )


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestError(f"{where} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise ManifestError(f"{where} keys must be strings")
    return value


def _require_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{where} must be a non-empty string")
    return value


def _require_bool(value: Any, where: str) -> bool:
    if type(value) is not bool:
        raise ManifestError(f"{where} must be a boolean")
    return value


def _require_int(value: Any, where: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ManifestError(f"{where} must be an integer >= {minimum}")
    return value


def _require_string_list(
    value: Any,
    where: str,
    *,
    allow_empty: bool,
    reject_duplicates: bool = True,
) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes))
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ManifestError(f"{where} must be a list of non-empty strings")
    items = tuple(value)
    if not allow_empty and not items:
        raise ManifestError(f"{where} must not be empty")
    if reject_duplicates and len(items) != len(set(items)):
        raise ManifestError(f"{where} contains duplicate items")
    return items


def _require_stable_id(value: Any, where: str) -> str:
    item = _require_string(value, where)
    if not _STABLE_ID_RE.fullmatch(item):
        raise ManifestError(
            f"{where} must be a stable lowercase identifier",
        )
    return item


def _parse_privacy_target(
    value: Any,
    *,
    owner_columns: tuple[str, ...],
) -> PrivacyTarget:
    raw = _require_mapping(value, "privacy target")
    unknown = set(raw) - _PRIVACY_TARGET_KEYS
    if unknown:
        raise ManifestError(
            f"privacy target has unknown keys: {sorted(unknown)}",
        )
    missing = _PRIVACY_TARGET_COMMON_KEYS - set(raw)
    if missing:
        raise ManifestError(
            f"privacy target is missing keys: {sorted(missing)}",
        )

    target_id = _require_stable_id(raw["id"], "privacy target.id")
    backend = _require_stable_id(raw["backend"], f"privacy target {target_id}.backend")
    adapter_key = _require_stable_id(
        raw["adapter_key"],
        f"privacy target {target_id}.adapter_key",
    )
    adapter = _require_string(
        raw["adapter"],
        f"privacy target {target_id}.adapter",
    )
    storage_variants = _require_string_list(
        raw["storage_variants"],
        f"privacy target {target_id}.storage_variants",
        allow_empty=False,
    )
    lifecycle = _require_string(
        raw["lifecycle"],
        f"privacy target {target_id}.lifecycle",
    )
    if lifecycle not in _LIFECYCLES:
        raise ManifestError(
            f"privacy target {target_id}.lifecycle must be one of "
            f"{sorted(_LIFECYCLES)}",
        )
    enforced_from = _require_string(
        raw["enforced_from"],
        f"privacy target {target_id}.enforced_from",
    )
    if enforced_from not in _MILESTONE_ORDER:
        raise ManifestError(
            f"privacy target {target_id}.enforced_from must be one of "
            f"{list(_MILESTONE_ORDER)}",
        )
    owner_fields = _require_string_list(
        raw["owner_fields"],
        f"privacy target {target_id}.owner_fields",
        allow_empty=False,
    )
    if owner_fields != owner_columns:
        raise ManifestError(
            f"privacy target {target_id}.owner_fields must equal "
            "privacy.owner_columns",
        )

    case_prefix = _MILESTONE_CASE_PREFIX[enforced_from]
    case_values: dict[str, str] = {}
    for field in _PRIVACY_PROBE_FIELDS:
        case_id = _require_stable_id(
            raw[field],
            f"privacy target {target_id}.{field}",
        )
        if not case_id.startswith(case_prefix):
            raise ManifestError(
                f"privacy target {target_id}.{field} must start with "
                f"{case_prefix!r}",
            )
        case_values[field] = case_id
    if len(set(case_values.values())) != len(_PRIVACY_PROBE_FIELDS):
        raise ManifestError(
            f"privacy target {target_id} probe IDs must be distinct",
        )

    policy_flags = _require_string_list(
        raw.get("policy_flags", []),
        f"privacy target {target_id}.policy_flags",
        allow_empty=True,
    )
    for flag in policy_flags:
        if not _STABLE_ID_RE.fullmatch(flag):
            raise ManifestError(
                f"privacy target {target_id}.policy_flags contains "
                f"an invalid value: {flag!r}",
            )

    delete_action = ""
    retention_reason = ""
    retain_or_redact_action = ""
    if lifecycle == "deletable":
        if "seed_case" not in raw:
            raise ManifestError(
                f"privacy target {target_id} is missing seed_case",
            )
        if "delete_action" not in raw:
            raise ManifestError(
                f"privacy target {target_id} is missing delete_action",
            )
        delete_action = _require_stable_id(
            raw["delete_action"],
            f"privacy target {target_id}.delete_action",
        )
        forbidden = {"retention_reason", "retain_or_redact_action"} & set(raw)
        if forbidden:
            raise ManifestError(
                f"privacy target {target_id} deletable has incompatible keys: "
                f"{sorted(forbidden)}",
            )
    else:
        required = {"retention_reason", "retain_or_redact_action"}
        missing_lifecycle = required - set(raw)
        if missing_lifecycle:
            raise ManifestError(
                f"privacy target {target_id} is missing keys: "
                f"{sorted(missing_lifecycle)}",
            )
        retention_reason = _require_stable_id(
            raw["retention_reason"],
            f"privacy target {target_id}.retention_reason",
        )
        retain_or_redact_action = _require_stable_id(
            raw["retain_or_redact_action"],
            f"privacy target {target_id}.retain_or_redact_action",
        )
        if "delete_action" in raw:
            raise ManifestError(
                f"privacy target {target_id} {lifecycle} must not declare "
                "delete_action",
            )

    return PrivacyTarget(
        id=target_id,
        backend=backend,
        adapter_key=adapter_key,
        adapter=adapter,
        storage_variants=storage_variants,
        lifecycle=lifecycle,
        enforced_from=enforced_from,
        owner_fields=owner_fields,
        seed_case=case_values["seed_case"],
        count_probe=case_values["count_probe"],
        read_probe=case_values["read_probe"],
        verify_case=case_values["verify_case"],
        delete_action=delete_action,
        retention_reason=retention_reason,
        retain_or_redact_action=retain_or_redact_action,
        policy_flags=policy_flags,
    )


def _validate_privacy_probe_ids(
    targets: Sequence[PrivacyTarget],
) -> None:
    owners: dict[str, str] = {}
    for target in targets:
        values = tuple(
            getattr(target, field)
            for field in _PRIVACY_PROBE_FIELDS
        )
        if len(set(values)) != len(_PRIVACY_PROBE_FIELDS):
            raise ManifestError(
                f"privacy target {target.id} probe IDs must be distinct",
            )
        for probe_id in values:
            previous = owners.get(probe_id)
            if previous is not None:
                raise ManifestError(
                    f"privacy probe ID {probe_id!r} is reused by "
                    f"{previous} and {target.id}",
                )
            owners[probe_id] = target.id


def _parse_privacy(value: Any) -> PrivacyConfig:
    raw = _require_mapping(value, "privacy")
    unknown = set(raw) - _PRIVACY_KEYS
    if unknown:
        raise ManifestError(f"privacy has unknown keys: {sorted(unknown)}")
    missing = _PRIVACY_KEYS - set(raw)
    if missing:
        raise ManifestError(f"privacy is missing keys: {sorted(missing)}")

    owner_columns = _require_string_list(
        raw["owner_columns"],
        "privacy.owner_columns",
        allow_empty=False,
    )
    if owner_columns != _OWNER_COLUMNS:
        raise ManifestError(
            f"privacy.owner_columns must be exactly {list(_OWNER_COLUMNS)}",
        )
    content_columns = _require_string_list(
        raw["personal_content_columns"],
        "privacy.personal_content_columns",
        allow_empty=False,
    )
    if content_columns != _PERSONAL_CONTENT_COLUMNS:
        raise ManifestError(
            "privacy.personal_content_columns does not match the frozen contract",
        )
    sql_sources = _require_string_list(
        raw["sql_sources"],
        "privacy.sql_sources",
        allow_empty=False,
    )
    if sql_sources != _SQL_SOURCES:
        raise ManifestError(
            "privacy.sql_sources does not match the frozen contract",
        )
    registry_symbol = _require_string(
        raw["registry_symbol"],
        "privacy.registry_symbol",
    )
    if registry_symbol != _REGISTRY_SYMBOL:
        raise ManifestError(
            f"privacy.registry_symbol must be {_REGISTRY_SYMBOL!r}",
        )

    raw_targets = raw["targets"]
    if (
        not isinstance(raw_targets, Sequence)
        or isinstance(raw_targets, (str, bytes))
    ):
        raise ManifestError("privacy.targets must be a list")
    targets = tuple(
        _parse_privacy_target(item, owner_columns=owner_columns)
        for item in raw_targets
    )
    ids = [target.id for target in targets]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    if duplicate_ids:
        raise ManifestError(
            f"duplicate privacy target entries: {duplicate_ids}",
        )
    _validate_privacy_probe_ids(targets)

    owners: dict[str, list[str]] = {}
    for target in targets:
        for variant in target.storage_variants:
            owners.setdefault(variant, []).append(target.id)
    overlaps = {
        variant: target_ids
        for variant, target_ids in owners.items()
        if len(target_ids) != 1
    }
    if overlaps:
        variant = sorted(overlaps)[0]
        raise ManifestError(
            "multiple privacy classifications for storage variant "
            f"{variant!r}: {overlaps[variant]}",
        )

    return PrivacyConfig(
        owner_columns=owner_columns,
        personal_content_columns=content_columns,
        sql_sources=sql_sources,
        registry_symbol=registry_symbol,
        targets=targets,
    )


def _runtime_target_value(target: Any, field: str) -> Any:
    if isinstance(target, Mapping):
        return target.get(field, "")
    return getattr(target, field, "")


def _target_snapshot(target: Any, *, where: str) -> dict[str, Any]:
    if isinstance(target, Mapping) and any(
        not isinstance(key, str)
        for key in target
    ):
        raise ManifestError(f"{where} mapping keys must be strings")
    raw_id = _runtime_target_value(target, "id")
    target_id = raw_id if isinstance(raw_id, str) and raw_id else "<unknown>"
    snapshot: dict[str, Any] = {}
    tuple_fields = {"storage_variants", "owner_fields", "policy_flags"}
    for field in _PRIVACY_TARGET_KEYS:
        value = _runtime_target_value(target, field)
        if field in tuple_fields:
            if (
                not isinstance(value, (list, tuple))
                or isinstance(value, (str, bytes))
                or any(not isinstance(item, str) for item in value)
            ):
                raise ManifestError(
                    f"{where} {target_id}.{field} must be a list or tuple "
                    "of strings",
                )
            value = tuple(value)
        elif not isinstance(value, str):
            raise ManifestError(
                f"{where} {target_id}.{field} must be a string",
            )
        snapshot[field] = value
    return snapshot


def validate_runtime_privacy_sync(
    manifest_targets: Sequence[Any],
    runtime_targets: Sequence[Any],
    *,
    runtime_adapters: Mapping[str, str] | None = None,
) -> None:
    """Require the acceptance mirror to match the production registry exactly."""

    manifest_snapshots = [
        _target_snapshot(target, where="manifest privacy target")
        for target in manifest_targets
    ]
    runtime_snapshots = [
        _target_snapshot(target, where="runtime privacy target")
        for target in runtime_targets
    ]
    if runtime_adapters is not None:
        if not isinstance(runtime_adapters, Mapping):
            raise ManifestError("runtime PRIVACY_ADAPTERS must be a mapping")
        adapter_snapshot: dict[str, str] = {}
        for key, adapter in runtime_adapters.items():
            if not isinstance(key, str) or not key:
                raise ManifestError(
                    "runtime PRIVACY_ADAPTERS has an invalid adapter key",
                )
            if not isinstance(adapter, str) or not adapter:
                raise ManifestError(
                    f"runtime PRIVACY_ADAPTERS[{key!r}] must be non-empty",
                )
            adapter_snapshot[key] = adapter
        for label, snapshots in (
            ("manifest", manifest_snapshots),
            ("runtime", runtime_snapshots),
        ):
            for target in snapshots:
                target_id = target["id"]
                adapter_key = target["adapter_key"]
                expected = adapter_snapshot.get(adapter_key)
                if expected is None or target["adapter"] != expected:
                    raise ManifestError(
                        f"privacy target {target_id!r} {label} adapter does "
                        f"not match runtime PRIVACY_ADAPTERS[{adapter_key!r}]",
                    )
    manifest_ids = [item["id"] for item in manifest_snapshots]
    runtime_ids = [item["id"] for item in runtime_snapshots]
    if manifest_ids != runtime_ids:
        raise ManifestError(
            "runtime registry drift in target order/ids: "
            f"manifest={manifest_ids}, runtime={runtime_ids}",
        )
    for manifest_target, runtime_target in zip(
        manifest_snapshots,
        runtime_snapshots,
    ):
        if manifest_target != runtime_target:
            fields = sorted(
                field
                for field in _PRIVACY_TARGET_KEYS
                if manifest_target[field] != runtime_target[field]
            )
            raise ManifestError(
                f"runtime registry drift for privacy target "
                f"{manifest_target['id']}: {fields}",
            )


def _load_runtime_privacy_registry(
    repo_root: Path,
) -> tuple[Sequence[Any], Mapping[str, str]] | None:
    registry_path = repo_root / "runtime" / "privacy_registry.py"
    if not registry_path.is_file():
        return None
    module_name = f"_e2e_privacy_registry_{abs(hash(registry_path))}"
    spec = importlib.util.spec_from_file_location(module_name, registry_path)
    if spec is None or spec.loader is None:
        raise ManifestError(
            f"cannot load runtime privacy registry: {registry_path}",
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        if exc.__class__.__name__ == "PrivacyRegistryError":
            raise ManifestError(
                f"cannot load runtime privacy registry: {exc}",
            ) from exc
        raise
    finally:
        sys.modules.pop(module_name, None)
    targets = getattr(module, "PRIVACY_TARGETS", None)
    if not isinstance(targets, (list, tuple)) or not targets:
        raise ManifestError(
            "runtime privacy registry must expose non-empty PRIVACY_TARGETS",
        )
    adapters = getattr(module, "PRIVACY_ADAPTERS", None)
    if not isinstance(adapters, Mapping) or not adapters:
        raise ManifestError(
            "runtime privacy registry must expose non-empty PRIVACY_ADAPTERS",
        )
    return targets, adapters


def privacy_targets_for_milestone(
    manifest: E2EManifest,
    milestone: str,
) -> tuple[PrivacyTarget, ...]:
    if milestone not in _MILESTONE_ORDER:
        raise ManifestError(f"unknown privacy milestone: {milestone!r}")
    current = _MILESTONE_ORDER[milestone]
    return tuple(
        target
        for target in manifest.privacy.targets
        if _MILESTONE_ORDER[target.enforced_from] <= current
    )


def validate_privacy_execution(
    manifest: E2EManifest,
    *,
    milestone: str,
    available_case_ids: set[str] | frozenset[str],
    available_action_ids: set[str] | frozenset[str],
) -> tuple[PrivacyTarget, ...]:
    """Resolve only targets due at ``milestone``; future IDs remain declarations."""

    _validate_privacy_probe_ids(manifest.privacy.targets)
    selected = privacy_targets_for_milestone(manifest, milestone)
    cases = set(available_case_ids)
    actions = set(available_action_ids)
    for target in selected:
        required_cases = {
            target.seed_case,
            target.count_probe,
            target.read_probe,
            target.verify_case,
        }
        missing_cases = sorted(required_cases - cases)
        action = (
            target.delete_action
            if target.lifecycle == "deletable"
            else target.retain_or_redact_action
        )
        missing_action = action if action not in actions else ""
        if missing_cases or missing_action:
            details = []
            if missing_cases:
                details.append(f"cases={missing_cases}")
            if missing_action:
                details.append(f"action={missing_action!r}")
            raise ManifestError(
                f"privacy target {target.id} has unresolvable enforcement "
                f"contract at {milestone}: {', '.join(details)}",
            )
    return selected


def _parse_nightly(
    value: Any,
    case_id: str,
    *,
    case_memory_sessions: int,
) -> NightlySelection:
    where = f"case {case_id}.nightly"
    raw = _require_mapping(value, where)
    unknown = set(raw) - {"all", "args", "memory_sessions"}
    if unknown:
        raise ManifestError(f"{where} has unknown keys: {sorted(unknown)}")
    selectors = set(raw) & {"all", "args"}
    if selectors == {"all"}:
        if _require_bool(raw["all"], f"{where}.all") is not True:
            raise ManifestError(f"{where}.all must be true")
        all_selected = True
        args: tuple[str, ...] = ()
    elif selectors == {"args"}:
        args = _require_string_list(
            raw["args"],
            f"{where} args",
            allow_empty=False,
            reject_duplicates=False,
        )
        all_selected = False
    else:
        raise ManifestError(f"{where} selection is empty or ambiguous")

    memory_sessions = None
    if "memory_sessions" in raw:
        memory_sessions = _require_int(
            raw["memory_sessions"],
            f"{where}.memory_sessions",
        )
        if memory_sessions > case_memory_sessions:
            raise ManifestError(
                f"{where}.memory_sessions must not exceed "
                f"case {case_id}.memory_sessions",
            )
    return NightlySelection(
        all=all_selected,
        args=args,
        memory_sessions=memory_sessions,
    )


def _normalize_case_path(value: Any, where: str) -> str:
    path = _require_string(value, where).replace("\\", "/")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ManifestError(f"{where} must be a repository-relative path")
    return path


def _command_resolves(command: tuple[str, ...], where: str) -> None:
    executable = command[0]
    executable_path = Path(executable)
    if executable_path.is_absolute():
        found = executable_path.is_file()
    else:
        found = shutil.which(executable) is not None
    if not found:
        raise ManifestError(f"{where} executable does not exist: {executable}")


def _parse_case(
    value: Any,
    *,
    repo_root: Path,
    test_root: Path,
    planned_paths: tuple[str, ...],
) -> E2ECase:
    raw = _require_mapping(value, "case")
    unknown = set(raw) - _CASE_KEYS
    if unknown:
        raise ManifestError(f"case has unknown keys: {sorted(unknown)}")
    missing = _REQUIRED_CASE_KEYS - set(raw)
    if missing:
        raise ManifestError(f"case is missing keys: {sorted(missing)}")

    case_id = _require_string(raw["id"], "case.id")
    if not _CASE_ID_RE.fullmatch(case_id):
        raise ManifestError(f"case {case_id!r} has invalid id")

    path = _normalize_case_path(raw["path"], f"case {case_id}.path")
    candidate = repo_root / path
    planned_and_missing = (
        path in planned_paths
        and not candidate.exists()
        and not candidate.is_symlink()
    )
    if not planned_and_missing:
        _resolve_test_file(
            candidate,
            test_root=test_root,
            where=f"case {case_id}.path",
        )

    command = _require_string_list(
        raw["command"],
        f"case {case_id}.command",
        allow_empty=False,
        reject_duplicates=False,
    )
    if command[0] != "python":
        raise ManifestError(
            f"case {case_id}.command executable must be the literal 'python'",
        )
    expected_command = ("python", path)
    if command != expected_command:
        raise ManifestError(
            f"case {case_id}.command has an invalid frozen command shape",
        )
    _command_resolves(command, f"case {case_id}.command")

    group = _require_string(raw["group"], f"case {case_id}.group")
    if group not in _GROUPS:
        raise ManifestError(
            f"case {case_id}.group must be one of {sorted(_GROUPS)}",
        )

    lanes = _require_string_list(
        raw["lanes"],
        f"case {case_id}.lanes",
        allow_empty=True,
    )
    invalid_lanes = set(lanes) - _LANES
    if invalid_lanes:
        raise ManifestError(
            f"case {case_id}.lanes has invalid values: {sorted(invalid_lanes)}",
        )
    if group in {"acoustic_probe", "manual_inspection"} and lanes:
        raise ManifestError(f"case {case_id}.{group} must not declare lanes")
    if group not in {"acoustic_probe", "manual_inspection"} and not lanes:
        raise ManifestError(f"case {case_id}.lanes must not be empty")

    timeout_s = _require_int(
        raw["timeout_s"],
        f"case {case_id}.timeout_s",
        minimum=1,
    )
    if timeout_s > 1800:
        raise ManifestError(f"case {case_id}.timeout_s must be <= 1800")

    profile = _require_string(raw["profile"], f"case {case_id}.profile")
    if profile not in _PROFILES:
        raise ManifestError(
            f"case {case_id}.profile must be one of {sorted(_PROFILES)}",
        )

    skip_reasons = _require_string_list(
        raw["skip_reasons"],
        f"case {case_id}.skip_reasons",
        allow_empty=False,
    )
    if skip_reasons not in _SKIP_POLICIES:
        raise ManifestError(
            f"case {case_id}.skip_reasons is not an allowed policy",
        )

    signed_identity = _require_bool(
        raw["signed_identity"],
        f"case {case_id}.signed_identity",
    )
    persistent_data = _require_bool(
        raw["persistent_data"],
        f"case {case_id}.persistent_data",
    )
    memory_sessions = _require_int(
        raw["memory_sessions"],
        f"case {case_id}.memory_sessions",
    )
    if memory_sessions > 64:
        raise ManifestError(
            f"case {case_id}.memory_sessions must be <= 64",
        )
    if memory_sessions > 0 and not signed_identity:
        raise ManifestError(
            f"case {case_id}.signed_identity must be true when "
            "memory_sessions is positive",
        )

    nightly = (
        _parse_nightly(
            raw["nightly"],
            case_id,
            case_memory_sessions=memory_sessions,
        )
        if "nightly" in raw
        else None
    )
    if "nightly" in lanes and nightly is None:
        raise ManifestError(f"case {case_id}.nightly selection is empty")
    if "nightly" not in lanes and nightly is not None:
        raise ManifestError(
            f"case {case_id}.nightly is set without the nightly lane",
        )
    if nightly is not None and path in nightly.args:
        raise ManifestError(
            f"case {case_id}.nightly args must not duplicate the manifest path",
        )
    fixture_pre_step = None
    if "fixture_pre_step" in raw:
        fixture_pre_step = _require_string(
            raw["fixture_pre_step"],
            f"case {case_id}.fixture_pre_step",
        )
        if fixture_pre_step != "voiceprint":
            raise ManifestError(
                f"case {case_id}.fixture_pre_step must be voiceprint",
            )
        if case_id != "e2e_voiceprint":
            raise ManifestError(
                "fixture_pre_step voiceprint is restricted to e2e_voiceprint",
            )

    return E2ECase(
        id=case_id,
        path=path,
        command=command,
        group=group,
        lanes=lanes,
        timeout_s=timeout_s,
        profile=profile,
        skip_reasons=skip_reasons,
        signed_identity=signed_identity,
        persistent_data=persistent_data,
        memory_sessions=memory_sessions,
        nightly=nightly,
        fixture_pre_step=fixture_pre_step,
    )


def _parse_canonical_paths(value: Any, where: str) -> tuple[str, ...]:
    items = _require_string_list(
        value,
        where,
        allow_empty=(where == "manifest.runner_dependencies"),
    )
    normalized: list[str] = []
    for raw in items:
        path = raw.replace("\\", "/")
        candidate = Path(path)
        if (
            raw != path
            or candidate.is_absolute()
            or ".." in candidate.parts
            or path.startswith("/")
            or "\x00" in path
        ):
            raise ManifestError(
                f"{where} contains an unsafe repository-relative path: {raw!r}",
            )
        normalized.append(path)
    return tuple(normalized)


def _parse_non_secret_config_keys(value: Any) -> tuple[str, ...]:
    keys = _require_string_list(
        value,
        "manifest.non_secret_config_keys",
        allow_empty=False,
    )
    for key in keys:
        if (
            _CONFIG_KEY_RE.fullmatch(key) is None
            or _is_sensitive_config_key(key)
        ):
            raise ManifestError(
                "manifest.non_secret_config_keys contains a sensitive or "
                f"invalid key: {key!r}",
            )
    return keys


def _parse_manifest(data: Any, *, repo_root: Path) -> E2EManifest:
    raw = _require_mapping(data, "manifest")
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ManifestError(f"manifest has unknown keys: {sorted(unknown)}")
    missing = _TOP_LEVEL_KEYS - set(raw)
    if missing:
        raise ManifestError(f"manifest is missing keys: {sorted(missing)}")

    version = _require_int(raw["version"], "manifest.version", minimum=1)
    if version != 1:
        raise ManifestError("manifest.version must be 1")

    planned_paths = _require_string_list(
        raw["planned_paths"],
        "manifest.planned_paths",
        allow_empty=True,
    )
    invalid_planned = set(planned_paths) - _PLANNED_PATH_ALLOWLIST
    if invalid_planned:
        raise ManifestError(
            "manifest.planned_paths contains unsupported paths: "
            f"{sorted(invalid_planned)}",
        )

    privacy = _parse_privacy(raw["privacy"])
    canonical_inputs = _parse_canonical_paths(
        raw["canonical_inputs"],
        "manifest.canonical_inputs",
    )
    runner_dependencies = _parse_canonical_paths(
        raw["runner_dependencies"],
        "manifest.runner_dependencies",
    )
    if any(not item.endswith(".py") for item in runner_dependencies):
        raise ManifestError(
            "manifest.runner_dependencies must contain Python source paths",
        )
    non_secret_config_keys = _parse_non_secret_config_keys(
        raw["non_secret_config_keys"],
    )

    raw_cases = raw["cases"]
    if (
        not isinstance(raw_cases, Sequence)
        or isinstance(raw_cases, (str, bytes))
    ):
        raise ManifestError("manifest.cases must be a list")
    if not raw_cases:
        raise ManifestError("manifest.cases selection is empty")

    _, test_root = _resolved_repo_and_test_roots(repo_root)
    if test_root is None:
        raise ManifestError("test root does not exist")
    cases = tuple(
        _parse_case(
            value,
            repo_root=repo_root,
            test_root=test_root,
            planned_paths=planned_paths,
        )
        for value in raw_cases
    )
    ids = [case.id for case in cases]
    paths = [case.path for case in cases]
    duplicate_ids = sorted({item for item in ids if ids.count(item) > 1})
    if duplicate_ids:
        raise ManifestError(f"duplicate id entries: {duplicate_ids}")
    duplicate_paths = sorted(
        {item for item in paths if paths.count(item) > 1},
    )
    if duplicate_paths:
        raise ManifestError(f"duplicate path entries: {duplicate_paths}")

    case_paths = set(paths)
    planned_without_case = set(planned_paths) - case_paths
    if planned_without_case:
        raise ManifestError(
            "manifest.planned_paths lacks case entries: "
            f"{sorted(planned_without_case)}",
        )

    discovered = set(discover_e2e_files(repo_root))
    unregistered = discovered - case_paths
    if unregistered:
        raise ManifestError(
            f"unregistered e2e files: {sorted(unregistered)}",
        )
    missing_files = case_paths - discovered - set(planned_paths)
    if missing_files:
        raise ManifestError(
            f"manifest paths do not exist: {sorted(missing_files)}",
        )

    _validate_privacy_inventory(privacy, repo_root=repo_root)
    runtime_registry = _load_runtime_privacy_registry(repo_root)
    if runtime_registry is not None:
        runtime_targets, runtime_adapters = runtime_registry
        validate_runtime_privacy_sync(
            privacy.targets,
            runtime_targets,
            runtime_adapters=runtime_adapters,
        )

    return E2EManifest(
        version=version,
        planned_paths=planned_paths,
        privacy=privacy,
        canonical_inputs=canonical_inputs,
        runner_dependencies=runner_dependencies,
        non_secret_config_keys=non_secret_config_keys,
        cases=cases,
    )


def load_manifest(
    path: Path | str = DEFAULT_MANIFEST_PATH,
    *,
    repo_root: Path | str | None = None,
) -> E2EManifest:
    """Load and validate the manifest, including the live file inventory."""

    manifest_path = Path(path).resolve()
    root, _ = _resolved_repo_and_test_roots(
        repo_root if repo_root is not None else manifest_path.parent.parent,
    )
    if not manifest_path.is_file():
        raise ManifestError(f"manifest does not exist: {manifest_path}")
    try:
        data = yaml.load(
            manifest_path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except ManifestError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ManifestError(f"cannot read manifest: {exc}") from exc
    return _parse_manifest(data, repo_root=root)


# ---------------------------------------------------------------------------
# Canonical journey evidence
# ---------------------------------------------------------------------------

def _git(
    repo_root: Path,
    args: Sequence[str],
    *,
    text: bool,
    check: bool = True,
) -> str | bytes:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
            encoding="utf-8" if text else None,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError(f"git command failed: {' '.join(args)}") from exc
    if check and completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            if isinstance(completed.stderr, str)
            else completed.stderr.decode("utf-8", "replace").strip()
        )
        raise ManifestError(
            f"git command failed ({' '.join(args)}): {detail[:300]}",
        )
    return completed.stdout


def _canonical_repo_root(repo_root: Path | str) -> Path:
    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManifestError(
            f"canonical repository root cannot be resolved: {repo_root}",
        ) from exc
    if not root.is_dir() or root.is_symlink():
        raise ManifestError("canonical repository root must be a regular directory")
    shown = str(_git(root, ("rev-parse", "--show-toplevel"), text=True)).strip()
    try:
        git_root = Path(shown).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ManifestError("git repository root cannot be resolved") from exc
    if git_root != root:
        raise ManifestError(
            f"canonical repository root mismatch: expected {root}, git={git_root}",
        )
    return root


def _nul_paths(raw: bytes, *, where: str) -> tuple[str, ...]:
    try:
        values = tuple(
            item.decode("utf-8")
            for item in raw.split(b"\0")
            if item
        )
    except UnicodeError as exc:
        raise ManifestError(f"{where} contains a non-UTF-8 path") from exc
    normalized: list[str] = []
    for value in values:
        path = value.replace("\\", "/")
        if (
            value != path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or "\x00" in path
        ):
            raise ManifestError(f"{where} contains an unsafe path: {value!r}")
        normalized.append(path)
    return tuple(normalized)


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(
        path == pattern or fnmatch.fnmatchcase(path, pattern)
        for pattern in patterns
    )


def _canonical_file(root: Path, relative: str) -> Path:
    unresolved = root / relative
    if unresolved.is_symlink():
        raise ManifestError(f"canonical input is a symlink: {relative}")
    try:
        resolved = unresolved.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ManifestError(
            f"canonical input cannot be resolved inside repository: {relative}",
        ) from exc
    if not resolved.is_file():
        raise ManifestError(f"canonical input is not a regular file: {relative}")
    return resolved


def _sensitive_canonical_path(relative: str) -> bool:
    lowered = relative.replace("\\", "/").lower()
    basename = Path(lowered).name
    if basename in {".env.example", ".env.template"}:
        return False
    if basename == ".env" or basename.startswith(".env."):
        return True
    if Path(basename).suffix in {
        ".key",
        ".pem",
        ".p12",
        ".pfx",
        ".jks",
        ".keystore",
    }:
        return True
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    wrapped = f"_{normalized}_"
    terms = (
        "credential",
        "credentials",
        "private_key",
        "access_key",
        "api_key",
        "authorization",
        "password",
        "passwd",
        "secret",
        "token",
        "cookie",
    )
    return any(f"_{term}_" in wrapped for term in terms)


def _canonical_bytes(path: Path) -> bytes:
    try:
        size_before = path.stat().st_size
        if size_before > MAX_CANONICAL_INPUT_BYTES:
            raise ManifestError(f"canonical input exceeds size limit: {path}")
        with path.open("rb") as handle:
            raw = handle.read(MAX_CANONICAL_INPUT_BYTES + 1)
        size_after = path.stat().st_size
    except OSError as exc:
        raise ManifestError(f"canonical input cannot be read: {path}") from exc
    if len(raw) > MAX_CANONICAL_INPUT_BYTES:
        raise ManifestError(f"canonical input exceeds size limit: {path}")
    if size_before != size_after or len(raw) != size_after:
        raise ManifestError(f"canonical input changed while reading: {path}")
    # A NUL byte is a conservative binary marker.  Some binary formats happen
    # to be valid UTF-8, but their CR/LF bytes are payload and must remain exact.
    if b"\0" in raw or any(
        byte < 32 and byte not in {9, 10, 12, 13} or byte == 127
        for byte in raw
    ):
        return raw
    try:
        raw.decode("utf-8")
    except UnicodeError:
        return raw
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _digest_files(root: Path, paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(set(paths)):
        content = _canonical_bytes(_canonical_file(root, relative))
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
    return digest.hexdigest()


def _resolve_local_import(
    root: Path,
    importer: str,
    module: str,
    *,
    level: int = 0,
) -> str | None:
    importer_path = Path(importer)
    module_parts = tuple(part for part in module.split(".") if part)
    candidates: list[Path] = []
    if level:
        base = importer_path.parent
        for _ in range(max(0, level - 1)):
            base = base.parent
        target = base.joinpath(*module_parts)
        candidates.extend((target.with_suffix(".py"), target / "__init__.py"))
    else:
        target = Path(*module_parts)
        local = importer_path.parent / target
        candidates.extend((local.with_suffix(".py"), local / "__init__.py"))
        candidates.extend((
            (root / target).relative_to(root).with_suffix(".py"),
            (root / target / "__init__.py").relative_to(root),
            Path("test") / target.with_suffix(".py"),
            Path("test") / target / "__init__.py",
        ))
    for candidate in candidates:
        relative = candidate.as_posix()
        unresolved = root / relative
        if unresolved.exists() or unresolved.is_symlink():
            _canonical_file(root, relative)
            return relative
    return None


def _runner_import_closure(
    root: Path,
    *,
    roots: Sequence[str],
    dependencies: Sequence[str],
    tracked: frozenset[str],
) -> tuple[str, ...]:
    explicit = tuple(dict.fromkeys(dependencies))
    pending = list(dict.fromkeys([*roots, *explicit]))
    closure: set[str] = set()
    while pending:
        relative = pending.pop(0)
        if relative in closure:
            continue
        if relative not in tracked:
            raise ManifestError(
                f"runner dependency is not a tracked file: {relative}",
            )
        path = _canonical_file(root, relative)
        if path.suffix != ".py":
            raise ManifestError(f"runner dependency is not Python: {relative}")
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=relative)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise ManifestError(
                f"runner dependency cannot be parsed: {relative}",
            ) from exc
        closure.add(relative)
        string_constants: dict[str, str] = {}
        assigned_expressions: dict[str, ast.expr] = {}
        import_aliases: dict[str, str] = {}
        for statement in ast.walk(tree):
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    bound = alias.asname or alias.name.split(".", 1)[0]
                    import_aliases[bound] = (
                        alias.name if alias.asname else bound
                    )
            elif isinstance(statement, ast.ImportFrom) and statement.module:
                for alias in statement.names:
                    if alias.name != "*":
                        import_aliases[alias.asname or alias.name] = (
                            f"{statement.module}.{alias.name}"
                        )
            if (
                isinstance(statement, (ast.Assign, ast.AnnAssign))
            ):
                targets = (
                    statement.targets
                    if isinstance(statement, ast.Assign)
                    else (statement.target,)
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        assigned_expressions[target.id] = statement.value
                        if (
                            isinstance(statement.value, ast.Constant)
                            and isinstance(statement.value.value, str)
                        ):
                            string_constants[target.id] = statement.value.value

        def static_path(
            expression: ast.expr | None,
            seen: frozenset[str] = frozenset(),
        ) -> str | None:
            if (
                isinstance(expression, ast.Constant)
                and isinstance(expression.value, str)
            ):
                return expression.value
            if isinstance(expression, ast.Name):
                if expression.id in {"repo_root", "REPO_ROOT"}:
                    return ""
                if expression.id in seen:
                    return None
                assigned = assigned_expressions.get(expression.id)
                if assigned is None:
                    return None
                return static_path(assigned, seen | {expression.id})
            if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Div):
                left = static_path(expression.left, seen)
                right = static_path(expression.right, seen)
                if left is None or right is None:
                    return None
                return (Path(left) / right).as_posix()
            return None

        def callable_path(
            expression: ast.expr | None,
            seen: frozenset[str] = frozenset(),
        ) -> str | None:
            if isinstance(expression, ast.Name):
                imported = import_aliases.get(expression.id)
                if imported is not None:
                    return imported
                if expression.id in seen:
                    return None
                assigned = assigned_expressions.get(expression.id)
                if assigned is None:
                    return expression.id if expression.id == "__import__" else None
                return callable_path(assigned, seen | {expression.id})
            if isinstance(expression, ast.Attribute):
                base = callable_path(expression.value, seen)
                return f"{base}.{expression.attr}" if base else None
            return None

        def local_path(raw: str) -> str | None:
            value = raw.replace("\\", "/")
            if (
                not value
                or Path(value).is_absolute()
                or ".." in Path(value).parts
                or "\0" in value
            ):
                return None
            candidates = (
                Path(value),
                Path(relative).parent / value,
            )
            for candidate in dict.fromkeys(item.as_posix() for item in candidates):
                if candidate in tracked:
                    _canonical_file(root, candidate)
                    return candidate
            return None

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
                level = 0
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                modules = [base]
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    modules.append(
                        ".".join(part for part in (base, alias.name) if part)
                    )
                modules = tuple(dict.fromkeys(modules))
                level = node.level
            else:
                modules = ()
                level = 0
            for module in modules:
                resolved = _resolve_local_import(
                    root,
                    relative,
                    module,
                    level=level,
                )
                if resolved is not None and resolved not in closure:
                    pending.append(resolved)
            if not isinstance(node, ast.Call):
                continue
            loader_path = callable_path(node.func)
            file_loader = loader_path == "importlib.util.spec_from_file_location"
            if file_loader:
                raw_target = static_path(node.args[1] if len(node.args) > 1 else None)
                resolved_path = local_path(raw_target) if raw_target is not None else None
                if resolved_path is None:
                    raise ManifestError(
                        f"{relative}:{node.lineno}:{node.col_offset}: "
                        "spec_from_file_location target cannot be resolved "
                        "statically",
                    )
                if resolved_path not in explicit:
                    raise ManifestError(
                        f"{relative}:{node.lineno}:{node.col_offset}: "
                        "spec_from_file_location target requires its exact "
                        "explicit manifest runner dependency",
                    )
                if resolved_path not in closure:
                    pending.append(resolved_path)
                continue
            dynamic = (
                loader_path in {"__import__", "importlib.import_module"}
            )
            if not dynamic:
                continue
            argument = node.args[0] if node.args else None
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                target = argument.value
            elif isinstance(argument, ast.Name):
                target = string_constants.get(argument.id)
            else:
                target = None
            if not target or target.startswith("."):
                raise ManifestError(
                    f"{relative}:{node.lineno}:{node.col_offset}: dynamic import "
                    "target cannot be resolved; add a statically resolvable "
                    "explicit manifest runner dependency",
                )
            resolved = _resolve_local_import(root, relative, target)
            if resolved is None:
                raise ManifestError(
                    f"{relative}:{node.lineno}:{node.col_offset}: dynamic "
                    f"import target {target!r} cannot be resolved statically",
                )
            if resolved not in explicit:
                raise ManifestError(
                    f"{relative}:{node.lineno}:{node.col_offset}: dynamic import "
                    f"target {target!r} requires its explicit manifest runner "
                    "dependency",
                )
            if resolved not in closure:
                pending.append(resolved)
    return tuple(sorted(closure))


def _config_digest(
    keys: Sequence[str],
    environ: Mapping[str, str],
) -> str:
    digest = hashlib.sha256()
    for key in sorted(keys):
        if (
            _CONFIG_KEY_RE.fullmatch(key) is None
            or _is_sensitive_config_key(key)
        ):
            raise ManifestError(
                f"non-secret config whitelist contains invalid key: {key!r}",
            )
        value = environ.get(key, "")
        if not isinstance(value, str):
            raise ManifestError(
                f"non-secret config {key!r} must resolve to a string",
            )
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("utf-8"))
    return digest.hexdigest()


def compute_canonical_snapshot(
    repo_root: Path | str,
    *,
    canonical_inputs: Sequence[str],
    manifest_path: str,
    runner_roots: Sequence[str],
    runner_dependencies: Sequence[str],
    non_secret_config_keys: Sequence[str],
    environ: Mapping[str, str],
) -> CanonicalSnapshot:
    """Expand tracked canonical inputs and compute all five SHA-256 classes."""

    root = _canonical_repo_root(repo_root)
    patterns = _parse_canonical_paths(
        canonical_inputs,
        "manifest.canonical_inputs",
    )
    dependencies = _parse_canonical_paths(
        runner_dependencies,
        "manifest.runner_dependencies",
    ) if runner_dependencies else ()
    manifest_relative = _parse_canonical_paths(
        (manifest_path,),
        "canonical manifest path",
    )[0]
    runner_root_paths = _parse_canonical_paths(
        runner_roots,
        "canonical runner roots",
    )
    head_before = str(_git(root, ("rev-parse", "HEAD"), text=True)).strip()
    tracked_all = frozenset(_nul_paths(
        bytes(_git(root, ("ls-files", "-z"), text=False)),
        where="git tracked inventory",
    ))
    tracked_inputs = tuple(sorted(
        path
        for path in tracked_all
        if path not in _CANONICAL_REPORT_PATHS
        and _matches_any(path, patterns)
    ))
    if not tracked_inputs:
        raise ManifestError("canonical input expansion is empty")
    if any(_sensitive_canonical_path(relative) for relative in tracked_inputs):
        raise ManifestError("canonical input inventory contains a sensitive path")
    total_size = 0
    for relative in tracked_inputs:
        canonical = _canonical_file(root, relative)
        size = canonical.stat().st_size
        if size > MAX_CANONICAL_INPUT_BYTES:
            raise ManifestError(
                f"canonical input exceeds size limit: {relative}",
            )
        total_size += size
        if total_size > MAX_CANONICAL_TOTAL_BYTES:
            raise ManifestError("canonical input inventory exceeds total size limit")
    if manifest_relative not in tracked_inputs:
        raise ManifestError(
            f"canonical manifest is outside canonical inputs: {manifest_relative}",
        )

    untracked_all = _nul_paths(
        bytes(_git(
            root,
            ("ls-files", "--others", "--exclude-standard", "-z"),
            text=False,
        )),
        where="git untracked inventory",
    )
    untracked_inputs = tuple(sorted(
        path
        for path in untracked_all
        if path not in _CANONICAL_REPORT_PATHS
        and _matches_any(path, patterns)
    ))
    dirty_candidates = set()
    for args in (
        ("diff", "--name-only", "-z", "--"),
        ("diff", "--cached", "--name-only", "-z", "--"),
    ):
        dirty_candidates.update(_nul_paths(
            bytes(_git(root, args, text=False)),
            where="git dirty inventory",
        ))
    dirty_paths = tuple(sorted(
        path for path in dirty_candidates if path in set(tracked_inputs)
    ))

    runner_paths = _runner_import_closure(
        root,
        roots=runner_root_paths,
        dependencies=dependencies,
        tracked=tracked_all,
    )
    if not set(runner_paths).issubset(tracked_inputs):
        outside = sorted(set(runner_paths) - set(tracked_inputs))
        raise ManifestError(
            f"runner dependency is outside canonical inputs: {outside}",
        )
    journey_paths = tuple(
        path
        for path in tracked_inputs
        if path.startswith("test/journeys/")
        and path.endswith((".yaml", ".yml"))
    )
    if not journey_paths:
        raise ManifestError("journey corpus expansion is empty")
    digests = {
        "journey_corpus": _digest_files(root, journey_paths),
        "e2e_manifest": _digest_files(root, (manifest_relative,)),
        "runner": _digest_files(root, runner_paths),
        "tracked_inputs": _digest_files(root, tracked_inputs),
        "non_secret_config": _config_digest(
            non_secret_config_keys,
            environ,
        ),
    }
    head_after = str(_git(root, ("rev-parse", "HEAD"), text=True)).strip()
    tracked_after = frozenset(_nul_paths(
        bytes(_git(root, ("ls-files", "-z"), text=False)),
        where="post-hash git tracked inventory",
    ))
    untracked_after = tuple(sorted(
        path
        for path in _nul_paths(
            bytes(_git(
                root,
                ("ls-files", "--others", "--exclude-standard", "-z"),
                text=False,
            )),
            where="post-hash git untracked inventory",
        )
        if path not in _CANONICAL_REPORT_PATHS
        and _matches_any(path, patterns)
    ))
    dirty_after_candidates: set[str] = set()
    for args in (
        ("diff", "--name-only", "-z", "--"),
        ("diff", "--cached", "--name-only", "-z", "--"),
    ):
        dirty_after_candidates.update(_nul_paths(
            bytes(_git(root, args, text=False)),
            where="post-hash git dirty inventory",
        ))
    dirty_after = tuple(sorted(
        path for path in dirty_after_candidates if path in set(tracked_inputs)
    ))
    if (
        head_before != head_after
        or tracked_all != tracked_after
        or untracked_inputs != untracked_after
        or dirty_paths != dirty_after
    ):
        raise ManifestError("canonical inputs changed during snapshot")
    return CanonicalSnapshot(
        digests=digests,
        tracked_input_count=len(tracked_inputs),
        dirty_paths=dirty_paths,
        untracked_input_paths=untracked_inputs,
        runner_paths=runner_paths,
    )


def canonical_write_reasons(
    state: CanonicalSnapshot,
    *,
    selection: Mapping[str, Any],
    journey_filters: Mapping[str, Any],
    scope: Mapping[str, Any],
    provider: str,
    model: str,
    runtime_before: Mapping[str, Any],
    runtime_after: Mapping[str, Any],
    provider_lock: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return stable reasons that make a run ineligible for canonical replace."""

    reasons: list[str] = []
    expected_selection = {
        "runner_lane": "milestone",
        "runner_group": None,
        "runner_ids": [],
        "full": True,
    }
    if dict(selection) != expected_selection:
        reasons.append("selection_not_canonical")
    expected_filters = {"ids", "suites", "lanes", "levels", "other"}
    if (
        set(journey_filters) != expected_filters
        or any(journey_filters.get(key) for key in expected_filters)
    ):
        reasons.append("journey_filter_active")
    if (
        scope.get("full") is not True
        or type(scope.get("declared")) is not int
        or scope.get("declared") != scope.get("selected")
    ):
        reasons.append("journey_scope_incomplete")
    if state.dirty:
        reasons.append("canonical_inputs_dirty")
    if not provider or not model:
        reasons.append("provider_model_unlocked")
    if (
        provider_lock.get("locked") is not True
        or provider_lock.get("drift_detected") is not False
    ):
        reasons.append("provider_drift")
    runtime_fields = {
        "provider_revision",
        "capability_revision",
        "capability_source",
        "non_secret_config",
    }
    if (
        set(runtime_before) != runtime_fields
        or set(runtime_after) != runtime_fields
        or any(not runtime_before.get(key) for key in runtime_fields)
        or any(not runtime_after.get(key) for key in runtime_fields)
    ):
        reasons.append("runtime_metadata_incomplete")
    elif dict(runtime_before) != dict(runtime_after):
        reasons.append("runtime_revision_drift")
    return tuple(dict.fromkeys(reasons))


def canonical_report_count_reasons(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate selected/executed/pass/fail/skip conservation and summary."""

    counts = payload.get("counts")
    keys = {"selected", "executed", "pass", "fail", "skip"}
    if (
        not isinstance(counts, Mapping)
        or set(counts) != keys
        or any(type(counts.get(key)) is not int or counts[key] < 0 for key in keys)
    ):
        return ("report_counts_invalid",)
    if (
        counts["executed"] != counts["pass"] + counts["fail"]
        or counts["selected"] != counts["executed"] + counts["skip"]
    ):
        return ("report_counts_invalid",)
    expected_summary = (
        f"pass/selected={counts['pass']}/{counts['selected']}; "
        f"fail={counts['fail']}; skip={counts['skip']}"
    )
    scope = payload.get("scope")
    if (
        payload.get("summary") != expected_summary
        or not isinstance(scope, Mapping)
        or scope.get("selected") != counts["selected"]
    ):
        return ("report_counts_invalid",)
    return ()


def canonical_report_detail_reasons(
    payload: Mapping[str, Any],
) -> tuple[str, ...]:
    """Cross-check every report bucket against the journey-detail source rows."""

    rows = payload.get("journeys")
    if not isinstance(rows, list) or any(
        not isinstance(row, Mapping) for row in rows
    ):
        return ("report_journey_details_invalid",)
    normalized: list[Mapping[str, Any]] = list(rows)
    if any(
        not isinstance(row.get("id"), str)
        or not row["id"]
        or row.get("status") not in {"pass", "fail", "skip"}
        or row.get("level") not in {"regression", "target"}
        or not isinstance(row.get("lane"), str)
        or not row["lane"]
        or not isinstance(row.get("suite"), str)
        or not row["suite"]
        or not isinstance(row.get("tags"), list)
        or any(not isinstance(tag, str) or not tag for tag in row.get("tags", ()))
        for row in normalized
    ):
        return ("report_journey_details_invalid",)
    ids = [str(row["id"]) for row in normalized]
    if len(ids) != len(set(ids)):
        return ("report_journey_details_invalid",)

    def summarize(selected: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        counts = {
            "selected": len(selected),
            "executed": sum(1 for row in selected if row["status"] != "skip"),
            "pass": sum(1 for row in selected if row["status"] == "pass"),
            "fail": sum(1 for row in selected if row["status"] == "fail"),
            "skip": sum(1 for row in selected if row["status"] == "skip"),
        }
        counts["summary"] = (
            f"pass/selected={counts['pass']}/{counts['selected']}; "
            f"fail={counts['fail']}; skip={counts['skip']}"
        )
        return counts

    overall = summarize(normalized)
    if payload.get("counts") != {
        key: overall[key]
        for key in ("selected", "executed", "pass", "fail", "skip")
    }:
        return ("report_journey_details_mismatch",)
    expected_levels = {
        level: summarize([
            row for row in normalized if row["level"] == level
        ])
        for level in ("regression", "target")
    }
    if any(payload.get(level) != bucket for level, bucket in expected_levels.items()):
        return ("report_bucket_mismatch",)

    def grouped(field: str) -> dict[str, dict[str, Any]]:
        return {
            value: summarize([
                row for row in normalized if row[field] == value
            ])
            for value in sorted({str(row[field]) for row in normalized})
        }

    if payload.get("lanes") != grouped("lane"):
        return ("report_bucket_mismatch",)
    if payload.get("suites") != grouped("suite"):
        return ("report_bucket_mismatch",)
    expected_scorecard = {
        tag: summarize([
            row for row in normalized if tag in row["tags"]
        ])
        for tag in sorted({
            str(tag)
            for row in normalized
            for tag in row["tags"]
        })
    }
    if payload.get("scorecard") != expected_scorecard:
        return ("report_bucket_mismatch",)
    return ()


def canonical_journey_contract(
    repo_root: Path | str,
) -> dict[str, dict[str, Any]]:
    """Load the tracked canonical corpus independently of the child report."""

    root = _canonical_repo_root(repo_root)
    tracked = _nul_paths(
        bytes(_git(root, ("ls-files", "-z", "--", "test/journeys"), text=False)),
        where="canonical journey inventory",
    )
    paths = tuple(sorted(
        path for path in tracked if path.endswith((".yaml", ".yml"))
    ))
    if not paths:
        raise ManifestError("canonical journey corpus is empty")
    declarations: dict[str, dict[str, Any]] = {}
    for relative in paths:
        try:
            document = strict_yaml_load(
                _canonical_file(root, relative).read_text(encoding="utf-8"),
                where=relative,
            ) or {}
        except (OSError, UnicodeError, yaml.YAMLError, ManifestError) as exc:
            raise ManifestError(
                f"canonical journey corpus cannot be parsed: {relative}",
            ) from exc
        rows = document.get("journeys") if isinstance(document, Mapping) else None
        if not isinstance(rows, list):
            raise ManifestError(
                f"canonical journey corpus has invalid rows: {relative}",
            )
        for row in rows:
            if not isinstance(row, Mapping):
                raise ManifestError(
                    f"canonical journey corpus has invalid row: {relative}",
                )
            journey_id = row.get("id")
            level = row.get("level")
            lane = row.get("lane")
            tags = row.get("tags") or []
            if (
                not isinstance(journey_id, str)
                or not journey_id
                or journey_id in declarations
                or level not in {"regression", "target"}
                or lane not in {"mock", "live"}
                or not isinstance(tags, list)
                or any(not isinstance(tag, str) or not tag for tag in tags)
            ):
                raise ManifestError(
                    f"canonical journey declaration is invalid: {relative}",
                )
            declarations[journey_id] = {
                "level": level,
                "lane": lane,
                "suite": Path(relative).relative_to(
                    "test/journeys",
                ).as_posix(),
                "tags": list(tags),
            }
    if not declarations:
        raise ManifestError("canonical journey corpus declares no journeys")
    protocol_ids = [journey_result_case_id(value) for value in declarations]
    if len(protocol_ids) != len(set(protocol_ids)):
        raise ManifestError("canonical journey ids collide in result protocol")
    return declarations


def journey_result_case_id(journey_id: str) -> str:
    """Mirror the child protocol's deterministic journey case-id mapping."""

    return "journey_" + "".join(
        char.lower() if char.isalnum() else "_"
        for char in journey_id
    ).strip("_")


def _current_sha(root: Path) -> str:
    value = str(_git(root, ("rev-parse", "HEAD"), text=True)).strip()
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ManifestError("current git HEAD is not a full SHA")
    return value


def _is_ancestor(root: Path, ancestor: str) -> bool:
    if re.fullmatch(r"[0-9a-f]{40}", ancestor) is None:
        return False
    try:
        completed = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, "HEAD"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManifestError("git ancestry check is unavailable") from exc
    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise ManifestError("git ancestry check failed")


def evaluate_canonical_freshness(
    repo_root: Path | str,
    *,
    state: CanonicalSnapshot,
    report_path: Path | str,
    markdown_path: Path | str,
    milestone: bool,
    runtime_current: Mapping[str, Any] | None,
) -> CanonicalFreshness:
    """Validate schema-v2 evidence without equating HEAD equality to freshness."""

    root = _canonical_repo_root(repo_root)
    current_sha = _current_sha(root)
    reasons: list[str] = []
    try:
        recover_report_transaction(report_path, markdown_path)
        report_file = _canonical_file(
            root,
            Path(report_path).resolve(strict=True).relative_to(root).as_posix(),
        )
        markdown_file = _canonical_file(
            root,
            Path(markdown_path).resolve(strict=True).relative_to(root).as_posix(),
        )
        payload = strict_json_loads(
            _read_text_bounded(report_file, MAX_CANONICAL_REPORT_BYTES),
        )
        markdown = _read_text_bounded(
            markdown_file,
            MAX_CANONICAL_REPORT_BYTES,
        )
    except (OSError, RuntimeError, ValueError, UnicodeError, json.JSONDecodeError,
            ManifestError):
        return CanonicalFreshness(
            True,
            ("canonical_report_invalid",),
            "unverified",
            current_code_sha=current_sha,
        )
    required = {
        "schema_version",
        "runner_version",
        "run_id",
        "generated_at",
        "code_sha",
        "provider",
        "model",
        "provider_revision",
        "capability_revision",
        "capability_source",
        "provider_lock",
        "selection",
        "scope",
        "canonical_input_state",
        "counts",
        "summary",
        "digests",
        "tracked_input_count",
    }
    if not isinstance(payload, Mapping) or not required.issubset(payload):
        reasons.append("canonical_metadata_missing")
        report_sha = ""
    else:
        report_sha = str(payload.get("code_sha") or "")
        if payload.get("schema_version") != 2:
            reasons.append("canonical_schema_invalid")
        if (
            canonical_report_count_reasons(payload)
            or canonical_report_detail_reasons(payload)
        ):
            reasons.append("report_counts_invalid")
        if payload.get("runner_version") != "1.0":
            reasons.append("canonical_schema_invalid")
        if (
            not isinstance(payload.get("generated_at"), str)
            or re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})",
                payload["generated_at"],
            )
            is None
        ):
            reasons.append("canonical_schema_invalid")
        for field in (
            "provider",
            "model",
            "provider_revision",
            "capability_revision",
            "capability_source",
        ):
            if not isinstance(payload.get(field), str) or not payload[field]:
                reasons.append("canonical_metadata_missing")
        if payload.get("capability_source") not in {
            "bootstrap_static",
            "gateway_rpc",
        }:
            reasons.append("canonical_schema_invalid")
        try:
            ancestor = _is_ancestor(root, report_sha)
        except ManifestError:
            reasons.append("git_ancestry_unavailable")
        else:
            if not ancestor:
                reasons.append("code_sha_not_ancestor")
        report_digests = payload.get("digests")
        if (
            not isinstance(report_digests, Mapping)
            or set(report_digests) != _REQUIRED_DIGESTS | {"algorithm"}
            or report_digests.get("algorithm") != "sha256"
            or {
                key: report_digests.get(key)
                for key in _REQUIRED_DIGESTS
            } != dict(state.digests)
        ):
            reasons.append("canonical_digest_mismatch")
        if (
            type(payload.get("tracked_input_count")) is not int
            or payload["tracked_input_count"] <= 0
            or payload.get("tracked_input_count") != state.tracked_input_count
        ):
            reasons.append("tracked_input_count_mismatch")
        expected_input_state = {
            "dirty": state.dirty,
            "dirty_paths": list(state.dirty_paths),
            "untracked_input_paths": list(state.untracked_input_paths),
        }
        if payload.get("canonical_input_state") != expected_input_state:
            reasons.append("canonical_input_state_mismatch")
        if state.dirty:
            reasons.append("canonical_inputs_dirty")
        if payload.get("selection") != {
            "runner_lane": "milestone",
            "runner_group": None,
            "runner_ids": [],
            "full": True,
        }:
            reasons.append("selection_not_canonical")
        scope = payload.get("scope")
        filters = scope.get("journey_filters") if isinstance(scope, Mapping) else None
        if (
            not isinstance(filters, Mapping)
            or set(filters) != {"ids", "suites", "lanes", "levels", "other"}
            or any(filters.values())
            or scope.get("full") is not True
            or scope.get("declared") != scope.get("selected")
        ):
            reasons.append("journey_scope_incomplete")
        lock = payload.get("provider_lock")
        if (
            not isinstance(lock, Mapping)
            or lock.get("locked") is not True
            or lock.get("drift_detected") is not False
        ):
            reasons.append("provider_drift")
        run_id = payload.get("run_id")
        summary = payload.get("summary")
        if (
            not isinstance(run_id, str)
            or not isinstance(summary, str)
            or run_id not in markdown
            or summary not in markdown
        ):
            reasons.append("report_pair_mismatch")

    runtime_freshness = "unverified"
    if runtime_current is not None and isinstance(payload, Mapping):
        runtime_freshness = "verified"
        runtime_fields = (
            "provider",
            "model",
            "provider_revision",
            "capability_revision",
            "capability_source",
        )
        if any(
            payload.get(field) != runtime_current.get(field)
            for field in runtime_fields
        ) or (
            payload.get("digests", {}).get("non_secret_config")
            != runtime_current.get("non_secret_config")
        ):
            reasons.append("runtime_metadata_mismatch")
            runtime_freshness = "stale"
    elif milestone:
        reasons.append("runtime_unverified")
    return CanonicalFreshness(
        stale=bool(reasons),
        reasons=tuple(dict.fromkeys(reasons)),
        runtime_freshness=runtime_freshness,
        report_code_sha=report_sha,
        current_code_sha=current_sha,
    )


_REPORT_TXN_FILES = {
    "journal": ".journeys-report.transaction.json",
    "journal_next": ".journeys-report.transaction.next",
    "old_json": ".journeys-report.old.json",
    "old_markdown": ".journeys-report.old.md",
    "new_json": ".journeys-report.new.json",
    "new_markdown": ".journeys-report.new.md",
    "restore_json": ".journeys-report.restore.json",
    "restore_markdown": ".journeys-report.restore.md",
}


def _report_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_sync(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _safe_report_destination(path: Path, directory: Path) -> None:
    current = path
    while True:
        if current.is_symlink() or (
            hasattr(current, "is_junction") and current.is_junction()
        ):
            raise ManifestError("report destination cannot use a link or reparse point")
        if current == current.parent:
            break
        current = current.parent
    if path.parent.resolve() != directory:
        raise ManifestError("report destination escapes its transaction directory")


def _read_bounded_report_bytes(
    path: Path | str,
    max_bytes: int = MAX_CANONICAL_REPORT_BYTES,
) -> bytes:
    """Read a regular report file without following links or allocating past limit."""

    report_path = Path(path)
    directory = report_path.parent.resolve()
    _safe_report_destination(report_path, directory)
    try:
        before = os.lstat(report_path)
        if not stat.S_ISREG(before.st_mode):
            raise ManifestError("bounded report input is not a regular file")
        if before.st_size > max_bytes:
            raise ManifestError("bounded report input exceeds size limit")
        with report_path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ManifestError("bounded report input is not a regular file")
            if opened.st_size > max_bytes:
                raise ManifestError("bounded report input exceeds size limit")
            raw = handle.read(max_bytes + 1)
            after = os.fstat(handle.fileno())
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError("bounded report input cannot be read") from exc
    if len(raw) > max_bytes or after.st_size > max_bytes:
        raise ManifestError("bounded report input exceeds size limit")
    if (
        before.st_size != opened.st_size
        or opened.st_size != after.st_size
        or len(raw) != after.st_size
        or (
            before.st_ino
            and opened.st_ino
            and (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        )
    ):
        raise ManifestError("bounded report input changed while reading")
    return raw


def _write_report_journal(directory: Path, payload: Mapping[str, Any]) -> None:
    journal = directory / _REPORT_TXN_FILES["journal"]
    next_path = directory / _REPORT_TXN_FILES["journal_next"]
    content = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    _write_sync(next_path, content)
    os.replace(next_path, journal)
    _fsync_directory(directory)


def _cleanup_report_transaction(directory: Path) -> None:
    for key, filename in _REPORT_TXN_FILES.items():
        if key != "journal":
            (directory / filename).unlink(missing_ok=True)
    _fsync_directory(directory)


def _restore_report_bytes(path: Path, staging: Path, content: bytes) -> None:
    _write_sync(staging, content)
    os.replace(staging, path)


def recover_report_transaction(
    json_path: Path | str,
    markdown_path: Path | str,
) -> None:
    """Recover a crash-interrupted two-file report transaction idempotently."""

    json_file = Path(json_path)
    markdown_file = Path(markdown_path)
    directory = json_file.parent.resolve()
    if markdown_file.parent.resolve() != directory:
        raise ManifestError("canonical report pair must share one directory")
    if not directory.exists():
        # 目录还不存在 ⇒ 这里从来没有过事务，没有东西可恢复。
        #
        # ⚠ 这条分支在 Windows 上**永远走不到**，所以它缺席了很久也没人发现：
        # `_fsync_directory` 对 `os.name == "nt"` 把 **所有** OSError 都吞掉
        # （Windows 没有目录 fd，本该吞的是 EACCES），连「目录不存在」一起吞了。
        # 而 `_promote_canonical_report` 是**先恢复、后建目录**的顺序
        # （`write_report_pair` 才 mkdir），于是 Linux 上首次晋升必然
        # `FileNotFoundError` → 被上游宽 `except OSError` 吞成泛化的
        # `canonical_promotion_failed`，具体理由全部丢失。
        # 修在这里而不是让 `_fsync_directory` 容忍 ENOENT：**事务目录在写入过程中
        # 消失是真事故**，不能一并放行。
        return
    _safe_report_destination(json_file, directory)
    _safe_report_destination(markdown_file, directory)
    journal = directory / _REPORT_TXN_FILES["journal"]
    if not journal.exists():
        if journal.is_symlink():
            raise ManifestError("report transaction journal cannot be a symlink")
        _cleanup_report_transaction(directory)
        return
    if journal.is_symlink() or not journal.is_file():
        raise ManifestError("report transaction journal is invalid")
    try:
        payload = strict_json_loads(
            _read_text_bounded(journal, MAX_REPORT_JOURNAL_BYTES),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("report transaction journal is invalid") from exc
    required = {
        "version",
        "state",
        "json_name",
        "markdown_name",
        "had_old",
        "old_json_sha256",
        "old_markdown_sha256",
        "new_json_sha256",
        "new_markdown_sha256",
        "old_json_bytes",
        "old_markdown_bytes",
        "new_json_bytes",
        "new_markdown_bytes",
    }
    digest_fields = (
        "old_json_sha256",
        "old_markdown_sha256",
        "new_json_sha256",
        "new_markdown_sha256",
    )
    length_fields = (
        "old_json_bytes",
        "old_markdown_bytes",
        "new_json_bytes",
        "new_markdown_bytes",
    )
    if (
        not isinstance(payload, Mapping)
        or set(payload) != required
        or payload.get("version") != 1
        or payload.get("state") not in {"prepared", "committed"}
        or payload.get("json_name") != json_file.name
        or payload.get("markdown_name") != markdown_file.name
        or type(payload.get("had_old")) is not bool
        or any(
            not isinstance(payload.get(field), str)
            or re.fullmatch(r"[0-9a-f]{64}", payload[field]) is None
            for field in digest_fields
        )
        or any(
            type(payload.get(field)) is not int
            or payload[field] < 0
            for field in length_fields
        )
    ):
        raise ManifestError("report transaction journal is invalid")
    if any(
        payload[field] > MAX_CANONICAL_REPORT_BYTES
        for field in length_fields
    ):
        raise ManifestError("report transaction content exceeds size limit")
    if not payload["had_old"] and (
        payload["old_json_bytes"] != 0
        or payload["old_markdown_bytes"] != 0
        or payload["old_json_sha256"] != _report_digest(b"")
        or payload["old_markdown_sha256"] != _report_digest(b"")
    ):
        raise ManifestError("report transaction journal is invalid")

    committed = payload["state"] == "committed"
    if committed and json_file.is_file() and markdown_file.is_file():
        current_json = _read_bounded_report_bytes(json_file)
        current_markdown = _read_bounded_report_bytes(markdown_file)
        if (
            len(current_json) == payload["new_json_bytes"]
            and len(current_markdown) == payload["new_markdown_bytes"]
            and _report_digest(current_json) == payload["new_json_sha256"]
            and _report_digest(current_markdown) == payload["new_markdown_sha256"]
        ):
            journal.unlink()
            _fsync_directory(directory)
            _cleanup_report_transaction(directory)
            return

    if payload["had_old"]:
        old_json_path = directory / _REPORT_TXN_FILES["old_json"]
        old_markdown_path = directory / _REPORT_TXN_FILES["old_markdown"]
        if (
            old_json_path.is_symlink()
            or old_markdown_path.is_symlink()
            or not old_json_path.is_file()
            or not old_markdown_path.is_file()
        ):
            raise ManifestError("report transaction backup is invalid")
        old_json = _read_bounded_report_bytes(old_json_path)
        old_markdown = _read_bounded_report_bytes(old_markdown_path)
        if (
            len(old_json) != payload["old_json_bytes"]
            or len(old_markdown) != payload["old_markdown_bytes"]
            or _report_digest(old_json) != payload["old_json_sha256"]
            or _report_digest(old_markdown) != payload["old_markdown_sha256"]
        ):
            raise ManifestError("report transaction backup is invalid")
        _restore_report_bytes(
            json_file,
            directory / _REPORT_TXN_FILES["restore_json"],
            old_json,
        )
        _restore_report_bytes(
            markdown_file,
            directory / _REPORT_TXN_FILES["restore_markdown"],
            old_markdown,
        )
    else:
        json_file.unlink(missing_ok=True)
        markdown_file.unlink(missing_ok=True)
    _fsync_directory(directory)
    journal.unlink()
    _fsync_directory(directory)
    _cleanup_report_transaction(directory)


def atomic_write_report_bytes_pair(
    json_path: Path | str,
    markdown_path: Path | str,
    json_bytes: bytes,
    markdown_bytes: bytes,
) -> None:
    """Atomically replace an exact report pair with rollback on partial failure."""

    if (
        len(json_bytes) > MAX_CANONICAL_REPORT_BYTES
        or len(markdown_bytes) > MAX_CANONICAL_REPORT_BYTES
    ):
        raise ManifestError("new report content exceeds size limit")
    json_file = Path(json_path)
    markdown_file = Path(markdown_path)
    if json_file.parent.resolve() != markdown_file.parent.resolve():
        raise ManifestError("canonical report pair must share one directory")
    directory = json_file.parent.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    recover_report_transaction(json_file, markdown_file)
    _safe_report_destination(json_file, directory)
    _safe_report_destination(markdown_file, directory)
    if json_file.exists() != markdown_file.exists():
        raise ManifestError("existing report pair is incomplete")
    old_json = (
        _read_bounded_report_bytes(json_file)
        if json_file.is_file()
        else None
    )
    old_markdown = (
        _read_bounded_report_bytes(markdown_file)
        if markdown_file.is_file()
        else None
    )
    had_old = old_json is not None
    journal_payload = {
        "version": 1,
        "state": "prepared",
        "json_name": json_file.name,
        "markdown_name": markdown_file.name,
        "had_old": had_old,
        "old_json_sha256": _report_digest(old_json or b""),
        "old_markdown_sha256": _report_digest(old_markdown or b""),
        "new_json_sha256": _report_digest(json_bytes),
        "new_markdown_sha256": _report_digest(markdown_bytes),
        "old_json_bytes": len(old_json or b""),
        "old_markdown_bytes": len(old_markdown or b""),
        "new_json_bytes": len(json_bytes),
        "new_markdown_bytes": len(markdown_bytes),
    }
    try:
        if had_old:
            _write_sync(directory / _REPORT_TXN_FILES["old_json"], old_json)
            _write_sync(
                directory / _REPORT_TXN_FILES["old_markdown"],
                old_markdown,
            )
        _write_sync(directory / _REPORT_TXN_FILES["new_json"], json_bytes)
        _write_sync(
            directory / _REPORT_TXN_FILES["new_markdown"],
            markdown_bytes,
        )
        _write_report_journal(directory, journal_payload)
        os.replace(directory / _REPORT_TXN_FILES["new_json"], json_file)
        _fsync_directory(directory)
        os.replace(
            directory / _REPORT_TXN_FILES["new_markdown"],
            markdown_file,
        )
        _fsync_directory(directory)
        _write_report_journal(
            directory,
            {**journal_payload, "state": "committed"},
        )
        recover_report_transaction(json_file, markdown_file)
    except BaseException:
        if (directory / _REPORT_TXN_FILES["journal"]).is_file():
            recover_report_transaction(json_file, markdown_file)
        raise


def atomic_write_report_pair(
    json_path: Path | str,
    markdown_path: Path | str,
    payload: Mapping[str, Any],
    markdown: str,
) -> None:
    """Serialize and atomically replace a canonical JSON/Markdown pair."""

    json_text = json.dumps(
        dict(payload),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    atomic_write_report_bytes_pair(
        json_path,
        markdown_path,
        json_text.encode("utf-8"),
        markdown.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Dynamic architecture guard
# ---------------------------------------------------------------------------

_ARCH_EXCLUDED_DIRS = frozenset({
    ".git",
    ".worktrees",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "gen",
    "test",
    "tests",
})
_ARCH_TARGETS: Mapping[str, tuple[str, ...] | None] = {
    "orchestrator/cloud/verify.py": None,
    "orchestrator/cloud/executor.py": (
        "_verify_outcome",
        "_evaluate",
        "_should_report",
    ),
    "proactive/governor.py": None,
    "llm-gateway/s2s/session.py": None,
}
_INTENT_IN_TEXT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"([a-z][a-z0-9_-]*\.[a-z][a-z0-9_.-]*)"
    r"(?![A-Za-z0-9_-])",
)
_IDENTIFIER_TOKEN_RE = re.compile(
    r"[A-Z]+(?=[A-Z][a-z]|[0-9]|\Z)|[A-Z]?[a-z]+|[0-9]+",
)
_ARCH_IDENTIFIER_ROLE_TOKENS = frozenset({
    "action",
    "agent",
    "capability",
    "command",
    "domain",
    "intent",
    "kind",
    "object",
    "route",
    "type",
})


def _identifier_tokens(value: str) -> tuple[str, ...]:
    """Split snake/kebab/camel identifiers into comparable lowercase tokens."""

    tokens: list[str] = []
    for part in re.split(r"[_-]+", value):
        if not part:
            continue
        found = _IDENTIFIER_TOKEN_RE.findall(part)
        tokens.extend(item.lower() for item in (found or [part]))
    return tuple(tokens)


def _has_contiguous_tokens(
    identifier: tuple[str, ...],
    candidate: tuple[str, ...],
) -> bool:
    if not candidate or len(candidate) > len(identifier):
        return False
    width = len(candidate)
    return any(
        identifier[index:index + width] == candidate
        for index in range(len(identifier) - width + 1)
    )


def _arch_root(repo_root: Path | str) -> Path:
    try:
        root = _resolve_path(Path(repo_root))
    except (OSError, RuntimeError) as exc:
        raise ArchitectureGuardError(
            f"architecture repository root does not resolve: {repo_root}",
        ) from exc
    if not root.is_dir():
        raise ArchitectureGuardError(
            f"architecture repository root is not a directory: {root}",
        )
    return root


def _arch_file(root: Path, relative: str) -> Path:
    candidate = root / relative
    try:
        path = _resolve_path(candidate)
        bounded = path.relative_to(root)
    except ValueError as exc:
        raise ArchitectureGuardError(
            f"architecture source escapes repository root: {relative}",
        ) from exc
    except (OSError, RuntimeError) as exc:
        raise ArchitectureGuardError(
            f"architecture source {relative} does not resolve",
        ) from exc
    if not path.is_file():
        raise ArchitectureGuardError(
            f"architecture source {relative} is not a file",
        )
    if bounded.as_posix() != relative:
        # A link which stays in the repository is still ambiguous: diagnostics
        # and review ownership must name the declared source, not an alias.
        raise ArchitectureGuardError(
            f"architecture source {relative} resolves to another path: "
            f"{bounded.as_posix()}",
        )
    return path


def _arch_glob(root: Path, pattern: str) -> tuple[Path, ...]:
    paths: dict[str, Path] = {}
    for candidate in root.glob(pattern):
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:  # pragma: no cover - Path.glob is rooted
            raise ArchitectureGuardError(
                f"architecture glob escaped repository root: {candidate}",
            ) from exc
        if any(part in _ARCH_EXCLUDED_DIRS for part in relative.parts[:-1]):
            continue
        if candidate.is_dir():
            continue
        try:
            resolved = _resolve_path(candidate)
            bounded = resolved.relative_to(root)
        except ValueError as exc:
            raise ArchitectureGuardError(
                f"architecture source escapes repository root: {relative.as_posix()}",
            ) from exc
        except (OSError, RuntimeError) as exc:
            raise ArchitectureGuardError(
                f"architecture source {relative.as_posix()} does not resolve",
            ) from exc
        if not resolved.is_file():
            continue
        if bounded != relative:
            raise ArchitectureGuardError(
                f"architecture source alias is not allowed: {relative.as_posix()}",
            )
        paths[relative.as_posix()] = resolved
    return tuple(paths[key] for key in sorted(paths))


def _arch_read_text(root: Path, path: Path) -> str:
    try:
        path.relative_to(root)
        return path.read_text(encoding="utf-8")
    except ValueError as exc:
        raise ArchitectureGuardError(
            f"architecture source escapes repository root: {path}",
        ) from exc
    except (OSError, UnicodeError) as exc:
        relative = path.relative_to(root).as_posix()
        raise ArchitectureGuardError(
            f"cannot read architecture source {relative}: {exc}",
        ) from exc


def _arch_parse_python(root: Path, path: Path) -> tuple[str, ast.Module]:
    source = _arch_read_text(root, path)
    relative = path.relative_to(root).as_posix()
    try:
        return source, ast.parse(source, filename=relative)
    except SyntaxError as exc:
        raise ArchitectureGuardError(
            f"{relative}:{exc.lineno or 0}:{exc.offset or 0}: "
            f"cannot parse guarded Python: {exc.msg}",
        ) from exc


def _arch_load_yaml(root: Path, path: Path) -> Any:
    relative = path.relative_to(root).as_posix()
    try:
        return yaml.load(
            _arch_read_text(root, path),
            Loader=_UniqueKeyLoader,
        )
    except ArchitectureGuardError:
        raise
    except ManifestError as exc:
        raise ArchitectureGuardError(
            f"cannot parse architecture YAML {relative}: {exc}",
        ) from exc
    except yaml.YAMLError as exc:
        raise ArchitectureGuardError(
            f"cannot parse architecture YAML {relative}: {exc}",
        ) from exc


def _arch_static_intent(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not _INTENT_IN_TEXT_RE.fullmatch(value):
        raise ArchitectureGuardError(
            f"{where} must be a static <domain>.<action> intent",
        )
    return value


def _add_intent_terms(terms: set[str], intent: str) -> None:
    terms.add(intent)
    terms.add(intent.split(".", 1)[0])


def _walk_scalar_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _walk_scalar_strings(item)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for item in value:
            yield from _walk_scalar_strings(item)


def _manifest_domain_terms(root: Path) -> set[str]:
    manifests = _arch_glob(root, "agents/*/manifest.yaml")
    if not manifests:
        raise ArchitectureGuardError(
            "agents/*/manifest.yaml selection is empty",
        )
    terms: set[str] = set()
    for path in manifests:
        relative = path.relative_to(root).as_posix()
        raw = _arch_load_yaml(root, path)
        if not isinstance(raw, Mapping):
            raise ArchitectureGuardError(f"{relative} must be a mapping")
        agent_id = raw.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ArchitectureGuardError(f"{relative}.agent_id must be a string")
        terms.add(agent_id.strip())
        for field in ("capabilities", "route_hints"):
            values = raw.get(field, [])
            if field == "route_hints" and values is None:
                values = []
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
                raise ArchitectureGuardError(f"{relative}.{field} must be a list")
            for index, item in enumerate(values):
                if not isinstance(item, Mapping) or "intent" not in item:
                    if field == "capabilities":
                        raise ArchitectureGuardError(
                            f"{relative}.{field}[{index}] must declare intent",
                        )
                    continue
                intent = _arch_static_intent(
                    item["intent"],
                    where=f"{relative}.{field}[{index}].intent",
                )
                _add_intent_terms(terms, intent)
    return terms


def _commands_domain_terms(root: Path) -> set[str]:
    path = _arch_file(root, "orchestrator/edge/knowledge/commands.yaml")
    raw = _arch_load_yaml(root, path)
    objects = raw.get("objects") if isinstance(raw, Mapping) else None
    if not isinstance(objects, Mapping) or not objects:
        raise ArchitectureGuardError(
            "orchestrator/edge/knowledge/commands.yaml.objects must be a mapping",
        )
    terms: set[str] = set()
    for value in objects:
        if not isinstance(value, str) or not value.strip():
            raise ArchitectureGuardError("commands object names must be strings")
        terms.add(value.strip())
    return terms


def _skills_domain_terms(root: Path) -> set[str]:
    paths = _arch_glob(root, "skills/**/*.yaml")
    if not paths:
        raise ArchitectureGuardError("skills/**/*.yaml selection is empty")
    terms: set[str] = set()
    for path in paths:
        raw = _arch_load_yaml(root, path)
        for text in _walk_scalar_strings(raw):
            for match in _INTENT_IN_TEXT_RE.finditer(text):
                _add_intent_terms(terms, match.group(1))
    return terms


@dataclass(frozen=True)
class _ArchUnknown:
    node: ast.AST
    reason: str


def _match_is_exhaustive(node: ast.Match) -> bool:
    return any(
        case.guard is None
        and isinstance(case.pattern, ast.MatchAs)
        and case.pattern.pattern is None
        for case in node.cases
    )


def _block_contains_line(statements: Sequence[ast.stmt], line: int) -> bool:
    return bool(statements) and (
        statements[0].lineno
        <= line
        <= getattr(statements[-1], "end_lineno", statements[-1].lineno)
    )


def _flow_reaching_definitions(
    statements: Sequence[ast.stmt],
    incoming: tuple[Any, ...],
    *,
    before_line: int,
    definition,
) -> tuple[Any, ...]:
    """Conservatively resolve values reaching one lexical program point."""

    def dedupe(values: Iterable[Any]) -> tuple[Any, ...]:
        unique: dict[tuple[str, Any], Any] = {}
        for value in values:
            marker = (
                ("value", value)
                if isinstance(value, str)
                else ("node", id(value))
            )
            unique.setdefault(marker, value)
        return tuple(unique.values())

    def flow(
        block: Sequence[ast.stmt],
        state: tuple[Any, ...],
        limit: int,
    ) -> tuple[Any, ...]:
        for statement in block:
            if statement.lineno >= limit:
                break
            value = definition(statement)
            if value is not None:
                state = (value,)
                continue
            complete = (
                getattr(statement, "end_lineno", statement.lineno) < limit
            )
            if isinstance(statement, ast.If):
                if complete:
                    body = flow(statement.body, state, 1 << 30)
                    otherwise = (
                        flow(statement.orelse, state, 1 << 30)
                        if statement.orelse
                        else state
                    )
                    state = dedupe((*body, *otherwise))
                else:
                    selected = next((
                        branch
                        for branch in (statement.body, statement.orelse)
                        if _block_contains_line(branch, limit)
                    ), None)
                    if selected is not None:
                        state = flow(selected, state, limit)
                continue
            if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                body = flow(statement.body, state, 1 << 30)
                loop_exit = dedupe((*state, *body))
                if complete:
                    state = (
                        flow(statement.orelse, loop_exit, 1 << 30)
                        if statement.orelse
                        else loop_exit
                    )
                elif _block_contains_line(statement.body, limit):
                    state = flow(statement.body, state, limit)
                elif _block_contains_line(statement.orelse, limit):
                    state = flow(statement.orelse, loop_exit, limit)
                continue
            if isinstance(statement, (ast.With, ast.AsyncWith)):
                state = flow(
                    statement.body,
                    state,
                    1 << 30 if complete else limit,
                )
                continue
            if isinstance(statement, ast.Try):
                body = flow(statement.body, state, 1 << 30)
                handler_input = dedupe((*state, *body))
                handlers = [
                    flow(handler.body, handler_input, 1 << 30)
                    for handler in statement.handlers
                ]
                success = (
                    flow(statement.orelse, body, 1 << 30)
                    if statement.orelse
                    else body
                )
                merged = dedupe(
                    value
                    for path in [success, *handlers]
                    for value in path
                )
                if complete:
                    state = (
                        flow(statement.finalbody, merged, 1 << 30)
                        if statement.finalbody
                        else merged
                    )
                elif _block_contains_line(statement.body, limit):
                    state = flow(statement.body, state, limit)
                else:
                    selected_handler = next((
                        handler
                        for handler in statement.handlers
                        if _block_contains_line(handler.body, limit)
                    ), None)
                    if selected_handler is not None:
                        state = flow(
                            selected_handler.body,
                            handler_input,
                            limit,
                        )
                    elif _block_contains_line(statement.orelse, limit):
                        state = flow(statement.orelse, body, limit)
                    elif _block_contains_line(statement.finalbody, limit):
                        state = flow(statement.finalbody, merged, limit)
                continue
            if isinstance(statement, ast.Match):
                if complete:
                    paths = [
                        flow(case.body, state, 1 << 30)
                        for case in statement.cases
                    ]
                    base = () if _match_is_exhaustive(statement) else state
                    state = dedupe(
                        (*base, *(
                            value for path in paths for value in path
                        ))
                    )
                else:
                    selected = next((
                        case.body
                        for case in statement.cases
                        if _block_contains_line(case.body, limit)
                    ), None)
                    if selected is not None:
                        state = flow(selected, state, limit)
        return state

    return flow(statements, incoming, before_line)


@dataclass(frozen=True)
class _ResolvedSymbol:
    symbol: str
    bound: bool = False
    receiver: ast.Call | None = None


@dataclass(frozen=True)
class _FunctionInfo:
    symbol: str
    module_name: str
    node: ast.FunctionDef | ast.AsyncFunctionDef
    params: tuple[str, ...]
    defaults: tuple[tuple[str, ast.AST], ...]
    class_symbol: str


@dataclass(frozen=True)
class _CallSite:
    module_name: str
    function_symbol: str
    call: ast.Call
    bound: bool
    receiver: ast.Call | None = None


class _GraphModule:
    """One bounded Python module in the proactive reverse-call graph."""

    def __init__(self, root: Path, path: Path, source: str, tree: ast.Module):
        self.root = root
        self.path = path
        self.source = source
        self.tree = tree
        self.relative = path.relative_to(root).as_posix()
        stem = self.relative[:-3].replace("/", ".")
        self.module_name = (
            stem[:-9] if stem.endswith(".__init__") else stem
        )
        self.parents: dict[ast.AST, ast.AST] = {}
        self.function_symbols: dict[ast.AST, str] = {}
        self.class_symbols: dict[ast.AST, str] = {}
        self.module_values: dict[str, ast.AST] = {}
        self.imports: dict[str, str] = {}
        self.local_imports: dict[
            tuple[str, str],
            list[tuple[int, str]],
        ] = {}
        self._index()

    @staticmethod
    def _params(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[str, ...]:
        return tuple(
            item.arg
            for item in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
        )

    @staticmethod
    def _defaults(
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> tuple[tuple[str, ast.AST], ...]:
        positional = list(node.args.posonlyargs) + list(node.args.args)
        positional_defaults = zip(
            positional[len(positional) - len(node.args.defaults):],
            node.args.defaults,
        )
        keyword_defaults = (
            (argument, value)
            for argument, value in zip(
                node.args.kwonlyargs,
                node.args.kw_defaults,
            )
            if value is not None
        )
        return tuple(
            (argument.arg, value)
            for argument, value in (*positional_defaults, *keyword_defaults)
        )

    def _qualname(self, node: ast.AST) -> str:
        names: list[str] = []
        current: ast.AST | None = node
        while current is not None and not isinstance(current, ast.Module):
            if isinstance(
                current,
                (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                names.append(current.name)
            current = self.parents.get(current)
        return ".".join(reversed(names))

    def _absolute_import(self, node: ast.ImportFrom) -> str:
        if not node.level:
            return node.module or ""
        package = (
            self.module_name
            if self.relative.endswith("/__init__.py")
            else self.module_name.rpartition(".")[0]
        )
        try:
            return importlib.util.resolve_name(
                "." * node.level + (node.module or ""),
                package,
            )
        except (ImportError, ValueError) as exc:
            raise ArchitectureGuardError(
                f"{self.relative}:{node.lineno}:{node.col_offset + 1}: "
                f"cannot resolve static import",
            ) from exc

    def _index(self) -> None:
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                self.class_symbols[node] = (
                    f"{self.module_name}.{self._qualname(node)}"
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function_symbols[node] = (
                    f"{self.module_name}.{self._qualname(node)}"
                )
        for node in ast.walk(self.tree):
            function_symbol = self.enclosing_function(node)
            if isinstance(node, ast.ImportFrom):
                base = self._absolute_import(node)
                for alias in node.names:
                    local = alias.asname or alias.name
                    target = f"{base}.{alias.name}" if base else alias.name
                    if function_symbol:
                        self.local_imports.setdefault(
                            (function_symbol, local),
                            [],
                        ).append((node.lineno, target))
                    else:
                        self.imports[local] = target
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    target = alias.name if alias.asname else local
                    if function_symbol:
                        self.local_imports.setdefault(
                            (function_symbol, local),
                            [],
                        ).append((node.lineno, target))
                    else:
                        self.imports[local] = target
        for statement in self.tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            value = statement.value
            if value is None:
                continue
            targets = (
                statement.targets
                if isinstance(statement, ast.Assign)
                else [statement.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    self.module_values[target.id] = value

    def enclosing_function(self, node: ast.AST) -> str:
        current = node
        while current in self.parents:
            current = self.parents[current]
            symbol = self.function_symbols.get(current)
            if symbol:
                return symbol
        return ""

    def enclosing_class(self, node: ast.AST) -> str:
        current = node
        while current in self.parents:
            current = self.parents[current]
            symbol = self.class_symbols.get(current)
            if symbol:
                return symbol
        return ""

    @staticmethod
    def _direct_nodes(
        function: ast.FunctionDef | ast.AsyncFunctionDef,
        kind: type[ast.AST],
    ) -> tuple[ast.AST, ...]:
        found: list[ast.AST] = []

        class Visitor(ast.NodeVisitor):
            def generic_visit(self, node: ast.AST) -> None:
                if isinstance(node, kind):
                    found.append(node)
                super().generic_visit(node)

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                if node is function:
                    self.generic_visit(node)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                if node is function:
                    self.generic_visit(node)

            def visit_Lambda(self, node: ast.Lambda) -> None:
                return None

        Visitor().visit(function)
        return tuple(found)

    def calls_in(self, function: _FunctionInfo) -> tuple[ast.Call, ...]:
        return tuple(
            node
            for node in self._direct_nodes(function.node, ast.Call)
            if isinstance(node, ast.Call)
        )

    def returns_in(self, function: _FunctionInfo) -> tuple[ast.Return, ...]:
        return tuple(
            node
            for node in self._direct_nodes(function.node, ast.Return)
            if isinstance(node, ast.Return)
        )


class _ProactiveCallGraph:
    """Resolve payload types backwards from the one runtime proactive sink."""

    _TRUE_SINK = "runtime.proactive.publish_proactive"

    def __init__(self, root: Path):
        self.root = root
        self.modules: dict[str, _GraphModule] = {}
        self.functions: dict[str, _FunctionInfo] = {}
        self.classes: set[str] = set()
        self.callsites: dict[str, list[_CallSite]] = {}
        self.constructor_sites: dict[str, list[_CallSite]] = {}
        self.summaries: dict[str, set[int]] = {}
        self.constructor_attr_params: dict[tuple[str, str], str] = {}
        self._load()
        self._index_callsites()
        self._index_constructor_attrs()
        self._solve_sinks()

    def _load(self) -> None:
        for path in _arch_glob(self.root, "**/*.py"):
            source, tree = _arch_parse_python(self.root, path)
            module = _GraphModule(self.root, path, source, tree)
            self.modules[module.module_name] = module
        for module in self.modules.values():
            for node, symbol in module.class_symbols.items():
                self.classes.add(symbol)
            for node, symbol in module.function_symbols.items():
                class_symbol = module.enclosing_class(node)
                self.functions[symbol] = _FunctionInfo(
                    symbol=symbol,
                    module_name=module.module_name,
                    node=node,
                    params=module._params(node),
                    defaults=module._defaults(node),
                    class_symbol=class_symbol,
                )

    @staticmethod
    def _attribute_chain(node: ast.Attribute) -> tuple[ast.AST, tuple[str, ...]]:
        attrs: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.Attribute):
            attrs.append(current.attr)
            current = current.value
        return current, tuple(reversed(attrs))

    def _local_definition(
        self,
        module: _GraphModule,
        function_symbol: str,
        name: str,
    ) -> _ResolvedSymbol | None:
        candidates: list[str] = []
        if function_symbol:
            candidates.append(f"{function_symbol}.{name}")
            info = self.functions.get(function_symbol)
            if info and info.class_symbol:
                candidates.append(f"{info.class_symbol}.{name}")
        candidates.append(f"{module.module_name}.{name}")
        for candidate in candidates:
            if candidate in self.functions:
                return _ResolvedSymbol(candidate)
            if candidate in self.classes:
                return _ResolvedSymbol(candidate, bound=True)
        return None

    def _import_targets(
        self,
        module: _GraphModule,
        function_symbol: str,
        name: str,
        *,
        before_line: int,
    ) -> tuple[str, ...]:
        initial = (
            (module.imports[name],)
            if name in module.imports
            else ()
        )
        function = self.functions.get(function_symbol)
        if function is None:
            return initial

        def imported(statement: ast.stmt) -> str | None:
            if isinstance(statement, ast.ImportFrom):
                base = module._absolute_import(statement)
                for alias in statement.names:
                    if (alias.asname or alias.name) == name:
                        return (
                            f"{base}.{alias.name}" if base else alias.name
                        )
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    local = alias.asname or alias.name.split(".", 1)[0]
                    if local == name:
                        return alias.name if alias.asname else local
            return None

        return _flow_reaching_definitions(
            function.node.body,
            initial,
            before_line=before_line,
            definition=imported,
        )

    @staticmethod
    def _bind_expression(
        expression: ast.AST,
        binding: Mapping[str, ast.AST],
    ) -> ast.AST:
        if isinstance(expression, ast.Name) and expression.id in binding:
            return binding[expression.id]
        if isinstance(expression, ast.Call):
            result = ast.Call(
                func=expression.func,
                args=[
                    _ProactiveCallGraph._bind_expression(item, binding)
                    for item in expression.args
                ],
                keywords=[
                    ast.keyword(
                        arg=item.arg,
                        value=_ProactiveCallGraph._bind_expression(
                            item.value,
                            binding,
                        ),
                    )
                    for item in expression.keywords
                ],
            )
            return ast.copy_location(result, expression)
        return expression

    def _constructor_calls(
        self,
        module: _GraphModule,
        function_symbol: str,
        expression: ast.Call,
        seen: frozenset[str] = frozenset(),
    ) -> tuple[tuple[str, ast.Call], ...]:
        use = self._resolve_symbol(module, function_symbol, expression.func)
        if not use:
            return ()
        if use.symbol in self.classes:
            return ((use.symbol, expression),)
        if use.symbol not in self.functions or use.symbol in seen:
            return ()
        factory = self.functions[use.symbol]
        binding = self._bind(factory, expression, bound=use.bound)
        factory_module = self.modules[factory.module_name]
        constructors: list[tuple[str, ast.Call]] = []
        for returned in factory_module.returns_in(factory):
            if returned.value is None:
                continue
            value = returned.value
            if isinstance(value, ast.Name):
                assigned = self._local_assignment(
                    factory_module,
                    factory.symbol,
                    value.id,
                    before_line=returned.lineno,
                )
                if assigned is not None:
                    value = assigned
            value = self._bind_expression(value, binding)
            if isinstance(value, ast.Call):
                constructors.extend(
                    self._constructor_calls(
                        factory_module,
                        factory.symbol,
                        value,
                        seen | {use.symbol},
                    )
                )
        return tuple(constructors)

    def _resolve_symbol(
        self,
        module: _GraphModule,
        function_symbol: str,
        expression: ast.AST,
    ) -> _ResolvedSymbol | None:
        if isinstance(expression, ast.Name):
            targets = self._import_targets(
                module,
                function_symbol,
                expression.id,
                before_line=getattr(expression, "lineno", 1 << 30),
            )
            if len(targets) == 1:
                return _ResolvedSymbol(targets[0])
            return self._local_definition(
                module,
                function_symbol,
                expression.id,
            )
        if not isinstance(expression, ast.Attribute):
            return None
        base, attrs = self._attribute_chain(expression)
        if isinstance(base, ast.Name) and base.id in {"self", "cls"}:
            info = self.functions.get(function_symbol)
            if not info or not info.class_symbol:
                return None
            candidate = ".".join((info.class_symbol, *attrs))
            if candidate in self.functions:
                return _ResolvedSymbol(candidate, bound=True)
            return None
        if isinstance(base, ast.Name):
            imported = self._import_targets(
                module,
                function_symbol,
                base.id,
                before_line=getattr(expression, "lineno", 1 << 30),
            )
            if len(imported) == 1:
                return _ResolvedSymbol(".".join((imported[0], *attrs)))
            local = self._local_definition(module, function_symbol, base.id)
            if local and local.symbol in self.classes:
                return _ResolvedSymbol(
                    ".".join((local.symbol, *attrs)),
                )
            assigned = self._local_assignment(
                module,
                function_symbol,
                base.id,
                before_line=getattr(expression, "lineno", 1 << 30),
            )
            if isinstance(assigned, ast.Call):
                constructors = self._constructor_calls(
                    module,
                    function_symbol,
                    assigned,
                )
                if len(constructors) == 1:
                    class_symbol, receiver = constructors[0]
                    candidate = ".".join((class_symbol, *attrs))
                    if candidate in self.functions:
                        return _ResolvedSymbol(
                            candidate,
                            bound=True,
                            receiver=receiver,
                        )
        return None

    def _index_callsites(self) -> None:
        for module in self.modules.values():
            for call in (
                node for node in ast.walk(module.tree) if isinstance(node, ast.Call)
            ):
                function_symbol = module.enclosing_function(call)
                use = self._resolve_symbol(module, function_symbol, call.func)
                if not use:
                    continue
                site = _CallSite(
                    module_name=module.module_name,
                    function_symbol=function_symbol,
                    call=call,
                    bound=use.bound,
                    receiver=use.receiver,
                )
                if use.symbol in self.functions:
                    self.callsites.setdefault(use.symbol, []).append(site)
                elif use.symbol in self.classes:
                    self.constructor_sites.setdefault(use.symbol, []).append(site)

    def _index_constructor_attrs(self) -> None:
        for function in self.functions.values():
            if function.node.name != "__init__" or not function.class_symbol:
                continue
            module = self.modules[function.module_name]
            for assignment in module._direct_nodes(function.node, ast.Assign):
                if (
                    not isinstance(assignment, ast.Assign)
                    or not isinstance(assignment.value, ast.Name)
                    or assignment.value.id not in function.params
                ):
                    continue
                for target in assignment.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        self.constructor_attr_params[
                            (function.class_symbol, target.attr)
                        ] = assignment.value.id

    def _bind(
        self,
        function: _FunctionInfo,
        call: ast.Call,
        *,
        bound: bool,
    ) -> dict[str, ast.AST]:
        values = dict(function.defaults)
        offset = (
            1
            if bound
            and function.params
            and function.params[0] in {"self", "cls"}
            else 0
        )
        for index, item in enumerate(call.args):
            target = index + offset
            if target < len(function.params):
                values[function.params[target]] = item
        for item in call.keywords:
            if item.arg:
                values[item.arg] = item.value
        return values

    def _function_parameter(
        self,
        function_symbol: str,
        name: str,
    ) -> int | None:
        function = self.functions.get(function_symbol)
        if not function:
            return None
        try:
            return function.params.index(name)
        except ValueError:
            return None

    @staticmethod
    def _assignment_value(statement: ast.stmt, name: str) -> ast.AST | None:
        def may_change_type(expression: ast.AST) -> bool:
            if not isinstance(expression, ast.Dict):
                return True
            keys = [
                key.value
                for key in expression.keys
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)
            ]
            return len(keys) != len(expression.keys) or "type" in keys

        if isinstance(statement, ast.Assign):
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets
            ):
                return statement.value
            if any(
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id == name
                and (
                    not isinstance(target.slice, ast.Constant)
                    or target.slice.value == "type"
                )
                for target in statement.targets
            ):
                return statement
        if (
            isinstance(statement, ast.AnnAssign)
            and statement.value is not None
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            return statement.value
        if (
            isinstance(statement, ast.AugAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == name
        ):
            if (
                isinstance(statement.op, ast.BitOr)
                and not may_change_type(statement.value)
            ):
                return None
            return statement
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and isinstance(statement.value.func.value, ast.Name)
            and statement.value.func.value.id == name
            and statement.value.func.attr == "update"
        ):
            call = statement.value
            if (
                len(call.args) == 1
                and not call.keywords
                and not may_change_type(call.args[0])
            ):
                return None
            return call
        return None

    @staticmethod
    def _dedupe_nodes(values: Iterable[ast.AST]) -> tuple[ast.AST, ...]:
        unique: dict[int, ast.AST] = {}
        for value in values:
            unique.setdefault(id(value), value)
        return tuple(unique.values())

    def _local_assignments(
        self,
        module: _GraphModule,
        function_symbol: str,
        name: str,
        *,
        before_line: int,
    ) -> tuple[ast.AST, ...]:
        function = self.functions.get(function_symbol)
        resolver = lambda statement: self._assignment_value(statement, name)
        if function is None:
            return _flow_reaching_definitions(
                module.tree.body,
                (),
                before_line=before_line,
                definition=resolver,
            )
        module_values = _flow_reaching_definitions(
            module.tree.body,
            (),
            before_line=1 << 30,
            definition=resolver,
        )
        return _flow_reaching_definitions(
            function.node.body,
            module_values,
            before_line=before_line,
            definition=resolver,
        )

    def _local_assignment(
        self,
        module: _GraphModule,
        function_symbol: str,
        name: str,
        *,
        before_line: int,
    ) -> ast.AST | None:
        values = self._local_assignments(
            module,
            function_symbol,
            name,
            before_line=before_line,
        )
        return values[0] if len(values) == 1 else None

    def _parameter_origins(
        self,
        module: _GraphModule,
        function: _FunctionInfo,
        expression: ast.AST,
        seen: frozenset[int] = frozenset(),
    ) -> set[int]:
        if id(expression) in seen:
            return set()
        if isinstance(expression, ast.Await):
            return self._parameter_origins(
                module,
                function,
                expression.value,
                seen | {id(expression)},
            )
        if isinstance(expression, ast.Name):
            index = self._function_parameter(function.symbol, expression.id)
            if index is not None:
                return {index}
            assigned = self._local_assignments(
                module,
                function.symbol,
                expression.id,
                before_line=getattr(expression, "lineno", 1 << 30),
            )
            origins: set[int] = set()
            for value in assigned:
                origins.update(
                    self._parameter_origins(
                        module,
                        function,
                        value,
                        seen | {id(expression)},
                    )
                )
            return origins
        return set()

    def _resolve_callable(
        self,
        module: _GraphModule,
        function_symbol: str,
        expression: ast.AST,
        seen: frozenset[tuple[str, str]] = frozenset(),
    ) -> set[int]:
        imported_payloads: set[int] = set()
        if isinstance(expression, ast.Name):
            for target in self._import_targets(
                module,
                function_symbol,
                expression.id,
                before_line=getattr(expression, "lineno", 1 << 30),
            ):
                if target == self._TRUE_SINK:
                    imported_payloads.add(1)
                elif target in self.summaries:
                    imported_payloads.update(self.summaries[target])
        if imported_payloads:
            return imported_payloads
        use = self._resolve_symbol(module, function_symbol, expression)
        if use:
            if use.symbol == self._TRUE_SINK:
                return {1}
            if use.symbol in self.summaries:
                offset = 1 if use.bound else 0
                return {
                    index - offset
                    for index in self.summaries[use.symbol]
                    if index >= offset
                }
        if isinstance(expression, ast.Name):
            parameter_index = self._function_parameter(
                function_symbol,
                expression.id,
            )
            if parameter_index is not None:
                # Callable parameters are resolved together with the payload at
                # each caller. Aggregating their actuals here would cross-pair
                # a true sink from one callsite with another callsite's data.
                return set()
            assigned = self._local_assignments(
                module,
                function_symbol,
                expression.id,
                before_line=getattr(expression, "lineno", 1 << 30),
            )
            resolved: set[int] = set()
            for value in assigned:
                resolved.update(
                    self._resolve_callable(
                        module,
                        function_symbol,
                        value,
                        seen,
                    )
                )
            return resolved
        return set()

    def _payload_argument(
        self,
        module: _GraphModule,
        function_symbol: str,
        call: ast.Call,
        payload_index: int,
        *,
        callable_expression: ast.AST | None = None,
        callable_module: _GraphModule | None = None,
        callable_function_symbol: str | None = None,
    ) -> ast.AST | None:
        """Bind one sink payload formal to its positional/keyword actual."""

        expression = callable_expression or call.func
        lookup_module = callable_module or module
        lookup_function = (
            function_symbol
            if callable_function_symbol is None
            else callable_function_symbol
        )
        use = self._resolve_symbol(
            lookup_module,
            lookup_function,
            expression,
        )
        if use and use.symbol in self.functions:
            callee = self.functions[use.symbol]
            offset = (
                1
                if (
                    use.bound
                    and callee.params
                    and callee.params[0] in {"self", "cls"}
                )
                else 0
            )
            formal_index = payload_index + offset
            if 0 <= formal_index < len(callee.params):
                return self._bind(
                    callee,
                    call,
                    bound=use.bound,
                ).get(callee.params[formal_index])
            return None
        if payload_index < len(call.args):
            return call.args[payload_index]
        if payload_index == 1:
            return next((
                item.value
                for item in call.keywords
                if item.arg == "payload"
            ), None)
        return None

    def _callback_parameter(
        self,
        function: _FunctionInfo,
        expression: ast.AST,
    ) -> str | None:
        if (
            isinstance(expression, ast.Name)
            and expression.id in function.params
        ):
            return expression.id
        return None

    def _callback_attr(
        self,
        function: _FunctionInfo,
        expression: ast.AST,
    ) -> str | None:
        if not isinstance(expression, ast.Attribute):
            return None
        base, attrs = self._attribute_chain(expression)
        if (
            function.class_symbol
            and isinstance(base, ast.Name)
            and base.id == "self"
            and len(attrs) == 1
            and (function.class_symbol, attrs[0]) in self.constructor_attr_params
        ):
            return attrs[0]
        return None

    def _solve_sinks(self) -> None:
        for _ in range(max(8, len(self.functions) + 1)):
            changed = False
            for function in self.functions.values():
                module = self.modules[function.module_name]
                for call in module.calls_in(function):
                    for payload_index in self._resolve_callable(
                        module,
                        function.symbol,
                        call.func,
                    ):
                        payload = self._payload_argument(
                            module,
                            function.symbol,
                            call,
                            payload_index,
                        )
                        if payload is None:
                            continue
                        origins = self._parameter_origins(
                            module,
                            function,
                            payload,
                        )
                        if not origins:
                            continue
                        before = set(self.summaries.get(function.symbol, set()))
                        self.summaries.setdefault(function.symbol, set()).update(
                            origins,
                        )
                        changed = changed or before != self.summaries[function.symbol]
            if not changed:
                return
        raise ArchitectureGuardError(
            "proactive call graph did not converge",
        )

    @staticmethod
    def _bound_payload_path(
        *,
        callee_module: _GraphModule,
        callee_function: _FunctionInfo,
        payload: ast.AST,
        binding: Mapping[str, ast.AST],
        caller_module: _GraphModule,
        caller_function_symbol: str,
    ) -> tuple[_GraphModule, str, ast.AST, Mapping[str, ast.AST]]:
        if isinstance(payload, ast.Name) and payload.id in binding:
            return (
                caller_module,
                caller_function_symbol,
                binding[payload.id],
                {},
            )
        return (
            callee_module,
            callee_function.symbol,
            payload,
            binding,
        )

    def _constructor_attr_actual(
        self,
        *,
        class_symbol: str,
        attr: str,
        receiver: ast.Call,
        module: _GraphModule,
        function_symbol: str,
    ) -> ast.AST | None:
        parameter = self.constructor_attr_params.get((class_symbol, attr))
        initializer = self.functions.get(f"{class_symbol}.__init__")
        if parameter is None or initializer is None:
            return None
        return self._bind(
            initializer,
            receiver,
            bound=True,
        ).get(parameter)

    def _payload_paths(
        self,
        module: _GraphModule,
        function_symbol: str,
        call: ast.Call,
    ) -> tuple[
        list[tuple[_GraphModule, str, ast.AST, Mapping[str, ast.AST]]],
        list[_ArchUnknown],
    ]:
        """Resolve reachable payloads without cross-pairing callsites."""

        paths: list[
            tuple[_GraphModule, str, ast.AST, Mapping[str, ast.AST]]
        ] = []
        missing: list[_ArchUnknown] = []
        function = self.functions.get(function_symbol)
        if function is not None:
            callback_parameter = self._callback_parameter(
                function,
                call.func,
            )
            if callback_parameter is not None:
                for site in self.callsites.get(function.symbol, []):
                    binding = self._bind(
                        function,
                        site.call,
                        bound=site.bound,
                    )
                    actual_callable = binding.get(callback_parameter)
                    if actual_callable is None:
                        continue
                    caller_module = self.modules[site.module_name]
                    for payload_index in self._resolve_callable(
                        caller_module,
                        site.function_symbol,
                        actual_callable,
                    ):
                        payload = self._payload_argument(
                            module,
                            function.symbol,
                            call,
                            payload_index,
                            callable_expression=actual_callable,
                            callable_module=caller_module,
                            callable_function_symbol=site.function_symbol,
                        )
                        if payload is None:
                            missing.append(_ArchUnknown(
                                call,
                                "proactive sink payload argument is missing",
                            ))
                            continue
                        paths.append(self._bound_payload_path(
                            callee_module=module,
                            callee_function=function,
                            payload=payload,
                            binding=binding,
                            caller_module=caller_module,
                            caller_function_symbol=site.function_symbol,
                        ))
                return paths, missing

            callback_attr = self._callback_attr(function, call.func)
            if callback_attr is not None:
                method_sites = self.callsites.get(function.symbol, [])
                constructor_paths: list[
                    tuple[_CallSite, Mapping[str, ast.AST]]
                ] = []
                if method_sites:
                    constructor_paths.extend(
                        (
                            site,
                            self._bind(
                                function,
                                site.call,
                                bound=site.bound,
                            ),
                        )
                        for site in method_sites
                        if site.receiver is not None
                    )
                else:
                    constructor_paths.extend(
                        (site, {})
                        for site in self.constructor_sites.get(
                            function.class_symbol,
                            [],
                        )
                    )
                for site, binding in constructor_paths:
                    if site.receiver is None:
                        receiver = site.call
                    else:
                        receiver = site.receiver
                    caller_module = self.modules[site.module_name]
                    actual_callable = self._constructor_attr_actual(
                        class_symbol=function.class_symbol,
                        attr=callback_attr,
                        receiver=receiver,
                        module=caller_module,
                        function_symbol=site.function_symbol,
                    )
                    if actual_callable is None:
                        continue
                    for payload_index in self._resolve_callable(
                        caller_module,
                        site.function_symbol,
                        actual_callable,
                    ):
                        payload = self._payload_argument(
                            module,
                            function.symbol,
                            call,
                            payload_index,
                            callable_expression=actual_callable,
                            callable_module=caller_module,
                            callable_function_symbol=site.function_symbol,
                        )
                        if payload is None:
                            missing.append(_ArchUnknown(
                                call,
                                "proactive sink payload argument is missing",
                            ))
                            continue
                        paths.append(self._bound_payload_path(
                            callee_module=module,
                            callee_function=function,
                            payload=payload,
                            binding=binding,
                            caller_module=caller_module,
                            caller_function_symbol=site.function_symbol,
                        ))
                return paths, missing

        for payload_index in self._resolve_callable(
            module,
            function_symbol,
            call.func,
        ):
            payload = self._payload_argument(
                module,
                function_symbol,
                call,
                payload_index,
            )
            if payload is None:
                missing.append(_ArchUnknown(
                    call,
                    "proactive sink payload argument is missing",
                ))
                continue
            paths.append((module, function_symbol, payload, {}))
        return paths, missing

    def _caller_expressions(
        self,
        function: _FunctionInfo,
        parameter: str,
    ) -> tuple[tuple[_GraphModule, str, ast.AST], ...]:
        values: list[tuple[_GraphModule, str, ast.AST]] = []
        for site in self.callsites.get(function.symbol, []):
            bound = self._bind(function, site.call, bound=site.bound)
            if parameter in bound:
                values.append((
                    self.modules[site.module_name],
                    site.function_symbol,
                    bound[parameter],
                ))
        return tuple(values)

    def _resolve_string(
        self,
        module: _GraphModule,
        function_symbol: str,
        node: ast.AST,
        env: Mapping[str, ast.AST],
        stack: frozenset[tuple[str, str]],
    ) -> tuple[set[str], list[_ArchUnknown]]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return {node.value}, []
        if isinstance(node, ast.Name):
            if node.id in env:
                return self._resolve_string(
                    module,
                    function_symbol,
                    env[node.id],
                    env,
                    stack,
                )
            assigned = self._local_assignments(
                module,
                function_symbol,
                node.id,
                before_line=getattr(node, "lineno", 1 << 30),
            )
            if assigned:
                values: set[str] = set()
                errors: list[_ArchUnknown] = []
                for value in assigned:
                    if value is node:
                        continue
                    found, unknowns = self._resolve_string(
                        module,
                        function_symbol,
                        value,
                        env,
                        stack,
                    )
                    values.update(found)
                    errors.extend(unknowns)
                if values or errors:
                    return values, errors
            function = self.functions.get(function_symbol)
            if function and node.id in function.params:
                key = (function_symbol, node.id)
                if key in stack:
                    return set(), [_ArchUnknown(node, "recursive producer data flow")]
                callers = self._caller_expressions(function, node.id)
                if not callers:
                    return set(), [_ArchUnknown(
                        node,
                        f"parameter {node.id!r} has no static callers",
                    )]
                values: set[str] = set()
                errors: list[_ArchUnknown] = []
                for caller_module, caller_function, expression in callers:
                    found, unknowns = self._resolve_string(
                        caller_module,
                        caller_function,
                        expression,
                        {},
                        stack | {key},
                    )
                    values.update(found)
                    errors.extend(unknowns)
                return values, errors
            return set(), [_ArchUnknown(node, f"name {node.id!r} is dynamic")]
        if isinstance(node, ast.IfExp):
            left, left_errors = self._resolve_string(
                module,
                function_symbol,
                node.body,
                env,
                stack,
            )
            right, right_errors = self._resolve_string(
                module,
                function_symbol,
                node.orelse,
                env,
                stack,
            )
            return left | right, left_errors + right_errors
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, left_errors = self._resolve_string(
                module,
                function_symbol,
                node.left,
                env,
                stack,
            )
            right, right_errors = self._resolve_string(
                module,
                function_symbol,
                node.right,
                env,
                stack,
            )
            if left_errors or right_errors:
                return set(), left_errors + right_errors
            return {a + b for a in left for b in right}, []
        if isinstance(node, ast.Call):
            use = self._resolve_symbol(module, function_symbol, node.func)
            if not use or use.symbol not in self.functions:
                return set(), [_ArchUnknown(
                    node,
                    "helper return is not statically known",
                )]
            callee = self.functions[use.symbol]
            bound = self._bind(callee, node, bound=use.bound)
            callee_module = self.modules[callee.module_name]
            values: set[str] = set()
            errors: list[_ArchUnknown] = []
            returns = callee_module.returns_in(callee)
            if not returns:
                return set(), [_ArchUnknown(callee.node, "helper has no static return")]
            for item in returns:
                if item.value is None:
                    errors.append(_ArchUnknown(item, "helper returns no value"))
                    continue
                found, unknowns = self._resolve_string(
                    callee_module,
                    callee.symbol,
                    item.value,
                    bound,
                    stack,
                )
                values.update(found)
                errors.extend(unknowns)
            return values, errors
        return set(), [_ArchUnknown(node, "expression is not a static string")]

    def _resolve_payload(
        self,
        module: _GraphModule,
        function_symbol: str,
        node: ast.AST,
        env: Mapping[str, ast.AST],
        stack: frozenset[tuple[str, str]],
    ) -> tuple[set[str], list[_ArchUnknown], bool]:
        if isinstance(node, ast.Await):
            return self._resolve_payload(
                module,
                function_symbol,
                node.value,
                env,
                stack,
            )
        if isinstance(node, ast.Name):
            if node.id in env:
                return self._resolve_payload(
                    module,
                    function_symbol,
                    env[node.id],
                    env,
                    stack,
                )
            assigned = self._local_assignments(
                module,
                function_symbol,
                node.id,
                before_line=getattr(node, "lineno", 1 << 30),
            )
            if assigned:
                values: set[str] = set()
                errors: list[_ArchUnknown] = []
                transparent = True
                for value in assigned:
                    found, unknowns, item_transparent = self._resolve_payload(
                        module,
                        function_symbol,
                        value,
                        env,
                        stack,
                    )
                    values.update(found)
                    errors.extend(unknowns)
                    transparent = transparent and item_transparent
                return values, errors, transparent
            function = self.functions.get(function_symbol)
            if function and node.id in function.params:
                key = (function_symbol, node.id)
                if key in stack:
                    return set(), [_ArchUnknown(
                        node,
                        "recursive producer payload flow",
                    )], False
                callers = self._caller_expressions(function, node.id)
                if not callers and function_symbol in self.summaries:
                    return set(), [], True
                if not callers:
                    return set(), [_ArchUnknown(
                        node,
                        f"payload parameter {node.id!r} has no static callers",
                    )], False
                values: set[str] = set()
                errors: list[_ArchUnknown] = []
                for caller_module, caller_function, expression in callers:
                    found, unknowns, _ = self._resolve_payload(
                        caller_module,
                        caller_function,
                        expression,
                        {},
                        stack | {key},
                    )
                    values.update(found)
                    errors.extend(unknowns)
                return values, errors, False
            return set(), [_ArchUnknown(node, "payload name is dynamic")], False
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "type":
                    values, errors = self._resolve_string(
                        module,
                        function_symbol,
                        value,
                        env,
                        frozenset(),
                    )
                    return values, errors, False
            return set(), [_ArchUnknown(
                node,
                "proactive payload has no static 'type' key",
            )], False
        if isinstance(node, ast.Call):
            use = self._resolve_symbol(module, function_symbol, node.func)
            if not use or use.symbol not in self.functions:
                return set(), [_ArchUnknown(
                    node,
                    "payload helper is not statically known",
                )], False
            callee = self.functions[use.symbol]
            bound = self._bind(callee, node, bound=use.bound)
            callee_module = self.modules[callee.module_name]
            values: set[str] = set()
            errors: list[_ArchUnknown] = []
            for item in callee_module.returns_in(callee):
                if item.value is None or (
                    isinstance(item.value, ast.Constant)
                    and item.value.value is None
                ):
                    continue
                found, unknowns, _ = self._resolve_payload(
                    callee_module,
                    callee.symbol,
                    item.value,
                    bound,
                    stack,
                )
                values.update(found)
                errors.extend(unknowns)
            if not values and not errors:
                errors.append(_ArchUnknown(callee.node, "payload helper has no return"))
            return values, errors, False
        if isinstance(node, ast.IfExp):
            left, left_errors, _ = self._resolve_payload(
                module,
                function_symbol,
                node.body,
                env,
                stack,
            )
            right, right_errors, _ = self._resolve_payload(
                module,
                function_symbol,
                node.orelse,
                env,
                stack,
            )
            return left | right, left_errors + right_errors, False
        return set(), [_ArchUnknown(
            node,
            "proactive payload is not statically traceable",
        )], False

    def proactive_types(self) -> set[str]:
        values: set[str] = set()
        unknowns: list[tuple[_GraphModule, _ArchUnknown]] = []
        for module in self.modules.values():
            for call in (
                node for node in ast.walk(module.tree) if isinstance(node, ast.Call)
            ):
                function_symbol = module.enclosing_function(call)
                paths, missing = self._payload_paths(
                    module,
                    function_symbol,
                    call,
                )
                unknowns.extend((module, item) for item in missing)
                for (
                    payload_module,
                    payload_function,
                    payload,
                    env,
                ) in paths:
                    found, errors, transparent = self._resolve_payload(
                        payload_module,
                        payload_function,
                        payload,
                        env,
                        frozenset(),
                    )
                    values.update(found)
                    if not transparent:
                        unknowns.extend(
                            (payload_module, item)
                            for item in errors
                        )
        if unknowns:
            module, first = sorted(
                unknowns,
                key=lambda item: (
                    item[0].relative,
                    getattr(item[1].node, "lineno", 0),
                    getattr(item[1].node, "col_offset", 0),
                    item[1].reason,
                ),
            )[0]
            raise ArchitectureGuardError(
                f"{module.relative}:{getattr(first.node, 'lineno', 0)}:"
                f"{getattr(first.node, 'col_offset', 0) + 1}: "
                f"proactive type must be static ({first.reason})",
            )
        return values


_PROACTIVE_TYPES_CACHE: dict[
    Path,
    tuple[tuple[tuple[str, str], ...], frozenset[str]],
] = {}


def _proactive_source_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
    """Invalidate the call graph from bounded source paths and content."""

    snapshot: list[tuple[str, str]] = []
    for path in _arch_glob(root, "**/*.py"):
        snapshot.append((
            path.relative_to(root).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        ))
    return tuple(snapshot)


def _proactive_types(root: Path) -> set[str]:
    snapshot = _proactive_source_snapshot(root)
    cached = _PROACTIVE_TYPES_CACHE.get(root)
    if cached is not None and cached[0] == snapshot:
        return set(cached[1])
    values = frozenset(_ProactiveCallGraph(root).proactive_types())
    _PROACTIVE_TYPES_CACHE[root] = (snapshot, values)
    return set(values)


def load_architecture_vocabulary(
    repo_root: Path | str = REPO_ROOT,
) -> ArchitectureVocabulary:
    """Build architecture vocabulary only from owned declarations/producers."""

    root = _arch_root(repo_root)
    manifest_terms = _manifest_domain_terms(root)
    command_terms = _commands_domain_terms(root)
    skill_terms = _skills_domain_terms(root)
    domain_terms = manifest_terms | command_terms | skill_terms
    proactive_types = _proactive_types(root)
    # A bare one-token command noun can also be a generic parameter. It remains
    # forbidden as executable string policy and is guarded inside compound
    # identifiers, while an exact one-token argument is structurally
    # ambiguous. Composite command nouns, all intent namespaces and proactive
    # types are otherwise data-driven identifier vocabulary.
    intent_namespaces = {
        value.split(".", 1)[0]
        for value in domain_terms
        if "." in value
    }
    composite_commands = {
        value for value in command_terms if len(_identifier_tokens(value)) > 1
    }
    ambiguous_identifier_terms = {
        value for value in command_terms if len(_identifier_tokens(value)) == 1
    }
    identifier_terms = (
        intent_namespaces
        | proactive_types
        | composite_commands
        | ambiguous_identifier_terms
    )
    return ArchitectureVocabulary(
        domain_terms=frozenset(domain_terms),
        proactive_types=frozenset(proactive_types),
        identifier_terms=frozenset(identifier_terms),
        ambiguous_identifier_terms=frozenset(ambiguous_identifier_terms),
    )


def _is_docstring_statement(node: ast.AST, parent: ast.AST | None) -> bool:
    if not (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ):
        return False
    body = getattr(parent, "body", None)
    return isinstance(body, list) and bool(body) and body[0] is node


class _ExecutableSemanticClosure:
    """Select executable definitions reachable from fixed guarded functions."""

    def __init__(self, tree: ast.Module):
        self.tree = tree
        self.parents: dict[ast.AST, ast.AST] = {}
        self.functions_by_scope: dict[
            tuple[ast.AST, str],
            ast.FunctionDef | ast.AsyncFunctionDef,
        ] = {}
        self.classes_by_name: dict[str, ast.ClassDef] = {}
        self.values_by_scope: dict[tuple[ast.AST, str], ast.AST] = {}
        self._index()

    def _index(self) -> None:
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef):
                self.classes_by_name.setdefault(node.name, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = self._definition_scope(node)
                self.functions_by_scope.setdefault((scope, node.name), node)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                scope = self._definition_scope(node)
                if not isinstance(scope, (ast.Module, ast.ClassDef)):
                    continue
                value = node.value
                if value is None:
                    continue
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                for target in targets:
                    if isinstance(target, ast.Name):
                        self.values_by_scope[(scope, target.id)] = value

    def _definition_scope(self, node: ast.AST) -> ast.AST:
        current = self.parents.get(node)
        while current is not None:
            if isinstance(
                current,
                (
                    ast.Module,
                    ast.ClassDef,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):
                return current
            current = self.parents.get(current)
        return self.tree

    def _enclosing_function(
        self,
        node: ast.AST,
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        current = self.parents.get(node)
        while current is not None:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current
            current = self.parents.get(current)
        return None

    def _enclosing_class(self, node: ast.AST) -> ast.ClassDef | None:
        current = self.parents.get(node)
        while current is not None:
            if isinstance(current, ast.ClassDef):
                return current
            current = self.parents.get(current)
        return None

    def _direct_function_nodes(
        self,
        function: ast.FunctionDef | ast.AsyncFunctionDef,
    ):
        for node in ast.walk(function):
            if node is not function and self._enclosing_function(node) is function:
                yield node

    def _local_callable_values(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef,
        name: str,
        *,
        before_line: int,
    ) -> tuple[ast.AST, ...]:
        def assignment(statement: ast.stmt) -> ast.AST | None:
            if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == name
                for target in statement.targets
            ):
                return statement.value
            if (
                isinstance(statement, ast.AnnAssign)
                and statement.value is not None
                and isinstance(statement.target, ast.Name)
                and statement.target.id == name
            ):
                return statement.value
            return None

        return _flow_reaching_definitions(
            owner.body,
            (),
            before_line=before_line,
            definition=assignment,
        )

    def _functions_for_call(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef | None,
        expression: ast.AST,
        seen: frozenset[str] = frozenset(),
    ) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ...]:
        if isinstance(expression, ast.Name):
            if expression.id in seen:
                return ()
            scope: ast.AST | None = owner
            while isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found = self.functions_by_scope.get((scope, expression.id))
                if found is not None:
                    return (found,)
                scope = self._definition_scope(scope)
            if owner is not None:
                assigned = self._local_callable_values(
                    owner,
                    expression.id,
                    before_line=getattr(expression, "lineno", 1 << 30),
                )
                if assigned:
                    functions: list[
                        ast.FunctionDef | ast.AsyncFunctionDef
                    ] = []
                    for value in assigned:
                        functions.extend(
                            self._functions_for_call(
                                owner,
                                value,
                                seen | {expression.id},
                            )
                        )
                    return tuple({
                        id(function): function
                        for function in functions
                    }.values())
            found = self.functions_by_scope.get((self.tree, expression.id))
            return (found,) if found is not None else ()
        if not isinstance(expression, ast.Attribute):
            return ()
        if isinstance(expression.value, ast.Name):
            if expression.value.id in {"self", "cls"} and owner is not None:
                class_node = self._enclosing_class(owner)
            else:
                class_node = self.classes_by_name.get(expression.value.id)
            if class_node is not None:
                found = self.functions_by_scope.get(
                    (class_node, expression.attr),
                )
                return (found,) if found is not None else ()
        return ()

    def _value_for_reference(
        self,
        owner: ast.FunctionDef | ast.AsyncFunctionDef | None,
        node: ast.AST,
    ) -> ast.AST | None:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if owner is None:
                class_node = self._enclosing_class(node)
                if class_node is not None:
                    value = self.values_by_scope.get((class_node, node.id))
                    if value is not None:
                        return value
            return self.values_by_scope.get((self.tree, node.id))
        if not isinstance(node, ast.Attribute) or not isinstance(
            node.value,
            ast.Name,
        ):
            return None
        if node.value.id in {"self", "cls"} and owner is not None:
            class_node = self._enclosing_class(owner)
        else:
            class_node = self.classes_by_name.get(node.value.id)
        if class_node is None:
            return None
        return self.values_by_scope.get((class_node, node.attr))

    def select(
        self,
        names: frozenset[str],
    ) -> tuple[frozenset[int], frozenset[int], frozenset[str]]:
        roots = {
            node
            for (scope, name), node in self.functions_by_scope.items()
            if (
                name in names
                and isinstance(scope, (ast.Module, ast.ClassDef))
            )
        }
        selected_functions = set(roots)
        selected_values: set[ast.AST] = set()
        function_queue = list(roots)
        value_queue: list[ast.AST] = []

        def add_value(value: ast.AST | None) -> None:
            if value is not None and value not in selected_values:
                selected_values.add(value)
                value_queue.append(value)

        def inspect(
            nodes,
            owner: ast.FunctionDef | ast.AsyncFunctionDef | None,
        ) -> None:
            for node in nodes:
                if isinstance(node, ast.Call):
                    for helper in self._functions_for_call(owner, node.func):
                        if helper not in selected_functions:
                            selected_functions.add(helper)
                            function_queue.append(helper)
                add_value(self._value_for_reference(owner, node))

        while function_queue or value_queue:
            while function_queue:
                function = function_queue.pop()
                inspect(self._direct_function_nodes(function), function)
            while value_queue:
                value = value_queue.pop()
                inspect(ast.walk(value), self._enclosing_function(value))

        return (
            frozenset(id(node) for node in selected_functions),
            frozenset(id(node) for node in selected_values),
            frozenset(node.name for node in roots),
        )


class _ExecutableVocabularyVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        path: str,
        terms: frozenset[str],
        identifier_terms: frozenset[str],
        ambiguous_identifier_terms: frozenset[str],
        only_functions: frozenset[str] | None,
        selected_function_nodes: frozenset[int] = frozenset(),
        selected_value_nodes: frozenset[int] = frozenset(),
    ):
        self.path = path
        self.terms = tuple(sorted(terms, key=lambda value: (-len(value), value)))
        self.identifier_terms = tuple(sorted(
            (
                (value, _identifier_tokens(value))
                for value in identifier_terms
            ),
            key=lambda item: (-len(item[1]), -len(item[0]), item[0]),
        ))
        self.ambiguous_identifier_terms = ambiguous_identifier_terms
        self.only_functions = only_functions
        self.selected_function_nodes = selected_function_nodes
        self.selected_value_nodes = selected_value_nodes
        self.violations: list[ArchitectureViolation] = []
        self.parents: list[ast.AST] = []
        self.functions: list[str] = []
        self._active = only_functions is None
        self._found_functions: set[str] = set()

    @staticmethod
    def _term_in_terms(value: str, terms: tuple[str, ...]) -> str | None:
        for term in terms:
            pattern = (
                r"(?<![A-Za-z0-9_-])"
                + re.escape(term)
                + r"(?![A-Za-z0-9_-])"
            )
            if re.search(pattern, value):
                return term
        return None

    def _term_in(self, value: str) -> str | None:
        return self._term_in_terms(value, self.terms)

    def _record(self, node: ast.AST, value: str, kind: str | None = None) -> None:
        if not self._active:
            return
        term = self._term_in(value)
        if term is None:
            return
        self.violations.append(ArchitectureViolation(
            path=self.path,
            line=getattr(node, "lineno", 0),
            column=getattr(node, "col_offset", 0) + 1,
            term=term,
            node_kind=kind or type(node).__name__,
            function=self.functions[-1] if self.functions else "",
        ))

    def visit(self, node: ast.AST):
        parent = self.parents[-1] if self.parents else None
        if _is_docstring_statement(node, parent):
            return None
        was_active = self._active
        if id(node) in self.selected_value_nodes:
            self._active = True
        self.parents.append(node)
        try:
            return super().visit(node)
        finally:
            self.parents.pop()
            self._active = was_active

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        was_active = self._active
        selected = (
            self.only_functions is None
            or id(node) in self.selected_function_nodes
        )
        if self.only_functions is None or node.name in self.only_functions:
            self._found_functions.add(node.name)
        self._active = selected
        self.functions.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self.functions.pop()
            self._active = was_active

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        parent = self.parents[-2] if len(self.parents) >= 2 else None
        kind = type(parent).__name__ if isinstance(
            parent,
            (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.MatchValue, ast.Compare),
        ) else "Constant"
        self._record(node, node.value, kind)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        fragments: list[str] = []
        deferred: list[ast.AST] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                fragments.append(item.value)
            elif (
                isinstance(item, ast.FormattedValue)
                and isinstance(item.value, ast.Constant)
                and isinstance(item.value.value, str)
            ):
                fragments.append(item.value.value)
            else:
                fragments.append("{}")
                if isinstance(item, ast.FormattedValue):
                    deferred.append(item.value)
                    if item.format_spec is not None:
                        deferred.append(item.format_spec)
        value = "".join(fragments)
        self._record(node, value, "JoinedStr")
        # The JoinedStr-level diagnostic is more useful than duplicates for
        # each static fragment. Dynamic formatted expressions are still
        # traversed so references to selected executable constants cannot hide.
        for item in deferred:
            self.visit(item)

    def visit_arg(self, node: ast.arg) -> None:
        if self._active:
            tokens = _identifier_tokens(node.arg)
            term = next((
                value
                for value, candidate in self.identifier_terms
                if (
                    _has_contiguous_tokens(tokens, candidate)
                    and not (
                        value in self.ambiguous_identifier_terms
                        and not (
                            set(tokens) - set(candidate)
                        ) & _ARCH_IDENTIFIER_ROLE_TOKENS
                    )
                )
            ), None)
            if term:
                self.violations.append(ArchitectureViolation(
                    path=self.path,
                    line=getattr(node, "lineno", 0),
                    column=getattr(node, "col_offset", 0) + 1,
                    term=term,
                    node_kind="arg",
                    function=self.functions[-1] if self.functions else "",
                ))
        if node.annotation is not None:
            self.visit(node.annotation)


def guard_architecture(
    repo_root: Path | str = REPO_ROOT,
) -> tuple[ArchitectureViolation, ...]:
    """Return executable business literals in the fixed generic mechanisms."""

    root = _arch_root(repo_root)
    vocabulary = load_architecture_vocabulary(root)
    violations: list[ArchitectureViolation] = []
    for relative, functions in _ARCH_TARGETS.items():
        path = _arch_file(root, relative)
        _, tree = _arch_parse_python(root, path)
        function_nodes: frozenset[int] = frozenset()
        value_nodes: frozenset[int] = frozenset()
        found_functions: frozenset[str] = frozenset()
        if functions is not None:
            (
                function_nodes,
                value_nodes,
                found_functions,
            ) = _ExecutableSemanticClosure(tree).select(frozenset(functions))
        visitor = _ExecutableVocabularyVisitor(
            path=relative,
            terms=vocabulary.all_terms,
            identifier_terms=vocabulary.identifier_terms,
            ambiguous_identifier_terms=vocabulary.ambiguous_identifier_terms,
            only_functions=frozenset(functions) if functions is not None else None,
            selected_function_nodes=function_nodes,
            selected_value_nodes=value_nodes,
        )
        visitor.visit(tree)
        if functions is not None:
            missing = set(functions) - found_functions
            if missing:
                raise ArchitectureGuardError(
                    f"{relative}: guarded functions do not resolve: {sorted(missing)}",
                )
        violations.extend(visitor.violations)
    return tuple(sorted(
        violations,
        key=lambda item: (
            item.path,
            item.line,
            item.column,
            item.term,
            item.node_kind,
        ),
    ))


def assert_architecture_guard(repo_root: Path | str = REPO_ROOT) -> None:
    """Fail with stable path/line diagnostics when a central mechanism leaks."""

    violations = guard_architecture(repo_root)
    if violations:
        raise ArchitectureGuardError(
            "architecture guard rejected executable domain policy:\n"
            + "\n".join(item.diagnostic() for item in violations),
        )
