from __future__ import annotations

from typing import Any

from db.database import get_db


class AnalyticsModel:
    @staticmethod
    async def get_sales_trend_data(period: str = "30d") -> list[dict[str, Any]]:
        db = await get_db()
        days_limit = None
        if period == "7d":
            days_limit = 7
        elif period == "30d":
            days_limit = 30
        elif period == "90d":
            days_limit = 90

        if days_limit:
            query = f"""
                SELECT date(created_at) as sale_date,
                       COUNT(*) as orders_count,
                       COALESCE(SUM(amount), 0) as total_revenue,
                       COALESCE(SUM(data_gb), 0) as total_gb
                FROM invoices
                WHERE status = 'approved'
                  AND created_at >= datetime('now', '-{days_limit} days')
                GROUP BY date(created_at)
                ORDER BY sale_date ASC
            """
        else:
            query = """
                SELECT date(created_at) as sale_date,
                       COUNT(*) as orders_count,
                       COALESCE(SUM(amount), 0) as total_revenue,
                       COALESCE(SUM(data_gb), 0) as total_gb
                FROM invoices
                WHERE status = 'approved'
                GROUP BY date(created_at)
                ORDER BY sale_date ASC
            """

        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [
            {
                "date": row["sale_date"],
                "orders": row["orders_count"],
                "revenue": row["total_revenue"],
                "gb": row["total_gb"],
            }
            for row in rows
        ]

    @staticmethod
    async def get_top_volume_plans_data() -> list[dict[str, Any]]:
        db = await get_db()
        query = """
            SELECT data_gb,
                   COUNT(*) as orders_count,
                   COALESCE(SUM(amount), 0) as total_revenue
            FROM invoices
            WHERE status = 'approved' AND data_gb > 0
            GROUP BY data_gb
            ORDER BY orders_count DESC, total_revenue DESC
            LIMIT 10
        """
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        total_orders = sum(r["orders_count"] for r in rows) or 1
        return [
            {
                "gb": row["data_gb"],
                "orders": row["orders_count"],
                "revenue": row["total_revenue"],
                "pct": round((row["orders_count"] / total_orders) * 100, 1),
            }
            for row in rows
        ]

    @staticmethod
    async def get_top_duration_plans_data() -> list[dict[str, Any]]:
        db = await get_db()
        query = """
            SELECT duration_days,
                   COUNT(*) as orders_count,
                   COALESCE(SUM(amount), 0) as total_revenue
            FROM invoices
            WHERE status = 'approved' AND duration_days > 0
            GROUP BY duration_days
            ORDER BY orders_count DESC
        """
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        total_orders = sum(r["orders_count"] for r in rows) or 1
        return [
            {
                "days": row["duration_days"],
                "orders": row["orders_count"],
                "revenue": row["total_revenue"],
                "pct": round((row["orders_count"] / total_orders) * 100, 1),
            }
            for row in rows
        ]

    @staticmethod
    async def get_user_growth_data(period: str = "30d") -> list[dict[str, Any]]:
        db = await get_db()
        days_limit = None
        if period == "7d":
            days_limit = 7
        elif period == "30d":
            days_limit = 30
        elif period == "90d":
            days_limit = 90

        if days_limit:
            query = f"""
                SELECT date(joined_at) as join_date,
                       COUNT(*) as new_users
                FROM users
                WHERE joined_at >= datetime('now', '-{days_limit} days')
                GROUP BY date(joined_at)
                ORDER BY join_date ASC
            """
        else:
            query = """
                SELECT date(joined_at) as join_date,
                       COUNT(*) as new_users
                FROM users
                GROUP BY date(joined_at)
                ORDER BY join_date ASC
            """

        cursor = await db.execute(query)
        rows = await cursor.fetchall()

        res: list[dict[str, Any]] = []
        cum = 0
        for r in rows:
            cum += r["new_users"]
            res.append(
                {
                    "date": r["join_date"],
                    "new_users": r["new_users"],
                    "cumulative": cum,
                }
            )
        return res

    @staticmethod
    async def get_payments_breakdown_data() -> dict[str, Any]:
        db = await get_db()
        method_query = """
            SELECT COALESCE(payment_method, 'card') as method,
                   COUNT(*) as count,
                   COALESCE(SUM(amount), 0) as revenue
            FROM invoices
            WHERE status = 'approved'
            GROUP BY method
        """
        cursor = await db.execute(method_query)
        method_rows = await cursor.fetchall()
        methods = {
            r["method"]: {"count": r["count"], "revenue": r["revenue"]}
            for r in method_rows
        }

        status_query = """
            SELECT status,
                   COUNT(*) as count,
                   COALESCE(SUM(amount), 0) as amount
            FROM invoices
            GROUP BY status
        """
        cursor = await db.execute(status_query)
        status_rows = await cursor.fetchall()
        statuses = {
            r["status"]: {"count": r["count"], "amount": r["amount"]}
            for r in status_rows
        }

        return {
            "methods": methods,
            "statuses": statuses,
        }
