from __future__ import annotations

import io
import logging
from typing import Any
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

from db.database import get_db
from utils.formatting import format_price, to_persian_digits

logger = logging.getLogger(__name__)

plt.rcParams["font.sans-serif"] = ["Vazirmatn", "DejaVu Sans", "sans-serif"]
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["axes.unicode_minus"] = False

BG_COLOR = "#0f172a"
CARD_COLOR = "#1e293b"
GRID_COLOR = "#334155"
TEXT_COLOR = "#f8fafc"
MUTED_COLOR = "#94a3b8"
ACCENT_CYAN = "#38bdf8"
ACCENT_PURPLE = "#a855f7"
ACCENT_GREEN = "#34d399"
ACCENT_ORANGE = "#f59e0b"
ACCENT_ROSE = "#f43f5e"
ACCENT_BLUE = "#6366f1"

CHART_PALETTE = [
    "#38bdf8",
    "#a855f7",
    "#34d399",
    "#f59e0b",
    "#f43f5e",
    "#ec4899",
    "#6366f1",
    "#14b8a6",
    "#fbbf24",
    "#818cf8",
]


def fa(text: Any) -> str:
    if text is None:
        return ""
    return str(text)


def _style_axes(ax: plt.Axes, title: str | None = None) -> None:
    ax.set_facecolor(CARD_COLOR)
    ax.tick_params(colors=MUTED_COLOR, labelsize=9)
    for spine in ax.spines.values():
        spine.set_color(GRID_COLOR)
        spine.set_linewidth(1.0)
    ax.grid(True, linestyle="--", alpha=0.35, color=GRID_COLOR)
    if title:
        ax.set_title(
            fa(title), color=TEXT_COLOR, fontsize=11, fontweight="bold", pad=10
        )


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
            WHERE status IN ('paid', 'approved')
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
            WHERE status IN ('paid', 'approved')
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


async def get_top_volume_plans_data() -> list[dict[str, Any]]:
    db = await get_db()
    query = """
        SELECT data_gb,
               COUNT(*) as orders_count,
               COALESCE(SUM(amount), 0) as total_revenue
        FROM invoices
        WHERE status IN ('paid', 'approved') AND data_gb > 0
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


async def get_top_duration_plans_data() -> list[dict[str, Any]]:
    db = await get_db()
    query = """
        SELECT duration_days,
               COUNT(*) as orders_count,
               COALESCE(SUM(amount), 0) as total_revenue
        FROM invoices
        WHERE status IN ('paid', 'approved') AND duration_days > 0
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

    res = []
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


async def get_payments_breakdown_data() -> dict[str, Any]:
    db = await get_db()
    method_query = """
        SELECT COALESCE(payment_method, 'card') as method,
               COUNT(*) as count,
               COALESCE(SUM(amount), 0) as revenue
        FROM invoices
        WHERE status IN ('paid', 'approved')
        GROUP BY method
    """
    cursor = await db.execute(method_query)
    method_rows = await cursor.fetchall()
    methods = {
        r["method"]: {"count": r["count"], "revenue": r["revenue"]} for r in method_rows
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
        r["status"]: {"count": r["count"], "amount": r["amount"]} for r in status_rows
    }

    return {
        "methods": methods,
        "statuses": statuses,
    }


def _render_fig_to_bytes(fig: plt.Figure) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=150,
        bbox_inches="tight",
        facecolor=fig.get_facecolor(),
        edgecolor="none",
    )
    plt.close(fig)
    buf.seek(0)
    return buf


async def render_overview_dashboard() -> tuple[io.BytesIO, str]:
    trend_data = await get_sales_trend_data("30d")
    top_vols = await get_top_volume_plans_data()
    top_durs = await get_top_duration_plans_data()
    payments_data = await get_payments_breakdown_data()

    fig = plt.figure(figsize=(12, 8.5), facecolor=BG_COLOR)
    gs = fig.add_gridspec(
        2, 2, hspace=0.35, wspace=0.25, left=0.08, right=0.95, top=0.90, bottom=0.08
    )

    fig.suptitle(
        fa("داشبورد جامع تحلیل آماری و وضعیت فروش (CandyPop Analytics)"),
        fontsize=14,
        fontweight="bold",
        color=TEXT_COLOR,
        y=0.96,
    )

    ax1 = fig.add_subplot(gs[0, 0])
    _style_axes(ax1, "روند درآمد ۳۰ روز اخیر")
    if trend_data:
        dates = [d["date"][5:] for d in trend_data]
        revenues = [d["revenue"] / 1000 for d in trend_data]
        ax1.plot(
            dates,
            revenues,
            color=ACCENT_CYAN,
            linewidth=2.2,
            marker="o",
            markersize=3,
            label=fa("درآمد (هزار تومان)"),
        )
        ax1.fill_between(dates, revenues, color=ACCENT_CYAN, alpha=0.2)
        if len(dates) > 7:
            ax1.xaxis.set_major_locator(ticker.MaxNLocator(6))
        ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{int(x):,}"))
    else:
        ax1.text(
            0.5,
            0.5,
            fa("داده‌ای برای ۳۰ روز اخیر ثبت نشده است"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    ax2 = fig.add_subplot(gs[0, 1])
    _style_axes(ax2, "پرفروش‌ترین پلن‌های حجم")
    if top_vols:
        labels = [fa(f"{v['gb']} گیگ") for v in reversed(top_vols[:5])]
        counts = [v["orders"] for v in reversed(top_vols[:5])]
        bars = ax2.barh(
            labels, counts, color=ACCENT_PURPLE, height=0.55, edgecolor="none"
        )
        for bar in bars:
            w = bar.get_width()
            ax2.text(
                w + 0.1,
                bar.get_y() + bar.get_height() / 2,
                f"{int(w)}",
                va="center",
                ha="left",
                color=TEXT_COLOR,
                fontsize=8,
            )
    else:
        ax2.text(
            0.5,
            0.5,
            fa("داده‌ای ثبت نشده است"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    ax3 = fig.add_subplot(gs[1, 0])
    _style_axes(ax3, "سهم مدت زمان‌های اشتراک")
    if top_durs:
        labels = [fa(f"{d['days']} روز ({d['pct']}%)") for d in top_durs]
        sizes = [d["orders"] for d in top_durs]
        wedges, _ = ax3.pie(
            sizes,
            labels=labels,
            colors=CHART_PALETTE[: len(sizes)],
            wedgeprops={"width": 0.45, "edgecolor": BG_COLOR, "linewidth": 2},
            textprops={"color": TEXT_COLOR, "fontsize": 8.5},
            startangle=140,
        )
    else:
        ax3.text(
            0.5,
            0.5,
            fa("داده‌ای ثبت نشده است"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    ax4 = fig.add_subplot(gs[1, 1])
    _style_axes(ax4, "روش‌های پرداخت فاکتورها")
    methods = payments_data.get("methods", {})
    card_cnt = methods.get("card", {}).get("count", 0)
    wallet_cnt = methods.get("wallet", {}).get("count", 0)
    if card_cnt > 0 or wallet_cnt > 0:
        p_labels = [fa("کارت به کارت"), fa("کیف پول")]
        p_sizes = [card_cnt, wallet_cnt]
        p_colors = [ACCENT_GREEN, ACCENT_ORANGE]
        ax4.pie(
            p_sizes,
            labels=p_labels,
            colors=p_colors,
            autopct="%1.1f%%",
            wedgeprops={"width": 0.45, "edgecolor": BG_COLOR, "linewidth": 2},
            textprops={"color": TEXT_COLOR, "fontsize": 9},
            startangle=90,
        )
    else:
        ax4.text(
            0.5,
            0.5,
            fa("تراکنشی یافت نشد"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    buf = _render_fig_to_bytes(fig)

    total_rev_30d = sum(d["revenue"] for d in trend_data)
    total_orders_30d = sum(d["orders"] for d in trend_data)
    total_gb_30d = sum(d["gb"] for d in trend_data)
    top_plan_str = (
        f"{top_vols[0]['gb']} گیگابایت ({top_vols[0]['pct']}%)" if top_vols else "—"
    )

    caption = (
        "📊 <b>داشبورد جامع تحلیل آماری و وضعیت فروش</b>\n\n"
        f"🗓 <b>خلاصه عملکرد ۳۰ روز اخیر:</b>\n"
        f"  • 💵 مجموع درآمد: <b>{format_price(total_rev_30d)}</b>\n"
        f"  • 🛒 کل فروش موفق: <b>{to_persian_digits(total_orders_30d)}</b> سفارش\n"
        f"  • 🌐 حجم کل فروخته‌شده: <b>{to_persian_digits(total_gb_30d)}</b> گیگابایت\n"
        f"  • 🏆 پرفروش‌ترین پلن حجم: <b>{top_plan_str}</b>\n\n"
        "💡 <i>برای مشاهده نمودارهای تفکیکی با جزئیات بیشتر، از گزینه‌های زیر استفاده کنید.</i>"
    )
    return buf, caption


async def render_sales_trend_chart(period: str = "30d") -> tuple[io.BytesIO, str]:
    trend_data = await get_sales_trend_data(period)

    period_titles = {
        "7d": "۷ روز گذشته",
        "30d": "۳۰ روز گذشته",
        "90d": "۳ ماه گذشته (۹۰ روز)",
        "all": "کل دوران",
    }
    p_title = period_titles.get(period, "۳۰ روز گذشته")

    fig, ax1 = plt.subplots(figsize=(10, 5.5), facecolor=BG_COLOR)
    _style_axes(ax1, f"روند فروش و درآمد ({p_title})")

    total_revenue = sum(d["revenue"] for d in trend_data)
    total_orders = sum(d["orders"] for d in trend_data)
    total_gb = sum(d["gb"] for d in trend_data)

    if trend_data:
        dates = [d["date"][5:] for d in trend_data]
        revenues = [d["revenue"] / 1000 for d in trend_data]
        orders = [d["orders"] for d in trend_data]

        ax1.plot(
            dates,
            revenues,
            color=ACCENT_CYAN,
            linewidth=2.5,
            marker="o",
            markersize=4,
            label=fa("درآمد (هزار تومان)"),
        )
        ax1.fill_between(dates, revenues, color=ACCENT_CYAN, alpha=0.18)
        ax1.set_ylabel(
            fa("درآمد (هزار تومان)"), color=ACCENT_CYAN, fontsize=10, labelpad=8
        )
        ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{int(x):,}"))

        ax2 = ax1.twinx()
        ax2.set_facecolor("none")
        for spine in ax2.spines.values():
            spine.set_color(GRID_COLOR)
        ax2.tick_params(colors=MUTED_COLOR, labelsize=9)
        ax2.plot(
            dates,
            orders,
            color=ACCENT_ORANGE,
            linewidth=1.8,
            linestyle="--",
            marker="s",
            markersize=4,
            label=fa("تعداد سفارش"),
        )
        ax2.set_ylabel(fa("تعداد سفارش"), color=ACCENT_ORANGE, fontsize=10, labelpad=8)
        ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

        if len(dates) > 10:
            ax1.xaxis.set_major_locator(ticker.MaxNLocator(8))

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(
            lines1 + lines2,
            labels1 + labels2,
            loc="upper left",
            facecolor=CARD_COLOR,
            edgecolor=GRID_COLOR,
            labelcolor=TEXT_COLOR,
            fontsize=8.5,
        )
    else:
        ax1.text(
            0.5,
            0.5,
            fa("هیچ داده‌ای در این بازه زمانی یافت نشد"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
            fontsize=11,
        )

    buf = _render_fig_to_bytes(fig)

    avg_order = int(round(total_revenue / total_orders)) if total_orders > 0 else 0
    caption = (
        f"📈 <b>تحلیل روند فروش و درآمد ╸ {p_title}</b>\n\n"
        f"  • 💵 درآمد کل در این بازه: <b>{format_price(total_revenue)}</b>\n"
        f"  • 🛒 تعداد کل سفارشات موفق: <b>{to_persian_digits(total_orders)}</b> سفارش\n"
        f"  • 🌐 حجم کل ترافیک فروخته‌شده: <b>{to_persian_digits(total_gb)}</b> گیگابایت\n"
        f"  • 📊 میانگین ارزش هر سفارش: <b>{format_price(avg_order)}</b>\n\n"
        "💡 <i>برای تغییر بازه زمانی از دکمه‌های زیر استفاده کنید:</i>"
    )
    return buf, caption


async def render_volume_plans_chart() -> tuple[io.BytesIO, str]:
    top_vols = await get_top_volume_plans_data()

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5), facecolor=BG_COLOR)
    _style_axes(ax1, "تعداد سفارشات هر پلن حجم")
    _style_axes(ax2, "سهم درآمدی پلن‌های حجم")

    if top_vols:
        labels = [fa(f"{v['gb']} گیگ") for v in top_vols[:7]]
        counts = [v["orders"] for v in top_vols[:7]]
        revenues = [v["revenue"] / 1000 for v in top_vols[:7]]

        bars = ax1.bar(
            labels,
            counts,
            color=CHART_PALETTE[: len(labels)],
            width=0.55,
            edgecolor="none",
        )
        for bar in bars:
            h = bar.get_height()
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                h + 0.1,
                f"{int(h)}",
                ha="center",
                va="bottom",
                color=TEXT_COLOR,
                fontsize=8.5,
            )
        ax1.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

        ax2.pie(
            revenues,
            labels=labels,
            colors=CHART_PALETTE[: len(labels)],
            autopct="%1.1f%%",
            wedgeprops={"width": 0.45, "edgecolor": BG_COLOR, "linewidth": 2},
            textprops={"color": TEXT_COLOR, "fontsize": 8.5},
            startangle=90,
        )
    else:
        ax1.text(
            0.5, 0.5, fa("داده‌ای یافت نشد"), ha="center", va="center", color=MUTED_COLOR
        )
        ax2.text(
            0.5, 0.5, fa("داده‌ای یافت نشد"), ha="center", va="center", color=MUTED_COLOR
        )

    buf = _render_fig_to_bytes(fig)

    lines = []
    for i, v in enumerate(top_vols[:5]):
        lines.append(
            f"  {to_persian_digits(i + 1)}. <b>پلن {to_persian_digits(v['gb'])} گیگابایت:</b> "
            f"<b>{to_persian_digits(v['orders'])}</b> خرید ({v['pct']}%) ── <code>{format_price(v['revenue'])}</code>"
        )
    list_str = "\n".join(lines) if lines else "<i>داده‌ای ثبت نشده است.</i>"

    caption = (
        "📦 <b>تحلیل و آمار پرفروش‌ترین پلن‌های حجم</b>\n\n"
        "📋 <b>رتبه‌بندی محبوب‌ترین حجم‌ها:</b>\n"
        f"{list_str}\n\n"
        "💡 <i>این آمار بر اساس سفارشات پرداخت‌شده محاسبه گردیده است.</i>"
    )
    return buf, caption


async def render_duration_plans_chart() -> tuple[io.BytesIO, str]:
    top_durs = await get_top_duration_plans_data()

    fig, ax = plt.subplots(figsize=(8.5, 5), facecolor=BG_COLOR)
    _style_axes(ax, "توزیع سهم فروش مدت زمان‌های اشتراک")

    if top_durs:
        labels = [fa(f"{d['days']} روز ({d['pct']}%)") for d in top_durs]
        sizes = [d["orders"] for d in top_durs]
        ax.pie(
            sizes,
            labels=labels,
            colors=CHART_PALETTE[: len(sizes)],
            autopct="%1.1f%%",
            pctdistance=0.75,
            wedgeprops={"width": 0.45, "edgecolor": BG_COLOR, "linewidth": 2},
            textprops={"color": TEXT_COLOR, "fontsize": 9.5},
            startangle=140,
        )
    else:
        ax.text(
            0.5, 0.5, fa("داده‌ای یافت نشد"), ha="center", va="center", color=MUTED_COLOR
        )

    buf = _render_fig_to_bytes(fig)

    lines = []
    for i, d in enumerate(top_durs):
        months = d["days"] // 30
        title = (
            f"{to_persian_digits(months)} ماهه ({to_persian_digits(d['days'])} روز)"
            if (d["days"] % 30 == 0 and months > 0)
            else f"{to_persian_digits(d['days'])} روز"
        )
        lines.append(
            f"  • <b>{title}:</b> <b>{to_persian_digits(d['orders'])}</b> خرید ({d['pct']}%) ── <code>{format_price(d['revenue'])}</code>"
        )
    list_str = "\n".join(lines) if lines else "<i>داده‌ای ثبت نشده است.</i>"

    caption = (
        "⏱ <b>تحلیل محبوبیت مدت زمان‌های اشتراک</b>\n\n"
        "📋 <b>توزیع زمانی خرید کاربران:</b>\n"
        f"{list_str}\n\n"
        "💡 <i>نمودار فوق نشان‌دهنده ترجیح کاربران در انتخاب طول مدت اعتبار سرویس است.</i>"
    )
    return buf, caption


async def render_user_growth_chart(period: str = "30d") -> tuple[io.BytesIO, str]:
    growth_data = await get_user_growth_data(period)

    period_titles = {
        "7d": "۷ روز گذشته",
        "30d": "۳۰ روز گذشته",
        "90d": "۳ ماه گذشته",
        "all": "کل دوران",
    }
    p_title = period_titles.get(period, "۳۰ روز گذشته")

    fig, ax1 = plt.subplots(figsize=(10, 5), facecolor=BG_COLOR)
    _style_axes(ax1, f"روند ثبت‌نام و رشد کاربران ({p_title})")

    total_new = sum(d["new_users"] for d in growth_data)

    if growth_data:
        dates = [d["date"][5:] for d in growth_data]
        new_users = [d["new_users"] for d in growth_data]
        cum_users = [d["cumulative"] for d in growth_data]

        ax1.plot(
            dates,
            cum_users,
            color=ACCENT_GREEN,
            linewidth=2.5,
            marker="o",
            markersize=3,
            label=fa("رشد تجمعی کاربران"),
        )
        ax1.fill_between(dates, cum_users, color=ACCENT_GREEN, alpha=0.15)
        ax1.set_ylabel(fa("تعداد تجمعی"), color=ACCENT_GREEN, fontsize=10)

        ax2 = ax1.twinx()
        ax2.set_facecolor("none")
        for spine in ax2.spines.values():
            spine.set_color(GRID_COLOR)
        ax2.tick_params(colors=MUTED_COLOR, labelsize=9)
        ax2.bar(
            dates,
            new_users,
            color=ACCENT_CYAN,
            alpha=0.4,
            width=0.4,
            label=fa("کاربران جدید روزانه"),
        )
        ax2.set_ylabel(fa("ثبت‌نام روزانه"), color=ACCENT_CYAN, fontsize=10)
        ax2.yaxis.set_major_locator(ticker.MaxNLocator(integer=True))

        if len(dates) > 10:
            ax1.xaxis.set_major_locator(ticker.MaxNLocator(8))

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(
            lines1 + lines2,
            labels1 + labels2,
            loc="upper left",
            facecolor=CARD_COLOR,
            edgecolor=GRID_COLOR,
            labelcolor=TEXT_COLOR,
            fontsize=8.5,
        )
    else:
        ax1.text(
            0.5,
            0.5,
            fa("داده‌ای در این بازه یافت نشد"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    buf = _render_fig_to_bytes(fig)

    avg_daily = round(total_new / len(growth_data), 1) if growth_data else 0
    caption = (
        f"👥 <b>تحلیل روند جذب و رشد کاربران ╸ {p_title}</b>\n\n"
        f"  • 👤 ثبت‌نام جدید در این بازه: <b>{to_persian_digits(total_new)}</b> کاربر\n"
        f"  • 📈 میانگین ورود روزانه: <b>{to_persian_digits(avg_daily)}</b> کاربر در روز\n\n"
        "💡 <i>برای تغییر بازه زمانی از دکمه‌های زیر استفاده کنید:</i>"
    )
    return buf, caption


async def render_payments_chart() -> tuple[io.BytesIO, str]:
    data = await get_payments_breakdown_data()
    methods = data.get("methods", {})
    statuses = data.get("statuses", {})

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5), facecolor=BG_COLOR)
    _style_axes(ax1, "روش‌های پرداخت موفق")
    _style_axes(ax2, "وضعیت کل فاکتورهای صادره")

    card_cnt = methods.get("card", {}).get("count", 0)
    wallet_cnt = methods.get("wallet", {}).get("count", 0)

    if card_cnt > 0 or wallet_cnt > 0:
        ax1.pie(
            [card_cnt, wallet_cnt],
            labels=[fa("کارت به کارت"), fa("کیف پول")],
            colors=[ACCENT_BLUE, ACCENT_ORANGE],
            autopct="%1.1f%%",
            wedgeprops={"width": 0.45, "edgecolor": BG_COLOR, "linewidth": 2},
            textprops={"color": TEXT_COLOR, "fontsize": 9},
            startangle=90,
        )
    else:
        ax1.text(
            0.5,
            0.5,
            fa("تراکنشی یافت نشد"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    paid_cnt = statuses.get("paid", {}).get("count", 0) + statuses.get(
        "approved", {}
    ).get("count", 0)
    pend_cnt = statuses.get("pending", {}).get("count", 0)
    rej_cnt = statuses.get("rejected", {}).get("count", 0)
    exp_cnt = statuses.get("expired", {}).get("count", 0)

    st_labels = []
    st_sizes = []
    st_colors = []
    if paid_cnt > 0:
        st_labels.append(fa(f"موفق ({paid_cnt})"))
        st_sizes.append(paid_cnt)
        st_colors.append(ACCENT_GREEN)
    if pend_cnt > 0:
        st_labels.append(fa(f"در انتظار ({pend_cnt})"))
        st_sizes.append(pend_cnt)
        st_colors.append(ACCENT_ORANGE)
    if rej_cnt > 0:
        st_labels.append(fa(f"ردشده ({rej_cnt})"))
        st_sizes.append(rej_cnt)
        st_colors.append(ACCENT_ROSE)
    if exp_cnt > 0:
        st_labels.append(fa(f"منقضی ({exp_cnt})"))
        st_sizes.append(exp_cnt)
        st_colors.append(MUTED_COLOR)

    if st_sizes:
        ax2.pie(
            st_sizes,
            labels=st_labels,
            colors=st_colors,
            autopct="%1.1f%%",
            wedgeprops={"width": 0.45, "edgecolor": BG_COLOR, "linewidth": 2},
            textprops={"color": TEXT_COLOR, "fontsize": 8.5},
            startangle=140,
        )
    else:
        ax2.text(
            0.5,
            0.5,
            fa("فاکتوری یافت نشد"),
            ha="center",
            va="center",
            color=MUTED_COLOR,
        )

    buf = _render_fig_to_bytes(fig)

    total_invoices = sum(s["count"] for s in statuses.values())
    conversion_rate = (
        round((paid_cnt / total_invoices * 100), 1) if total_invoices > 0 else 0
    )

    caption = (
        "💳 <b>تحلیل روش‌های پرداخت و بازدهی فاکتورها</b>\n\n"
        f"  • 🟢 نرخ موفقیت پرداخت فاکتورها: <b>{to_persian_digits(conversion_rate)}%</b>\n"
        f"  • 💳 سهم کارت به کارت: <b>{to_persian_digits(card_cnt)}</b> تراکنش ({format_price(methods.get('card', {}).get('revenue', 0))})\n"
        f"  • 👛 سهم کیف پول: <b>{to_persian_digits(wallet_cnt)}</b> تراکنش ({format_price(methods.get('wallet', {}).get('revenue', 0))})\n"
        f"  • 🟡 در انتظار تأیید: <b>{to_persian_digits(pend_cnt)}</b> فاکتور\n"
        f"  • 🔴 لغوشده یا منقضی: <b>{to_persian_digits(rej_cnt + exp_cnt)}</b> فاکتور\n\n"
        "💡 <i>این آمار جهت ارزیابی قیف فروش و ترجیحات پرداخت کاربران مفید است.</i>"
    )
    return buf, caption
