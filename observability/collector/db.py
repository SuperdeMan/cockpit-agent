"""SQLite 持久层：turns / spans / llm_calls / logs 落盘与查询。

选型（见 docs/design/2026-07-10-dashboard-badcase-observability-redesign.md §3.6）：
- stdlib sqlite3 零新依赖（对齐 R3.6 手写 Prometheus 的先例），WAL 模式；
- 单连接 + 线程锁，调用方经 asyncio.to_thread 进事件循环旁路（写入量 = 人工对话级，微秒级语句）；
- 观测数据不进业务 PostgreSQL——保留期/清理策略独立，collector 保持可单独运行；
- OBS_DB_PATH 未配置时用内存库（测试/裸跑仍可用，只是不跨重启）；compose 挂 named volume。

失败隔离：所有写入错误由调用方吞掉（观测挂了不能影响 collector 主链路）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns(
  trace_id TEXT PRIMARY KEY,
  session_id TEXT DEFAULT '',
  ts INTEGER DEFAULT 0,
  duration_ms REAL DEFAULT 0,
  user_text TEXT DEFAULT '',
  speech TEXT DEFAULT '',
  status TEXT DEFAULT '',
  path TEXT DEFAULT '',
  input_source TEXT DEFAULT '',
  is_confirmation INTEGER DEFAULT 0,
  ui_card_type TEXT DEFAULT '',
  actions INTEGER DEFAULT 0,
  error TEXT DEFAULT '',
  badcase INTEGER DEFAULT 0,
  note TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);
CREATE INDEX IF NOT EXISTS idx_turns_status ON turns(status, ts);

CREATE TABLE IF NOT EXISTS spans(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT DEFAULT '',
  span_id TEXT DEFAULT '',
  parent_id TEXT DEFAULT '',
  ts INTEGER DEFAULT 0,
  service TEXT DEFAULT '',
  node TEXT DEFAULT '',
  status TEXT DEFAULT '',
  duration_ms REAL DEFAULT 0,
  attrs TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS idx_spans_ts ON spans(ts);

CREATE TABLE IF NOT EXISTS llm_calls(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trace_id TEXT DEFAULT '',
  session_id TEXT DEFAULT '',
  ts INTEGER DEFAULT 0,
  caller TEXT DEFAULT '',
  model TEXT DEFAULT '',
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  latency_ms REAL DEFAULT 0,
  cache_hit INTEGER DEFAULT 0,
  thinking INTEGER DEFAULT 0,
  status TEXT DEFAULT '',
  error TEXT DEFAULT '',
  prompt_tail TEXT DEFAULT '',
  content_head TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_llm_trace ON llm_calls(trace_id);
CREATE INDEX IF NOT EXISTS idx_llm_ts ON llm_calls(ts);

CREATE TABLE IF NOT EXISTS logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER DEFAULT 0,
  service TEXT DEFAULT '',
  level TEXT DEFAULT '',
  logger TEXT DEFAULT '',
  msg TEXT DEFAULT '',
  trace_id TEXT DEFAULT '',
  session_id TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_logs_trace ON logs(trace_id);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
"""

_TURN_FIELDS = ("session_id", "ts", "duration_ms", "user_text", "speech", "status",
                "path", "input_source", "is_confirmation", "ui_card_type",
                "actions", "error")


def _rows_to_dicts(cursor) -> list[dict]:
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


class ObsDB:
    """turns/spans/llm_calls/logs 的同步 SQLite 存取（调用方负责 to_thread）。"""

    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("OBS_DB_PATH", "") or ":memory:"
        parent = os.path.dirname(self.path)
        if parent and self.path != ":memory:":
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ── 写入（ingest 路径） ──────────────────────────────────────────────

    def insert_turn(self, event: dict) -> None:
        trace_id = event.get("trace_id") or ""
        if not trace_id:
            return
        values = {
            "session_id": event.get("session_id", "") or "",
            "ts": int(event.get("ts", 0) or 0),
            "duration_ms": float(event.get("duration_ms", 0) or 0),
            "user_text": event.get("user_text", "") or "",
            "speech": event.get("speech", "") or "",
            "status": event.get("status", "") or "",
            "path": event.get("path", "") or "",
            "input_source": event.get("input_source", "") or "",
            "is_confirmation": 1 if event.get("is_confirmation") else 0,
            "ui_card_type": event.get("ui_card_type", "") or "",
            "actions": int(event.get("actions", 0) or 0),
            "error": event.get("error", "") or "",
        }
        assigns = ", ".join(f"{k}=excluded.{k}" for k in _TURN_FIELDS)
        with self._lock:
            # UPSERT：重复到达覆盖运行字段，但绝不动人工标记（badcase/note）
            self._conn.execute(
                f"INSERT INTO turns(trace_id, {', '.join(_TURN_FIELDS)}) "
                f"VALUES(:trace_id, {', '.join(':' + k for k in _TURN_FIELDS)}) "
                f"ON CONFLICT(trace_id) DO UPDATE SET {assigns}",
                {"trace_id": trace_id, **values})
            self._conn.commit()

    def insert_span(self, event: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO spans(trace_id, span_id, parent_id, ts, service, node, "
                "status, duration_ms, attrs) VALUES(?,?,?,?,?,?,?,?,?)",
                (event.get("trace_id", "") or "", event.get("span_id", "") or "",
                 event.get("parent_id", "") or "", int(event.get("ts", 0) or 0),
                 event.get("service", "") or "", event.get("node", "") or "",
                 event.get("status", "") or "", float(event.get("duration_ms", 0) or 0),
                 json.dumps(event.get("attrs") or {}, ensure_ascii=False)))
            self._conn.commit()

    def insert_llm(self, event: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO llm_calls(trace_id, session_id, ts, caller, model, "
                "prompt_tokens, completion_tokens, latency_ms, cache_hit, thinking, "
                "status, error, prompt_tail, content_head) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event.get("trace_id", "") or "", event.get("session_id", "") or "",
                 int(event.get("ts", 0) or 0), event.get("caller", "") or "",
                 event.get("model", "") or "",
                 int(event.get("prompt_tokens", 0) or 0),
                 int(event.get("completion_tokens", 0) or 0),
                 float(event.get("latency_ms", 0) or 0),
                 1 if event.get("cache_hit") else 0,
                 1 if event.get("thinking") else 0,
                 event.get("status", "") or "", event.get("error", "") or "",
                 event.get("prompt_tail", "") or "", event.get("content_head", "") or ""))
            self._conn.commit()

    def insert_log(self, event: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO logs(ts, service, level, logger, msg, trace_id, session_id) "
                "VALUES(?,?,?,?,?,?,?)",
                (int(event.get("ts", 0) or 0), event.get("service", "") or "",
                 event.get("level", "") or "", event.get("logger", "") or "",
                 event.get("msg", "") or "", event.get("trace_id", "") or "",
                 event.get("session_id", "") or ""))
            self._conn.commit()

    # ── 查询（REST API） ────────────────────────────────────────────────

    def sessions(self, limit: int = 50, q: str = "") -> list[dict]:
        """会话列表：起止时间/轮数/错误数/拒识数/badcase 数，按最近活跃倒序。
        q 非空时只保留命中文本（用户原话或话术 LIKE）的会话。"""
        sql = ("SELECT session_id, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
               "COUNT(*) AS turns, "
               "SUM(CASE WHEN status IN ('err','timeout','empty') THEN 1 ELSE 0 END) AS errors, "
               "SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS rejected, "
               "SUM(badcase) AS badcases FROM turns")
        params: list = []
        if q:
            sql += (" WHERE session_id IN (SELECT DISTINCT session_id FROM turns "
                    "WHERE user_text LIKE ? OR speech LIKE ?)")
            like = f"%{q}%"
            params += [like, like]
        sql += " GROUP BY session_id ORDER BY last_ts DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            return _rows_to_dicts(self._conn.execute(sql, params))

    def session_turns(self, session_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            return _rows_to_dicts(self._conn.execute(
                "SELECT * FROM turns WHERE session_id=? ORDER BY ts ASC LIMIT ?",
                (session_id, int(limit))))

    def turn_detail(self, trace_id: str) -> dict | None:
        """轮次详情 = turn + spans + llm_calls + logs（badcase 排查一屏所需的全部）。"""
        with self._lock:
            rows = _rows_to_dicts(self._conn.execute(
                "SELECT * FROM turns WHERE trace_id=?", (trace_id,)))
            turn = rows[0] if rows else None
            spans = _rows_to_dicts(self._conn.execute(
                "SELECT * FROM spans WHERE trace_id=? ORDER BY ts ASC, id ASC",
                (trace_id,)))
            llm_calls = _rows_to_dicts(self._conn.execute(
                "SELECT * FROM llm_calls WHERE trace_id=? ORDER BY ts ASC, id ASC",
                (trace_id,)))
            logs = _rows_to_dicts(self._conn.execute(
                "SELECT * FROM logs WHERE trace_id=? ORDER BY ts ASC, id ASC",
                (trace_id,)))
        if turn is None and not spans and not llm_calls and not logs:
            return None
        for s in spans:
            try:
                s["attrs"] = json.loads(s.get("attrs") or "{}")
            except Exception:
                s["attrs"] = {}
        return {"turn": turn, "spans": spans, "llm_calls": llm_calls, "logs": logs}

    def search_turns(self, q: str = "", status: str = "", session_id: str = "",
                     badcase: bool | None = None, since: int = 0, until: int = 0,
                     limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM turns WHERE 1=1"
        params: list = []
        if q:
            # trace_id 前缀直达：HMI 复制的短 id 粘进搜索框即可定位
            sql += " AND (user_text LIKE ? OR speech LIKE ? OR trace_id LIKE ?)"
            like = f"%{q}%"
            params += [like, like, f"{q}%"]
        if status:
            sql += " AND status=?"
            params.append(status)
        if session_id:
            sql += " AND session_id=?"
            params.append(session_id)
        if badcase is not None:
            sql += " AND badcase=?"
            params.append(1 if badcase else 0)
        if since:
            sql += " AND ts>=?"
            params.append(int(since))
        if until:
            sql += " AND ts<=?"
            params.append(int(until))
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            return _rows_to_dicts(self._conn.execute(sql, params))

    def set_badcase(self, trace_id: str, flag: bool, note: str = "") -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE turns SET badcase=?, note=? WHERE trace_id=?",
                (1 if flag else 0, note or "", trace_id))
            self._conn.commit()
            return cur.rowcount > 0

    def query_logs(self, trace_id: str = "", service: str = "", level: str = "",
                   q: str = "", limit: int = 200) -> list[dict]:
        sql = "SELECT * FROM logs WHERE 1=1"
        params: list = []
        if trace_id:
            sql += " AND trace_id=?"
            params.append(trace_id)
        if service:
            sql += " AND service=?"
            params.append(service)
        if level:
            sql += " AND level=?"
            params.append(level.upper())
        if q:
            sql += " AND msg LIKE ?"
            params.append(f"%{q}%")
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = _rows_to_dicts(self._conn.execute(sql, params))
        rows.reverse()  # 返回按时间正序，便于阅读
        return rows

    # ── 保留期清理 ──────────────────────────────────────────────────────

    def cleanup(self, retention_days: float | None = None) -> int:
        """删过期数据。badcase 标记的轮次（及其 spans/llm/logs）豁免——排查素材不过期。
        返回删除的 turn 行数。"""
        if retention_days is None:
            retention_days = float(os.getenv("OBS_RETENTION_DAYS", "7"))
        cutoff = int((time.time() - retention_days * 86400) * 1000)
        with self._lock:
            keep = ("SELECT trace_id FROM turns WHERE badcase=1")
            cur = self._conn.execute(
                f"DELETE FROM turns WHERE ts<? AND badcase=0", (cutoff,))
            deleted = cur.rowcount
            for table in ("spans", "llm_calls", "logs"):
                self._conn.execute(
                    f"DELETE FROM {table} WHERE ts<? AND trace_id NOT IN ({keep})",
                    (cutoff,))
            self._conn.commit()
        return deleted

    def close(self) -> None:
        with self._lock:
            self._conn.close()
