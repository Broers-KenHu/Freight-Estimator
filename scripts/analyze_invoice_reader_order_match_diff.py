from __future__ import annotations

import datetime as dt
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

import psycopg
import pymssql
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / "backend" / ".env"
REPORT_DIR = PROJECT_ROOT / "reports"


TRACKING_TERMS = ("tracking", "connote", "consignment", "awb", "waybill", "article", "shipment_no")
ORDER_TERMS = ("erp_order", "order_no", "order_number", "owner_order", "oms_order", "order_id")
PLATFORM_ORDER_TERMS = ("platform_order", "platform_reference", "rd3", "third_party", "reference_no")
INVOICE_TERMS = ("invoice_no", "invoice_number", "invoice_id", "bill_no")
AMOUNT_TERMS = ("actual_freight", "total_inc_gst", "charge_inc_gst", "amount_inc_gst", "total_amount", "freight_amount", "charge_amount", "amount")
STATUS_TERMS = ("match_status", "status", "match_result")


@dataclass(frozen=True)
class TableRef:
    schema: str
    table: str

    @property
    def full_name(self) -> str:
        return f"{self.schema}.{self.table}"


@dataclass
class Candidate:
    ref: TableRef
    row_count: int
    score: int
    columns: list[str]
    tracking_col: str
    order_col: str
    platform_order_col: str
    invoice_col: str
    amount_col: str
    status_col: str


def load_env(path: Path) -> dict[str, str]:
    env = dict(os.environ)
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def bracket(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def quote_sqlserver(ref: TableRef) -> str:
    return f"{bracket(ref.schema)}.{bracket(ref.table)}"


def normalize(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.upper()


def to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def pick_column(columns: list[str], terms: Iterable[str]) -> str:
    lowered = {col.lower(): col for col in columns}
    for term in terms:
        if term in lowered:
            return lowered[term]
    scored: list[tuple[int, str]] = []
    for col in columns:
        name = col.lower()
        score = 0
        for idx, term in enumerate(terms):
            if term in name:
                score += 100 - idx
        if score:
            scored.append((score, col))
    return sorted(scored, reverse=True)[0][1] if scored else ""


def score_table(ref: TableRef, columns: list[str]) -> int:
    name = ref.full_name.lower()
    column_text = " ".join(col.lower() for col in columns)
    score = 0
    if "erp_match_results" in name or "match_result" in name or "order_match" in name:
        score += 90
    for term in ("match", "matched", "normalized", "reconc", "fact", "order", "invoice"):
        if term in name:
            score += 12
    if any(term in column_text for term in TRACKING_TERMS):
        score += 30
    if any(term in column_text for term in ORDER_TERMS):
        score += 30
    if any(term in column_text for term in PLATFORM_ORDER_TERMS):
        score += 14
    if any(term in column_text for term in INVOICE_TERMS):
        score += 14
    if any(term in column_text for term in AMOUNT_TERMS):
        score += 8
    if "fact_invoice_order_normalized" in name:
        score += 5
    if ref.table.lower().startswith(("stg_", "tmp_", "temp_", "raw_")):
        score -= 20
    return score


def connect_invoice_reader(env: dict[str, str]):
    return pymssql.connect(
        server=env.get("INVOICE_SQLSERVER_HOST") or env.get("SQLSERVER_HOST") or "192.168.72.8",
        port=int(env.get("INVOICE_SQLSERVER_PORT") or env.get("SQLSERVER_PORT") or "1433"),
        user=env.get("INVOICE_SQLSERVER_USER") or env.get("SQLSERVER_USER"),
        password=env.get("INVOICE_SQLSERVER_PASSWORD") or env.get("SQLSERVER_PASSWORD"),
        database=env.get("INVOICE_SQLSERVER_DATABASE") or "invoiceReader",
        login_timeout=20,
        timeout=60,
        as_dict=True,
    )


def connect_local_postgres(env: dict[str, str]):
    database_url = env.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required in backend/.env")
    return psycopg.connect(database_url, connect_timeout=20, row_factory=dict_row)


def discover_invoice_candidates(conn) -> list[Candidate]:
    with conn.cursor() as cur:
        cur.execute("SET LOCK_TIMEOUT 5000")
        cur.execute(
            """
            SELECT
                s.name AS schema_name,
                t.name AS table_name,
                c.name AS column_name
            FROM sys.tables t
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            JOIN sys.columns c ON c.object_id = t.object_id
            WHERE t.is_ms_shipped = 0
              AND s.name NOT IN ('sys', 'INFORMATION_SCHEMA')
            ORDER BY s.name, t.name, c.column_id
            """
        )
        grouped: dict[TableRef, list[str]] = defaultdict(list)
        for row in cur.fetchall():
            grouped[TableRef(row["schema_name"], row["table_name"])].append(row["column_name"])

        row_counts: dict[TableRef, int] = {}
        for ref in grouped:
            cur.execute(
                """
                SELECT COALESCE(SUM(p.row_count), 0) AS row_count
                FROM sys.dm_db_partition_stats p
                WHERE p.object_id = OBJECT_ID(%s)
                  AND p.index_id IN (0, 1)
                """,
                (ref.full_name,),
            )
            row_counts[ref] = int(cur.fetchone()["row_count"] or 0)

    candidates: list[Candidate] = []
    for ref, columns in grouped.items():
        score = score_table(ref, columns)
        if score < 54:
            continue
        candidates.append(
            Candidate(
                ref=ref,
                row_count=row_counts.get(ref, 0),
                score=score,
                columns=columns,
                tracking_col=pick_column(columns, TRACKING_TERMS),
                order_col=pick_column(columns, ORDER_TERMS),
                platform_order_col=pick_column(columns, PLATFORM_ORDER_TERMS),
                invoice_col=pick_column(columns, INVOICE_TERMS),
                amount_col=pick_column(columns, AMOUNT_TERMS),
                status_col=pick_column(columns, STATUS_TERMS),
            )
        )
    return sorted(candidates, key=lambda item: (item.score, item.row_count), reverse=True)


def non_null_stats_sqlserver(conn, candidate: Candidate) -> dict[str, int]:
    stats: dict[str, int] = {}
    cols = [
        candidate.tracking_col,
        candidate.order_col,
        candidate.platform_order_col,
        candidate.invoice_col,
        candidate.amount_col,
        candidate.status_col,
    ]
    cols = [col for col in cols if col]
    if not cols:
        return stats
    expressions = [
        f"SUM(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), {bracket(col)}))), '') IS NULL THEN 0 ELSE 1 END) AS {bracket(col)}"
        for col in cols
    ]
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(expressions)} FROM {quote_sqlserver(candidate.ref)}")
        row = cur.fetchone() or {}
    for col in cols:
        stats[col] = int(row.get(col) or 0)
    return stats


def stream_invoice_reader_rows(conn, candidate: Candidate, batch_size: int = 20000):
    selected = {
        "tracking": candidate.tracking_col,
        "order_no": candidate.order_col,
        "platform_order_no": candidate.platform_order_col,
        "invoice_no": candidate.invoice_col,
        "amount": candidate.amount_col,
        "status": candidate.status_col,
    }
    select_parts = []
    for alias, col in selected.items():
        if col:
            if alias == "amount":
                select_parts.append(f"TRY_CONVERT(DECIMAL(18,4), {bracket(col)}) AS {bracket(alias)}")
            else:
                select_parts.append(f"CONVERT(NVARCHAR(500), {bracket(col)}) AS {bracket(alias)}")
        else:
            select_parts.append(f"CAST(NULL AS NVARCHAR(500)) AS {bracket(alias)}")
    sql = f"SELECT {', '.join(select_parts)} FROM {quote_sqlserver(candidate.ref)}"
    with conn.cursor() as cur:
        cur.execute(sql)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                yield row


def local_order_expression(candidate: Candidate) -> str:
    sql_order = (candidate.order_col or "").lower()
    if "owner" in sql_order:
        return "COALESCE(NULLIF(ess.erp_owner_order_no, ''), NULLIF(ess.erp_order_no, ''), NULLIF(iri.order_no, ''))"
    if "rd3" in sql_order or "third" in sql_order or "external" in sql_order:
        return "COALESCE(NULLIF(ess.third_party_order_no, ''), NULLIF(ess.platform_order_no, ''), NULLIF(ess.erp_order_no, ''), NULLIF(iri.order_no, ''))"
    if "platform" in sql_order or "reference" in sql_order:
        return "COALESCE(NULLIF(ess.platform_order_no, ''), NULLIF(ess.third_party_order_no, ''), NULLIF(ess.erp_order_no, ''), NULLIF(iri.order_no, ''))"
    return "COALESCE(NULLIF(ess.erp_order_no, ''), NULLIF(ess.erp_owner_order_no, ''), NULLIF(iri.order_no, ''))"


def local_platform_order_expression(candidate: Candidate) -> str:
    sql_ref = (candidate.platform_order_col or "").lower()
    if "rd3" in sql_ref or "third" in sql_ref or "external" in sql_ref:
        return "COALESCE(NULLIF(ess.third_party_order_no, ''), NULLIF(ess.platform_order_no, ''))"
    if "owner" in sql_ref:
        return "COALESCE(NULLIF(ess.erp_owner_order_no, ''), NULLIF(ess.erp_order_no, ''))"
    return "COALESCE(NULLIF(ess.platform_order_no, ''), NULLIF(ess.third_party_order_no, ''))"


def stream_local_rows(conn, candidate: Candidate, batch_size: int = 20000):
    order_expr = local_order_expression(candidate)
    platform_order_expr = local_platform_order_expression(candidate)
    query = """
        SELECT
            COALESCE(NULLIF(ics.tracking_no, ''), NULLIF(ess.tracking_no, ''), NULLIF(iri.consignment_no, '')) AS tracking,
            {order_expr} AS order_no,
            {platform_order_expr} AS platform_order_no,
            NULLIF(ics.invoice_no, '') AS invoice_no,
            iri.actual_freight AS amount,
            iri.match_status AS status
        FROM invoice_reconciliation_item iri
        LEFT JOIN invoice_charge_snapshot ics ON ics.id = iri.invoice_charge_snapshot_id
        LEFT JOIN erp_shipment_snapshot ess ON ess.id = iri.erp_shipment_snapshot_id
        WHERE iri.source_system LIKE 'invoiceReader%%'
           OR ics.id IS NOT NULL
    """.format(order_expr=order_expr, platform_order_expr=platform_order_expr)
    with conn.cursor(name="local_invoice_match_cursor") as cur:
        cur.itersize = batch_size
        cur.execute(query)
        while True:
            rows = cur.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                yield row


def build_index(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result = {
        "rows": 0,
        "tracking_rows": 0,
        "order_rows": 0,
        "invoice_rows": 0,
        "amount_rows": 0,
        "status_counts": defaultdict(int),
        "tracking_set": set(),
        "order_set": set(),
        "platform_order_set": set(),
        "invoice_tracking_set": set(),
        "pair_set": set(),
        "tracking_to_orders": defaultdict(set),
        "tracking_to_platform_orders": defaultdict(set),
        "tracking_amount_sum": defaultdict(Decimal),
    }
    for row in rows:
        result["rows"] += 1
        tracking = normalize(row.get("tracking"))
        order_no = normalize(row.get("order_no"))
        platform_order_no = normalize(row.get("platform_order_no"))
        invoice_no = normalize(row.get("invoice_no"))
        status = normalize(row.get("status")) or "(BLANK)"
        amount = to_decimal(row.get("amount"))

        result["status_counts"][status] += 1
        if tracking:
            result["tracking_rows"] += 1
            result["tracking_set"].add(tracking)
        if order_no:
            result["order_rows"] += 1
            result["order_set"].add(order_no)
        if platform_order_no:
            result["platform_order_set"].add(platform_order_no)
        if invoice_no:
            result["invoice_rows"] += 1
        if invoice_no and tracking:
            result["invoice_tracking_set"].add((invoice_no, tracking))
        if tracking and order_no:
            result["pair_set"].add((tracking, order_no))
            result["tracking_to_orders"][tracking].add(order_no)
        if tracking and platform_order_no:
            result["tracking_to_platform_orders"][tracking].add(platform_order_no)
        if tracking and amount is not None:
            result["amount_rows"] += 1
            result["tracking_amount_sum"][tracking] += amount
    return result


def ratio(part: int, whole: int) -> str:
    if not whole:
        return "0.00%"
    return f"{part / whole * 100:.2f}%"


def compare_indexes(sql_idx: dict[str, Any], local_idx: dict[str, Any]) -> dict[str, Any]:
    sql_tracking = sql_idx["tracking_set"]
    local_tracking = local_idx["tracking_set"]
    sql_orders = sql_idx["order_set"]
    local_orders = local_idx["order_set"]
    sql_pairs = sql_idx["pair_set"]
    local_pairs = local_idx["pair_set"]
    sql_invoice_tracking = sql_idx["invoice_tracking_set"]
    local_invoice_tracking = local_idx["invoice_tracking_set"]

    tracking_overlap = sql_tracking & local_tracking
    order_overlap = sql_orders & local_orders
    pair_overlap = sql_pairs & local_pairs
    invoice_tracking_overlap = sql_invoice_tracking & local_invoice_tracking

    different_order_for_same_tracking = 0
    same_tracking_sql_has_order_local_blank = 0
    same_tracking_local_has_order_sql_blank = 0
    for tracking in tracking_overlap:
        sql_order_set = sql_idx["tracking_to_orders"].get(tracking, set())
        local_order_set = local_idx["tracking_to_orders"].get(tracking, set())
        if sql_order_set and local_order_set and sql_order_set.isdisjoint(local_order_set):
            different_order_for_same_tracking += 1
        elif sql_order_set and not local_order_set:
            same_tracking_sql_has_order_local_blank += 1
        elif local_order_set and not sql_order_set:
            same_tracking_local_has_order_sql_blank += 1

    amount_compare_total = amount_match = amount_diff = 0
    for tracking in tracking_overlap:
        sql_amount = sql_idx["tracking_amount_sum"].get(tracking)
        local_amount = local_idx["tracking_amount_sum"].get(tracking)
        if sql_amount is None or local_amount is None:
            continue
        if tracking not in sql_idx["tracking_amount_sum"] or tracking not in local_idx["tracking_amount_sum"]:
            continue
        amount_compare_total += 1
        if abs(sql_amount - local_amount) <= Decimal("0.01"):
            amount_match += 1
        else:
            amount_diff += 1

    return {
        "sql_tracking_unique": len(sql_tracking),
        "local_tracking_unique": len(local_tracking),
        "tracking_overlap": len(tracking_overlap),
        "sql_tracking_only": len(sql_tracking - local_tracking),
        "local_tracking_only": len(local_tracking - sql_tracking),
        "sql_order_unique": len(sql_orders),
        "local_order_unique": len(local_orders),
        "order_overlap": len(order_overlap),
        "sql_order_only": len(sql_orders - local_orders),
        "local_order_only": len(local_orders - sql_orders),
        "sql_pair_unique": len(sql_pairs),
        "local_pair_unique": len(local_pairs),
        "pair_overlap": len(pair_overlap),
        "sql_pair_only": len(sql_pairs - local_pairs),
        "local_pair_only": len(local_pairs - sql_pairs),
        "sql_invoice_tracking_unique": len(sql_invoice_tracking),
        "local_invoice_tracking_unique": len(local_invoice_tracking),
        "invoice_tracking_overlap": len(invoice_tracking_overlap),
        "different_order_for_same_tracking": different_order_for_same_tracking,
        "same_tracking_sql_has_order_local_blank": same_tracking_sql_has_order_local_blank,
        "same_tracking_local_has_order_sql_blank": same_tracking_local_has_order_sql_blank,
        "amount_compare_total": amount_compare_total,
        "amount_match": amount_match,
        "amount_diff": amount_diff,
    }


def fetch_local_summary(conn) -> dict[str, Any]:
    queries = {
        "invoice_reconciliation_item": "SELECT COUNT(*) AS count FROM invoice_reconciliation_item",
        "invoice_charge_snapshot": "SELECT COUNT(*) AS count FROM invoice_charge_snapshot",
        "erp_shipment_snapshot": "SELECT COUNT(*) AS count FROM erp_shipment_snapshot",
    }
    result: dict[str, Any] = {}
    with conn.cursor() as cur:
        for key, query in queries.items():
            cur.execute(query)
            result[key] = int(cur.fetchone()["count"] or 0)
        cur.execute(
            """
            SELECT match_status, COUNT(*) AS count
            FROM invoice_reconciliation_item
            WHERE source_system LIKE 'invoiceReader%%'
            GROUP BY match_status
            ORDER BY match_status
            """
        )
        result["match_status"] = {row["match_status"] or "(BLANK)": int(row["count"] or 0) for row in cur.fetchall()}
        cur.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE erp_shipment_snapshot_id IS NOT NULL) AS with_erp_snapshot,
                COUNT(*) FILTER (WHERE invoice_charge_snapshot_id IS NOT NULL) AS with_invoice_snapshot,
                COUNT(*) FILTER (WHERE system_estimated_freight IS NOT NULL) AS with_system_estimate,
                COUNT(*) FILTER (WHERE estimated_freight IS NOT NULL) AS with_erp_estimate
            FROM invoice_reconciliation_item
            WHERE source_system LIKE 'invoiceReader%%'
            """
        )
        result["coverage"] = dict(cur.fetchone())
    return result


def status_table(status_counts: dict[str, int], limit: int = 12) -> str:
    rows = sorted(status_counts.items(), key=lambda item: item[1], reverse=True)[:limit]
    if not rows:
        return "无"
    return "\n".join(f"| `{status}` | {count:,} |" for status, count in rows)


def render_report(
    candidates: list[Candidate],
    selected: Candidate,
    selected_stats: dict[str, int],
    sql_idx: dict[str, Any],
    local_idx: dict[str, Any],
    comparison: dict[str, Any],
    local_summary: dict[str, Any],
) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    candidate_rows = []
    for cand in candidates[:12]:
        candidate_rows.append(
            "| `{}` | {:,} | {} | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                cand.ref.full_name,
                cand.row_count,
                cand.score,
                cand.tracking_col or "-",
                cand.order_col or "-",
                cand.platform_order_col or "-",
                cand.invoice_col or "-",
                cand.amount_col or "-",
            )
        )

    stats_rows = []
    for col, count in selected_stats.items():
        stats_rows.append(f"| `{col}` | {count:,} | {ratio(count, selected.row_count)} |")

    local_match_rows = []
    for status, count in sorted(local_summary.get("match_status", {}).items()):
        local_match_rows.append(f"| `{status}` | {count:,} |")

    cov = local_summary.get("coverage", {})
    candidate_table = "\n".join(candidate_rows) if candidate_rows else "| 无 | 0 | 0 | - | - | - | - | - |"
    stats_table = "\n".join(stats_rows) if stats_rows else "| 无可统计字段 | 0 | 0.00% |"
    local_match_table = "\n".join(local_match_rows) if local_match_rows else "| 无 | 0 |"

    report = f"""# InvoiceReader Order Match 与 CourieDelivery 匹配差异报告

生成时间：{now}

本报告只读取 SQL Server `invoiceReader` 和 CourieDelivery PostgreSQL 的元数据、匹配键和聚合结果。报告不输出原始 invoice/order/tracking 明细值。

## 结论摘要

- 在 `invoiceReader` 中识别到最像“order match 结果表”的候选表为 `{selected.ref.full_name}`，行数约 {selected.row_count:,}。
- CourieDelivery 当前本地 `invoice_reconciliation_item` 总行数为 {local_summary["invoice_reconciliation_item"]:,}，其中来自 InvoiceReader 的本地对账行按 `match_status` 见下方表格。
- 以 tracking 为主键对比：InvoiceReader 结果表唯一 tracking {comparison["sql_tracking_unique"]:,}，CourieDelivery 唯一 tracking {comparison["local_tracking_unique"]:,}，交集 {comparison["tracking_overlap"]:,}。
- InvoiceReader 有、CourieDelivery 没有的 tracking：{comparison["sql_tracking_only"]:,}；CourieDelivery 有、InvoiceReader 没有的 tracking：{comparison["local_tracking_only"]:,}。
- 同 tracking 但 ERP/order 映射不同的数量：{comparison["different_order_for_same_tracking"]:,}。
- tracking+order 成对匹配交集：{comparison["pair_overlap"]:,}，InvoiceReader 独有 pair：{comparison["sql_pair_only"]:,}，CourieDelivery 独有 pair：{comparison["local_pair_only"]:,}。

## InvoiceReader 候选结果表

| 候选表 | 行数 | 分数 | tracking 字段 | order 字段 | platform/ref 字段 | invoice 字段 | amount 字段 |
|---|---:|---:|---|---|---|---|---|
{candidate_table}

> 说明：分数由表名和字段名推断，例如是否包含 `order`、`match`、`normalized`、`tracking`、`invoice`、`amount` 等。该分数用于定位候选表，不代表业务正确性。

## 选中表字段覆盖

选中表：`{selected.ref.full_name}`

| 字段 | 非空行数 | 覆盖率 |
|---|---:|---:|
{stats_table}

选中字段：

| 用途 | 字段 |
|---|---|
| Tracking | `{selected.tracking_col or "-"}` |
| ERP/Order | `{selected.order_col or "-"}` |
| Platform/External Ref | `{selected.platform_order_col or "-"}` |
| Invoice | `{selected.invoice_col or "-"}` |
| Amount | `{selected.amount_col or "-"}` |
| Status | `{selected.status_col or "-"}` |

本次本地对齐口径：

| InvoiceReader 字段 | 本地对齐字段 |
|---|---|
| `{selected.order_col or "-"}` | `{local_order_expression(selected)}` |
| `{selected.platform_order_col or "-"}` | `{local_platform_order_expression(selected)}` |

## CourieDelivery 当前匹配结果

| 本地对象 | 行数 |
|---|---:|
| `invoice_reconciliation_item` | {local_summary["invoice_reconciliation_item"]:,} |
| `invoice_charge_snapshot` | {local_summary["invoice_charge_snapshot"]:,} |
| `erp_shipment_snapshot` | {local_summary["erp_shipment_snapshot"]:,} |

InvoiceReader 本地对账行状态：

| match_status | 行数 |
|---|---:|
{local_match_table}

本地覆盖：

| 覆盖项 | 行数 |
|---|---:|
| 已关联 `ErpShipmentSnapshot` | {int(cov.get("with_erp_snapshot") or 0):,} |
| 已关联 `InvoiceChargeSnapshot` | {int(cov.get("with_invoice_snapshot") or 0):,} |
| 有 ERP Est | {int(cov.get("with_erp_estimate") or 0):,} |
| 有 System Est | {int(cov.get("with_system_estimate") or 0):,} |

## 集合差异

| 维度 | InvoiceReader | CourieDelivery | 交集 | InvoiceReader 独有 | CourieDelivery 独有 |
|---|---:|---:|---:|---:|---:|
| Unique tracking | {comparison["sql_tracking_unique"]:,} | {comparison["local_tracking_unique"]:,} | {comparison["tracking_overlap"]:,} | {comparison["sql_tracking_only"]:,} | {comparison["local_tracking_only"]:,} |
| Unique order | {comparison["sql_order_unique"]:,} | {comparison["local_order_unique"]:,} | {comparison["order_overlap"]:,} | {comparison["sql_order_only"]:,} | {comparison["local_order_only"]:,} |
| Tracking + order pair | {comparison["sql_pair_unique"]:,} | {comparison["local_pair_unique"]:,} | {comparison["pair_overlap"]:,} | {comparison["sql_pair_only"]:,} | {comparison["local_pair_only"]:,} |
| Invoice + tracking pair | {comparison["sql_invoice_tracking_unique"]:,} | {comparison["local_invoice_tracking_unique"]:,} | {comparison["invoice_tracking_overlap"]:,} | - | - |

## 同 tracking 的映射差异

| 差异类型 | 数量 | 解释 |
|---|---:|---|
| 同 tracking 但 ERP/order 映射不同 | {comparison["different_order_for_same_tracking"]:,} | 两边都能找到 tracking，但 order 集合没有交集，需要检查字段口径或 normalize 规则 |
| InvoiceReader 有 order，本地同 tracking 没 order | {comparison["same_tracking_sql_has_order_local_blank"]:,} | 本地可能只导入了 invoice charge，未匹配 ERP shipment |
| 本地有 order，InvoiceReader 同 tracking 没 order | {comparison["same_tracking_local_has_order_sql_blank"]:,} | InvoiceReader result 表可能缺 order match 或字段选择不对 |

## 金额差异检查

金额字段是根据字段名推断的 `{selected.amount_col or "-"}`，只作为辅助检查，不直接代表最终实际运费口径。

| 项目 | 数量 |
|---|---:|
| 可按 tracking 对比金额 | {comparison["amount_compare_total"]:,} |
| 金额一致，误差 <= 0.01 | {comparison["amount_match"]:,} |
| 金额不同 | {comparison["amount_diff"]:,} |

## 初步判断

1. 如果 `{selected.ref.full_name}` 是 InvoiceReader 已有的 order match 结果表，它的覆盖范围和 CourieDelivery 当前 tracking-based reconciliation 不完全一致。
2. CourieDelivery 目前的主路径仍然是 `InvoiceChargeSnapshot.tracking_no -> ErpShipmentSnapshot.tracking_no`，再用 carrier/channel/service 消歧。
3. InvoiceReader 结果表可作为对账辅助来源，但不建议直接替代当前逻辑，除非确认它的 order 字段、tracking 字段、金额字段、GST 口径和多行聚合规则。
4. 下一步建议抽样核对 `same tracking but different order` 的明细，确认差异来自字段选择、tracking normalization、multi-package 分组，还是 InvoiceReader 结果表的历史匹配规则。

## 建议动作

- 将 `{selected.ref.full_name}` 作为候选辅助匹配源新增到设计文档，但先不要直接改写生产同步逻辑。
- 对 `InvoiceReader 独有 tracking` 做二次检查：是否来自当前系统未导入的 `invoice_detail_*` 表、tracking 格式差异、或历史 invoice 不在本地 batch 范围。
- 对 `CourieDelivery 独有 tracking` 做二次检查：是否来自 CSV/XLSX 手动上传、本地重新同步后的 ERP shipment、或 InvoiceReader result 表未覆盖的 carrier。
- 如果业务确认 InvoiceReader result 表更可信，可新增一个 `invoice_reader_order_match_snapshot` 本地表保存其结果，再和 `InvoiceChargeSnapshot`、`ErpShipmentSnapshot` 三方比对，不要在请求时直接连 `.8`。
"""
    return report


def main() -> None:
    env = load_env(ENV_PATH)
    REPORT_DIR.mkdir(exist_ok=True)

    with connect_invoice_reader(env) as sql_conn, connect_local_postgres(env) as pg_conn:
        candidates = discover_invoice_candidates(sql_conn)
        if not candidates:
            raise RuntimeError("No invoiceReader order-match candidate tables were found.")
        selected = candidates[0]
        selected_stats = non_null_stats_sqlserver(sql_conn, selected)
        sql_idx = build_index(stream_invoice_reader_rows(sql_conn, selected))
        local_summary = fetch_local_summary(pg_conn)
        local_idx = build_index(stream_local_rows(pg_conn, selected))
        comparison = compare_indexes(sql_idx, local_idx)

    report = render_report(candidates, selected, selected_stats, sql_idx, local_idx, comparison, local_summary)
    out_path = REPORT_DIR / f"invoice_reader_order_match_comparison_{dt.date.today():%Y%m%d}.md"
    out_path.write_text(report, encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
