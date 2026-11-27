"""sqlite 队列与状态机。

状态流转:
  new --(下载成功+元数据生成)--> ready --(投稿成功)--> published
   |                          +-> downloaded (下载成功但元数据待生成)
   +--(下载/投稿失败)--> failed  --(retry)--> new / ready
  任意 --(人工跳过)--> skipped
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name   TEXT NOT NULL,
  line          TEXT NOT NULL,
  platform      TEXT DEFAULT '',
  source_id     TEXT NOT NULL,
  source_url    TEXT DEFAULT '',
  orig_title    TEXT DEFAULT '',
  orig_desc     TEXT DEFAULT '',
  uploader      TEXT DEFAULT '',
  duration      REAL DEFAULT 0,
  upload_date   TEXT DEFAULT '',
  status        TEXT DEFAULT 'new',
  fail_reason   TEXT DEFAULT '',
  file_path     TEXT DEFAULT '',
  cover_path    TEXT DEFAULT '',
  subtitle_path TEXT DEFAULT '',
  downloaded_at INTEGER DEFAULT 0,
  title         TEXT DEFAULT '',
  description   TEXT DEFAULT '',
  tags          TEXT DEFAULT '[]',
  tid           INTEGER DEFAULT 0,
  mission_id    INTEGER DEFAULT 0,
  dtime         INTEGER DEFAULT 0,
  bvid          TEXT DEFAULT '',
  pub_log       TEXT DEFAULT '',
  created_at    INTEGER,
  updated_at    INTEGER,
  published_at  INTEGER,
  UNIQUE(platform, source_id)
);
CREATE INDEX IF NOT EXISTS idx_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_line   ON items(line);
"""


def _now() -> int:
    return int(time.time())


class DB:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            # 老库缺列时补列
            cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(items)")}
            for col, ddl in (("mission_id", "ALTER TABLE items ADD COLUMN mission_id INTEGER DEFAULT 0"),
                             ("dtime", "ALTER TABLE items ADD COLUMN dtime INTEGER DEFAULT 0"),
                             ("downloaded_at", "ALTER TABLE items ADD COLUMN downloaded_at INTEGER DEFAULT 0"),
                             ("verified_at", "ALTER TABLE items ADD COLUMN verified_at INTEGER DEFAULT 0"),
                             ("extra_pub", "ALTER TABLE items ADD COLUMN extra_pub TEXT DEFAULT ''"),
                             ("pub_platforms", "ALTER TABLE items ADD COLUMN pub_platforms TEXT DEFAULT ''"),
                             ("copyright", "ALTER TABLE items ADD COLUMN copyright INTEGER DEFAULT 0"),
                             ("cover_portrait_path", "ALTER TABLE items ADD COLUMN cover_portrait_path TEXT DEFAULT ''")):
                if col not in cols:
                    self.conn.execute(ddl)
            self.conn.commit()

    def insert_item(self, item: dict) -> int | None:
        """插入新条目，已存在(同 platform+source_id)返回 None。"""
        now = _now()
        with self._lock:
            cur = self.conn.execute(
            """INSERT OR IGNORE INTO items
               (source_name, line, platform, source_id, source_url, orig_title,
                orig_desc, uploader, duration, upload_date, status, tid, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?, 'new', ?, ?, ?)""",
            (
                item.get("source_name", ""), item.get("line", ""),
                item.get("platform", ""), item["source_id"],
                item.get("source_url", ""), item.get("orig_title", ""),
                item.get("orig_desc", ""), item.get("uploader", ""),
                item.get("duration") or 0, item.get("upload_date", ""),
                item.get("tid") or 0, now, now,
            ),
        )
            self.conn.commit()
            return cur.lastrowid if cur.rowcount else None

    def get(self, item_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return dict(row) if row else None

    def list_items(self, status: str | None = None, line: str | None = None,
                   limit: int = 200, offset: int = 0) -> list[dict]:
        q, args = "SELECT * FROM items WHERE 1=1", []
        if status:                              # 支持 "a,b" 多状态合并查询
            sts = status.split(",")
            q += f" AND status IN ({','.join('?' * len(sts))})"
            args += sts
        if line:
            q += " AND line=?"; args.append(line)
        q += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def update(self, item_id: int, **fields):
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self.conn.execute(f"UPDATE items SET {cols} WHERE id=?", (*fields.values(), item_id))
            self.conn.commit()

    def set_status(self, item_id: int, status: str, fail_reason: str = ""):
        self.update(item_id, status=status, fail_reason=fail_reason)

    def set_meta(self, item_id: int, title: str, desc: str, tags: list, tid: int):
        self.update(item_id, title=title, description=desc,
                    tags=json.dumps(tags, ensure_ascii=False), tid=tid)

    def unskip(self, item_id: int):
        """撤销跳过: 回 new 状态等待重新处理。"""
        self.update(item_id, status="new", fail_reason="")

    def published_unverified(self, max_age: int = 86400) -> list[dict]:
        """已发布且超过 max_age 未复核的条目(用于审核回执巡检)。"""
        cut = _now() - max_age
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM items WHERE status='published' AND bvid!='' AND verified_at < ?",
            (cut,)).fetchall()]

    def mark_verified(self, item_id: int, fail_reason: str = ""):
        self.update(item_id, verified_at=_now(), fail_reason=fail_reason)

    def mark_published(self, item_id: int, bvid: str, log: str = ""):
        self.update(item_id, status="published", bvid=bvid,
                    pub_log=log, published_at=_now())

    def counts(self) -> dict:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) c FROM items GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    def platform_pub_counts(self) -> dict:
        """每平台投递成败数: {platform: [ok_count, fail_count]}。
        B站以 bvid 为准, 其余平台扫 extra_pub JSON。"""
        rows = self.conn.execute(
            "SELECT bvid, extra_pub FROM items "
            "WHERE bvid!='' OR extra_pub!=''").fetchall()
        out: dict[str, list[int]] = {}
        for r in rows:
            if r["bvid"]:
                out.setdefault("bilibili", [0, 0])[0] += 1
            try:
                res = json.loads(r["extra_pub"] or "{}")
            except Exception:
                continue
            for p, v in res.items():
                if not isinstance(v, dict):
                    continue
                if p == "bilibili" and r["bvid"]:
                    continue            # bvid 已计成功, extra_pub 同腿不重复计
                slot = out.setdefault(p, [0, 0])
                slot[0 if v.get("ok") else 1] += 1
        return out

    def published_today(self) -> int:
        start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
        return self.conn.execute(
            "SELECT COUNT(*) c FROM items WHERE published_at >= ?", (start,)
        ).fetchone()["c"]

    def downloaded_today(self) -> int:
        """今日完成下载的条数，用于每日下载额度。"""
        start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
        return self.conn.execute(
            "SELECT COUNT(*) c FROM items WHERE downloaded_at >= ?", (start,)
        ).fetchone()["c"]
