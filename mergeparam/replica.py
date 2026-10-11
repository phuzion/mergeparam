"""Queries against the Toolforge wiki replicas (WikiProject filter only).

Since MediaWiki 1.45, categorylinks points at linktarget via cl_target_id.
page_assessments.pa_page_id is the article's page ID (not the talk page's).
"""

from __future__ import annotations

from . import config


class ReplicaUnavailable(RuntimeError):
    pass


def build_query(category: str, project: str, limit: int) -> tuple[str, list]:
    tracking = [c.replace(" ", "_") for c in config.TRACKING_CATEGORIES.values()]
    sql = [
        "SET STATEMENT max_statement_time=30 FOR",
        "SELECT DISTINCT p.page_title FROM page p",
        "JOIN categorylinks tcl ON tcl.cl_from = p.page_id",
        "JOIN linktarget tlt ON tlt.lt_id = tcl.cl_target_id",
        "  AND tlt.lt_namespace = 14",
        f"  AND tlt.lt_title IN ({', '.join(['%s'] * len(tracking))})",
    ]
    args: list = list(tracking)
    if category:
        sql += [
            "JOIN categorylinks ucl ON ucl.cl_from = p.page_id",
            "JOIN linktarget ult ON ult.lt_id = ucl.cl_target_id",
            "  AND ult.lt_namespace = 14 AND ult.lt_title = %s",
        ]
        args.append(category.replace(" ", "_"))
    if project:
        sql += [
            "JOIN page_assessments pa ON pa.pa_page_id = p.page_id",
            "JOIN page_assessments_projects pap ON pap.pap_project_id = pa.pa_project_id",
            "  AND pap.pap_project_title = %s",
        ]
        args.append(project)
    sql += ["WHERE p.page_namespace = 0", "ORDER BY RAND()", "LIMIT %s"]
    args.append(int(limit))
    return "\n".join(sql), args


def _connect():
    try:
        import pymysql
        return pymysql.connect(
            host=config.REPLICA_HOST, port=config.REPLICA_PORT, database=config.REPLICA_DB,
            read_default_file=config.REPLICA_CNF, charset="utf8mb4", connect_timeout=10,
        )
    except Exception as e:  # missing credentials, no tunnel, network
        raise ReplicaUnavailable(
            "The WikiProject filter needs the Toolforge database replicas, which "
            "aren't reachable right now.") from e


def random_titles(category: str, project: str, limit: int = 30) -> list[str]:
    sql, args = build_query(category, project, limit)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_text(r[0]).replace("_", " ") for r in rows]


def _text(value) -> str:
    return value.decode("utf-8") if isinstance(value, (bytes, bytearray)) else value
