"""Generate the rpt_isbank PBIR pages and theme from one layout spec.

Usage: uv run python scripts/build_report.py

Rewrites fabric/rpt_isbank.Report/definition/pages, the IS Bank theme, and the theme
registration in report.json. The semantic model keeps its technical names; every field
gets a business-friendly display name on the visual instead.
"""

import json
import shutil
from pathlib import Path

REPORT = Path(__file__).resolve().parents[1] / "fabric" / "rpt_isbank.Report"
DEFINITION = REPORT / "definition"
THEME_NAME = "IS_Bank_Theme.json"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"

NAVY = "#0B1F3A"
NAVY_2 = "#16325C"
GOLD = "#C9A227"
INK = "#1B2A41"
MUTED = "#5B6B82"
CANVAS = "#F3F4F7"
LINE = "#E1E5EB"
RED = "#B5523B"

# Canvas grid (1280 x 720): navy sidebar on the left, header, KPI row, two chart rows.
SIDEBAR_W = 210
X0, X1 = 226, 1264
KPI_Y, KPI_H = 76, 92
ROW1_Y, ROW2_Y, ROW_H = 180, 450, 258

THEME = {
    "name": "IS Bank",
    "dataColors": [NAVY, GOLD, "#2E6DA4", "#7A8FA6", "#E0B94A", "#4C9A8A", RED, "#9AA5B1"],
    "background": "#FFFFFF",
    "foreground": INK,
    "tableAccent": GOLD,
    "good": "#2E8B57",
    "neutral": GOLD,
    "bad": RED,
    "textClasses": {
        "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 24, "color": NAVY},
        "title": {"fontFace": "Segoe UI Semibold", "fontSize": 12, "color": NAVY},
        "header": {"fontFace": "Segoe UI Semibold", "fontSize": 11, "color": NAVY},
        "label": {"fontFace": "Segoe UI", "fontSize": 9, "color": MUTED},
    },
    "visualStyles": {
        "*": {
            "*": {
                "background": [
                    {"show": True, "color": {"solid": {"color": "#FFFFFF"}}, "transparency": 0}
                ],
                "border": [{"show": True, "color": {"solid": {"color": LINE}}, "radius": 8}],
                "dropShadow": [{"show": False}],
                "padding": [{"top": 10, "bottom": 8, "left": 12, "right": 12}],
                "title": [
                    {
                        "show": True,
                        "fontColor": {"solid": {"color": NAVY}},
                        "fontSize": 12,
                        "fontFamily": "Segoe UI Semibold",
                    }
                ],
                "categoryAxis": [
                    {
                        "showAxisTitle": False,
                        "labelColor": {"solid": {"color": MUTED}},
                        "fontSize": 9,
                    }
                ],
                "valueAxis": [
                    {
                        "showAxisTitle": False,
                        "labelColor": {"solid": {"color": MUTED}},
                        "fontSize": 9,
                        "gridlineColor": {"solid": {"color": LINE}},
                    }
                ],
                "legend": [{"labelColor": {"solid": {"color": MUTED}}, "fontSize": 9}],
            }
        },
        "page": {
            "*": {
                "background": [{"color": {"solid": {"color": CANVAS}}, "transparency": 0}],
                "outspace": [{"color": {"solid": {"color": CANVAS}}}],
            }
        },
    },
}


# ---------------------------------------------------------------- PBIR building blocks


def lit(value) -> dict:
    """A PBIR literal expression: strings quoted, numbers as doubles, booleans bare."""
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = f"{value}D"
    else:
        text = "'" + str(value).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": text}}}


def color(hex_code: str) -> dict:
    """A solid colour fill."""
    return {"solid": {"color": lit(hex_code)}}


def props(**kwargs) -> list[dict]:
    """One object entry; dict values pass through, everything else becomes a literal."""
    return [{"properties": {k: v if isinstance(v, dict) else lit(v) for k, v in kwargs.items()}}]


def field(ref: str) -> dict:
    """'m:table.Measure' or 'c:table.column' -> a PBIR field reference."""
    kind, rest = ref.split(":", 1)
    entity, prop = rest.split(".", 1)
    body = {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}
    return {"Measure" if kind == "m" else "Column": body}


def projection(ref: str, label: str | None = None) -> dict:
    """A field projection, with an optional business-friendly display name."""
    entity, prop = ref.split(":", 1)[1].split(".", 1)
    p = {"field": field(ref), "queryRef": f"{entity}.{prop}", "nativeQueryRef": prop}
    if label:
        p["displayName"] = label
    return p


def roles(**role_fields) -> dict:
    """Build a queryState from role -> [(field ref, display name)] lists."""
    return {
        role: {"projections": [projection(r, lab) for r, lab in items]}
        for role, items in role_fields.items()
    }


class Page:
    """One report page: its header text, visuals, and extra page properties."""

    def __init__(
        self, name: str, title: str, subtitle: str = "Data as of 31 Dec 2025 · Amounts in GHS"
    ):
        self.name, self.title, self.subtitle = name, title, subtitle
        self.visuals: list[dict] = []
        self.extra: dict = {}

    def add(self, name: str, x, y, w, h, visual: dict) -> None:
        """Place a visual; z order follows insertion so later visuals sit on top."""
        z = len(self.visuals) * 1000
        self.visuals.append(
            {
                "$schema": f"{SCHEMA}/visualContainer/2.7.0/schema.json",
                "name": f"{self.name}_{name}"[:50],
                "position": {"x": x, "y": y, "z": z, "height": h, "width": w, "tabOrder": z},
                "visual": visual,
            }
        )


def container(title: str | None, background: bool = True) -> dict:
    """Container formatting: an optional title, and optionally no card chrome."""
    out = {"title": props(show=bool(title), text=title or "")}
    if not background:
        out["background"] = props(show=False)
        out["border"] = props(show=False)
    return out


def chart(
    kind: str,
    title: str,
    query: dict,
    sort: list[tuple[str, str]] | None = None,
    objects: dict | None = None,
) -> dict:
    """A data visual with an optional explicit sort and formatting objects."""
    q = {"queryState": query}
    if sort:
        q["sortDefinition"] = {
            "sort": [{"field": field(r), "direction": d} for r, d in sort],
            "isDefaultSort": False,
        }
    return {
        "visualType": kind,
        "query": q,
        "objects": objects or {},
        "visualContainerObjects": container(title),
        "drillFilterOtherVisuals": True,
    }


MONTH = [("c:dim_date.year", "Year"), ("c:dim_date.month_name", "Month")]
MONTH_SORT = [("c:dim_date.year", "Ascending"), ("c:dim_date.month_name", "Ascending")]
MONTH_AXIS = {
    "categoryAxis": props(axisType="Categorical", concatenateLabels=True, showAxisTitle=False)
}


def line(title, measure, label):
    """A monthly trend on a Year > Month categorical axis."""
    return chart(
        "lineChart",
        title,
        roles(Category=MONTH, Y=[(measure, label)]),
        MONTH_SORT,
        {**MONTH_AXIS, "dataPoint": props(fill=color(NAVY)), "lineStyles": props(strokeWidth=2)},
    )


def bar(
    title, category, cat_label, measure, label, sort="value", kind="clusteredBarChart", fill=NAVY
):
    """A bar or column chart, sorted by value (default) or by category."""
    order = [(measure, "Descending")] if sort == "value" else [(category, "Ascending")]
    return chart(
        kind,
        title,
        roles(Category=[(category, cat_label)], Y=[(measure, label)]),
        order,
        {
            "dataPoint": props(fill=color(fill)),
            "labels": props(show=True, color=color(INK), fontSize=8),
            "valueAxis": props(show=False),
        },
    )


def donut(title, category, cat_label, measure, label):
    """A donut with a right-hand legend and percent-of-total labels."""
    return chart(
        "donutChart",
        title,
        roles(Category=[(category, cat_label)], Y=[(measure, label)]),
        objects={
            "legend": props(show=True, position="Right"),
            "labels": props(show=True, labelStyle="Percent of total", fontSize=8),
        },
    )


def card(measure, label, accent=NAVY):
    """A KPI card; the accent colour flags risk measures."""
    return {
        "visualType": "card",
        "query": {"queryState": roles(Values=[(measure, label)])},
        "objects": {
            "labels": props(color=color(accent), fontSize=22, fontFamily="Segoe UI Semibold"),
            "categoryLabels": props(show=True, color=color(MUTED), fontSize=9),
        },
        "visualContainerObjects": container(None),
        "drillFilterOtherVisuals": True,
    }


def textbox(runs: list[tuple[str, dict]], align="left") -> dict:
    """Static text made of styled runs."""
    return {
        "visualType": "textbox",
        "objects": {
            "general": [
                {
                    "properties": {
                        "paragraphs": [
                            {
                                "textRuns": [
                                    {"value": text, "textStyle": style} for text, style in runs
                                ],
                                "horizontalTextAlignment": align,
                            }
                        ]
                    }
                }
            ]
        },
        # No padding: the theme's card padding would clip text in short header boxes.
        "visualContainerObjects": {
            **container(None, background=False),
            "padding": props(top=0, bottom=0, left=4, right=4),
        },
        "drillFilterOtherVisuals": True,
    }


def shape(fill: str) -> dict:
    """A filled rectangle (the sidebar background)."""
    return {
        "visualType": "shape",
        "objects": {
            "shape": props(tileShape="rectangle"),
            "fill": props(show=True, fillColor=color(fill)),
            "outline": props(show=False),
        },
        "visualContainerObjects": container(None, background=False),
        "drillFilterOtherVisuals": True,
    }


def slicer(ref: str, label: str, group: str, mode: str = "Dropdown") -> dict:
    """A slicer synced across pages through its group name; mode Between gives a date range."""
    return {
        "visualType": "slicer",
        "query": {"queryState": roles(Values=[(ref, label)])},
        "objects": {
            "data": props(mode=mode),
            "header": props(show=True, fontColor=color(NAVY), textSize=9),
            "items": props(fontColor=color(INK), textSize=9),
        },
        "visualContainerObjects": {
            "title": props(show=False),
            "padding": props(top=4, bottom=4, left=8, right=8),
        },
        "syncGroup": {"groupName": group, "fieldChanges": True, "filterChanges": True},
        "drillFilterOtherVisuals": True,
    }


def navigator() -> dict:
    """A vertical page navigator in the sidebar, gold for the current page."""
    states = lambda prop, default, selected: [  # noqa: E731
        {"properties": {prop: color(default)}, "selector": {"id": "default"}},
        {"properties": {prop: color(selected)}, "selector": {"id": "selected"}},
    ]
    return {
        "visualType": "pageNavigator",
        "objects": {
            "layout": props(orientation=1),
            "fill": states("fillColor", NAVY_2, GOLD),
            "text": states("fontColor", "#FFFFFF", NAVY),
            "outline": props(show=False),
            "shape": props(tileShape="roundedRectangle"),
        },
        "visualContainerObjects": container(None, background=False),
        "drillFilterOtherVisuals": True,
    }


def back_button() -> dict:
    """The drill-through Back button."""
    default = {"id": "default"}
    return {
        "visualType": "actionButton",
        "objects": {
            "icon": [
                {
                    "properties": {
                        "show": lit(True),
                        "shapeType": lit("back"),
                        "lineColor": color(NAVY),
                        "lineWeight": lit(2),
                    },
                    "selector": default,
                }
            ],
            "text": [
                {
                    "properties": {
                        "show": lit(True),
                        "text": lit("Back"),
                        "fontColor": color(NAVY),
                        "fontSize": lit(10),
                    },
                    "selector": default,
                }
            ],
            "fill": [
                {
                    "properties": {"show": lit(True), "fillColor": color("#FFFFFF")},
                    "selector": default,
                }
            ],
            "outline": [
                {"properties": {"show": lit(True), "lineColor": color(LINE)}, "selector": default}
            ],
        },
        "visualContainerObjects": {
            "visualLink": props(show=True, type="Back"),
            "title": props(show=False),
            "background": props(show=False),
            "border": props(show=False),
        },
        "drillFilterOtherVisuals": True,
    }


def chrome(page: Page) -> None:
    """Sidebar (wordmark, navigation, synced slicers) and the page header."""
    white = {"fontFamily": "Segoe UI Semibold", "fontSize": "20pt", "color": "#FFFFFF"}
    gold = {"fontFamily": "Segoe UI", "fontSize": "9pt", "color": GOLD}
    page.add("sidebar", 0, 0, SIDEBAR_W, 720, shape(NAVY))
    page.add(
        "brand",
        10,
        12,
        190,
        70,
        textbox([("IS Bank", white), ("\nData & Analytics Platform", gold)]),
    )
    page.add("nav", 12, 88, 186, 180, navigator())
    # Period filters first, then organisation filters; each synced across pages.
    filters = [
        ("f_date", "c:dim_date.date", "Date range", "date", "Between", 104),
        ("f_year", "c:dim_date.year", "Year", "year", "Dropdown", 60),
        ("f_quarter", "c:dim_date.quarter_name", "Quarter", "quarter", "Dropdown", 60),
        ("f_week", "c:dim_date.week_label", "Week", "week", "Dropdown", 60),
        ("f_region", "c:dim_branch.region", "Region", "region", "Dropdown", 60),
        ("f_branch", "c:dim_branch.branch_name", "Branch", "branch", "Dropdown", 60),
    ]
    y = 278  # ends at 702: date range (two inputs) 104, dropdowns 60, gaps 4
    for name, ref, label, group, mode, height in filters:
        page.add(name, 12, y, 186, height, slicer(ref, label, group, mode))
        y += height + 4
    title = {"fontFamily": "Segoe UI Semibold", "fontSize": "18pt", "color": NAVY}
    sub = {"fontFamily": "Segoe UI", "fontSize": "9pt", "color": MUTED}
    page.add("title", X0 - 6, 6, 700, 40, textbox([(page.title, title)]))
    page.add("subtitle", X0 - 6, 44, 700, 24, textbox([(page.subtitle, sub)]))


def kpis(page: Page, items: list[tuple[str, str]]) -> None:
    """A row of equal-width KPI cards across the content area."""
    gap = 10
    width = (X1 - X0 - gap * (len(items) - 1)) / len(items)
    for i, (measure, label) in enumerate(items):
        accent = (
            RED
            if "risk" in label.lower() or "npl" in label.lower() or "default" in label.lower()
            else NAVY
        )
        page.add(
            f"kpi{i}",
            round(X0 + i * (width + gap)),
            KPI_Y,
            round(width),
            KPI_H,
            card(measure, label, accent),
        )


def columns(spec: list[float]) -> list[tuple[int, int]]:
    """Split the content width into columns by weight; returns (x, width) pairs."""
    gap, total = 10, X1 - X0
    usable = total - gap * (len(spec) - 1)
    out, x = [], X0
    for weight in spec:
        w = round(usable * weight / sum(spec))
        out.append((x, w))
        x += w + gap
    return out


# ---------------------------------------------------------------- pages

F = "fact_transaction"
BRANCH = ("c:dim_branch.branch_name", "Branch")


def executive() -> Page:
    """Executive summary: headline KPIs and trends."""
    p = Page("executive", "Executive summary")
    chrome(p)
    kpis(
        p,
        [
            ("m:dim_customer.Customers", "Customers"),
            (f"m:{F}.Active Customers", "Active customers"),
            ("m:fact_account_balance_monthly.Deposits", "Deposits (GHS)"),
            ("m:fact_loan.Loan Principal", "Loan book (GHS)"),
            ("m:fact_loan.NPL Ratio", "NPL ratio"),
            ("m:fact_gl_entry.Net Profit", "Net profit (GHS)"),
        ],
    )
    (a, aw), (b, bw) = columns([2, 1])
    p.add(
        "trend",
        a,
        ROW1_Y,
        aw,
        ROW_H,
        line("Transaction value by month", f"m:{F}.Transaction Value", "Transaction value"),
    )
    p.add(
        "region",
        b,
        ROW1_Y,
        bw,
        ROW_H,
        # Ten regions with a long tail: a sorted bar reads far better than a donut.
        bar(
            "Deposits by region (GHS)",
            "c:dim_branch.region",
            "Region",
            "m:fact_account_balance_monthly.Deposits",
            "Deposits",
            fill=GOLD,
        ),
    )
    (a, aw), (b, bw) = columns([1, 1])
    p.add(
        "profit",
        a,
        ROW2_Y,
        aw,
        ROW_H,
        bar("Net profit by branch", *BRANCH, "m:fact_gl_entry.Net Profit", "Net profit"),
    )
    p.add(
        "channel",
        b,
        ROW2_Y,
        bw,
        ROW_H,
        bar(
            "Transactions by channel",
            "c:dim_channel.channel",
            "Channel",
            f"m:{F}.Transactions",
            "Transactions",
            fill=GOLD,
        ),
    )
    return p


def branches() -> Page:
    """Branch performance: GL P&L and reconciliation breaks by branch."""
    p = Page(
        "branches",
        "Branch performance",
        "Branch P&L from the general ledger · right-click a branch to drill through",
    )
    chrome(p)
    kpis(
        p,
        [
            ("m:fact_gl_entry.Income", "Income (GHS)"),
            ("m:fact_gl_entry.Expense", "Expense (GHS)"),
            ("m:fact_gl_entry.Net Profit", "Net profit (GHS)"),
            ("m:fact_gl_entry.Cost-to-Income Ratio", "Cost-to-income"),
            ("m:recon_gl_fees.Reconciliation Breaks", "GL vs core breaks"),
        ],
    )
    (a, aw), (b, bw) = columns([3, 2])
    matrix = chart(
        "pivotTable",
        "Profit and loss by branch",
        roles(
            Rows=[BRANCH],
            Values=[
                ("m:fact_gl_entry.Income", "Income"),
                ("m:fact_gl_entry.Expense", "Expense"),
                ("m:fact_gl_entry.Net Profit", "Net profit"),
                ("m:fact_gl_entry.Cost-to-Income Ratio", "Cost-to-income"),
                ("m:fact_gl_entry.Profit Margin", "Margin"),
            ],
        ),
        [("m:fact_gl_entry.Net Profit", "Descending")],
        {
            "columnHeaders": props(fontColor=color("#FFFFFF"), backColor=color(NAVY), fontSize=9),
            "values": props(fontSize=9),
            "rowHeaders": props(fontSize=9),
            "total": props(fontColor=color(NAVY)),
        },
    )
    p.add("pnl", a, ROW1_Y, aw, ROW2_Y + ROW_H - ROW1_Y, matrix)
    p.add(
        "trend",
        b,
        ROW1_Y,
        bw,
        ROW_H,
        line("Net profit by month", "m:fact_gl_entry.Net Profit", "Net profit"),
    )
    p.add(
        "breaks",
        b,
        ROW2_Y,
        bw,
        ROW_H,
        bar(
            "GL vs core fee breaks by branch",
            *BRANCH,
            "m:recon_gl_fees.Reconciliation Breaks",
            "Breaks",
            fill=RED,
        ),
    )
    return p


def credit() -> Page:
    """Credit risk: loan status, NPL, defaults, and collections."""
    p = Page(
        "credit",
        "Credit risk",
        "Loan book as of 31 Dec 2025 · NPL = arrears + default over the open book",
    )
    chrome(p)
    kpis(
        p,
        [
            ("m:fact_loan.Open Loans", "Open loans"),
            ("m:fact_loan.Loan Principal", "Loan book (GHS)"),
            ("m:fact_loan.NPL Ratio", "NPL ratio"),
            ("m:fact_loan.Default Rate", "Default rate"),
            ("m:fact_loan_payment.Collection Rate", "Collection rate"),
            ("m:fact_loan.Avg Bureau Score", "Avg bureau score"),
        ],
    )
    (a, aw), (b, bw), (c, cw) = columns([1, 1, 1])
    p.add(
        "status",
        a,
        ROW1_Y,
        aw,
        ROW_H,
        donut("Loans by status", "c:fact_loan.loan_status", "Status", "m:fact_loan.Loans", "Loans"),
    )
    p.add(
        "employer",
        b,
        ROW1_Y,
        bw,
        ROW_H,
        bar(
            "Default rate by employment",
            "c:fact_loan.employer_type",
            "Employment",
            "m:fact_loan.Default Rate",
            "Default rate",
            fill=RED,
        ),
    )
    p.add(
        "product",
        c,
        ROW1_Y,
        cw,
        ROW_H,
        bar(
            "Loan book by product",
            "c:dim_product.product_name",
            "Product",
            "m:fact_loan.Loan Principal",
            "Loan book",
        ),
    )
    (a, aw), (b, bw) = columns([1, 1])
    p.add(
        "npl",
        a,
        ROW2_Y,
        aw,
        ROW_H,
        bar("NPL ratio by branch", *BRANCH, "m:fact_loan.NPL Ratio", "NPL ratio", fill=RED),
    )
    p.add(
        "missed",
        b,
        ROW2_Y,
        bw,
        ROW_H,
        line(
            "Missed instalment rate by month",
            "m:fact_loan_payment.Missed Instalment Rate",
            "Missed instalments",
        ),
    )
    return p


def fraud() -> Page:
    """Fraud and card activity: declines, night-time use, card-testing alerts."""
    p = Page(
        "fraud",
        "Fraud and card activity",
        "Card authorisations · alerts are rule-based signals, not confirmed fraud",
    )
    chrome(p)
    c = "fact_card_transaction"
    kpis(
        p,
        [
            (f"m:{c}.Card Transactions", "Card transactions"),
            (f"m:{c}.Card Value", "Card value (GHS)"),
            (f"m:{c}.Card Decline Rate", "Decline rate"),
            (f"m:{c}.Night-time Share", "Night-time share"),
            (f"m:{c}.Card-testing Alerts", "Card-testing alerts (risk)"),
        ],
    )
    (a, aw), (b, bw) = columns([1, 1])
    p.add(
        "hour",
        a,
        ROW1_Y,
        aw,
        ROW_H,
        bar(
            "Card transactions by hour of day",
            f"c:{c}.hour_of_day",
            "Hour",
            f"m:{c}.Card Transactions",
            "Transactions",
            sort="category",
            kind="clusteredColumnChart",
        ),
    )
    p.add(
        "alerts",
        b,
        ROW1_Y,
        bw,
        ROW_H,
        line("Card-testing alerts by month", f"m:{c}.Card-testing Alerts", "Alerts"),
    )
    (a, aw), (b, bw), (d, dw) = columns([1, 1, 1.4])
    p.add(
        "mode",
        a,
        ROW2_Y,
        aw,
        ROW_H,
        donut(
            "Card value by entry mode",
            f"c:{c}.entry_mode",
            "Entry mode",
            f"m:{c}.Card Value",
            "Card value",
        ),
    )
    p.add(
        "city",
        b,
        ROW2_Y,
        bw,
        ROW_H,
        bar(
            "Night-time card value by city",
            f"c:{c}.merchant_city",
            "City",
            f"m:{c}.Night-time Card Value",
            "Night-time value",
            fill=RED,
        ),
    )
    table = chart(
        "tableEx",
        "Merchants",
        roles(
            Values=[
                ("c:dim_merchant.merchant_name", "Merchant"),
                (f"m:{c}.Card Transactions", "Transactions"),
                (f"m:{c}.Card Decline Rate", "Decline rate"),
                (f"m:{c}.Night-time Share", "Night-time share"),
            ]
        ),
        [(f"m:{c}.Card Transactions", "Descending")],
        {
            "columnHeaders": props(fontColor=color("#FFFFFF"), backColor=color(NAVY), fontSize=9),
            "values": props(fontSize=9),
        },
    )
    p.add("merchants", d, ROW2_Y, dw, ROW_H, table)
    return p


def customers() -> Page:
    """Customers and digital: churn risk and app adoption."""
    p = Page(
        "customers",
        "Customers and digital",
        "At risk = activity in the last 3 months below half of the 3 months before",
    )
    chrome(p)
    d = "fact_digital_event"
    kpis(
        p,
        [
            (f"m:{F}.Active Customers", "Active customers"),
            (f"m:{F}.Customers at Risk", "Customers at risk"),
            (f"m:{F}.At-risk Rate", "At-risk rate"),
            (f"m:{d}.Digital Adoption", "Digital adoption"),
            (f"m:{d}.Biometric Login Share", "Biometric logins"),
            (f"m:{d}.Digital Payment Value", "Digital payments (GHS)"),
        ],
    )
    (a, aw), (b, bw) = columns([1, 1])
    p.add(
        "risk",
        a,
        ROW1_Y,
        aw,
        ROW_H,
        bar("At-risk rate by branch", *BRANCH, f"m:{F}.At-risk Rate", "At-risk rate", fill=RED),
    )
    p.add(
        "digital",
        b,
        ROW1_Y,
        bw,
        ROW_H,
        line("Digital customers by month", f"m:{d}.Digital Customers", "Digital customers"),
    )
    (a, aw), (b, bw), (c, cw) = columns([1, 1, 1])
    p.add(
        "events",
        a,
        ROW2_Y,
        aw,
        ROW_H,
        bar(
            "App activity by event",
            f"c:{d}.event_type",
            "Event",
            f"m:{d}.Digital Events",
            "Events",
            fill=GOLD,
        ),
    )
    p.add(
        "os",
        b,
        ROW2_Y,
        bw,
        ROW_H,
        donut(
            "App users by device", f"c:{d}.device_os", "Device", f"m:{d}.Digital Users", "App users"
        ),
    )
    p.add(
        "age",
        c,
        ROW2_Y,
        cw,
        ROW_H,
        bar(
            "Active customers by age band",
            "c:dim_customer.age_band",
            "Age band",
            f"m:{F}.Active Customers",
            "Active customers",
            sort="category",
        ),
    )
    return p


def branch_detail() -> Page:
    """Hidden drill-through page for a single branch."""
    p = Page("branch_detail", "Branch detail", "Drill-through from any branch · use Back to return")
    chrome(p)
    # A card on a text column needs an aggregation; Min is Power BI's "First" for text.
    name_card = card("c:dim_branch.branch_name", "Branch", GOLD)
    name_card["query"]["queryState"]["Values"]["projections"] = [
        {
            "field": {
                "Aggregation": {"Expression": field("c:dim_branch.branch_name"), "Function": 3}
            },
            "queryRef": "Min(dim_branch.branch_name)",
            "nativeQueryRef": "First branch_name",
            "displayName": "Branch",
        }
    ]
    p.add("name", 760, 10, 400, 58, name_card)
    p.add("back", 1170, 18, 94, 40, back_button())
    kpis(
        p,
        [
            (f"m:{F}.Active Customers", "Active customers"),
            ("m:fact_account_balance_monthly.Deposits", "Deposits (GHS)"),
            ("m:fact_loan.Loan Principal", "Loan book (GHS)"),
            ("m:fact_loan.NPL Ratio", "NPL ratio"),
            ("m:fact_gl_entry.Net Profit", "Net profit (GHS)"),
            ("m:fact_gl_entry.Cost-to-Income Ratio", "Cost-to-income"),
        ],
    )
    (a, aw), (b, bw) = columns([1, 1])
    p.add(
        "value",
        a,
        ROW1_Y,
        aw,
        ROW_H,
        line("Transaction value by month", f"m:{F}.Transaction Value", "Transaction value"),
    )
    p.add(
        "profit",
        b,
        ROW1_Y,
        bw,
        ROW_H,
        line("Net profit by month", "m:fact_gl_entry.Net Profit", "Net profit"),
    )
    (a, aw), (b, bw), (c, cw) = columns([1, 1, 1])
    p.add(
        "status",
        a,
        ROW2_Y,
        aw,
        ROW_H,
        donut("Loans by status", "c:fact_loan.loan_status", "Status", "m:fact_loan.Loans", "Loans"),
    )
    p.add(
        "channel",
        b,
        ROW2_Y,
        bw,
        ROW_H,
        bar(
            "Transactions by channel",
            "c:dim_channel.channel",
            "Channel",
            f"m:{F}.Transactions",
            "Transactions",
            fill=GOLD,
        ),
    )
    p.add(
        "events",
        c,
        ROW2_Y,
        cw,
        ROW_H,
        bar(
            "App activity by event",
            "c:fact_digital_event.event_type",
            "Event",
            "m:fact_digital_event.Digital Events",
            "Events",
        ),
    )
    branch = field("c:dim_branch.branch_name")
    p.extra = {
        "filterConfig": {
            "filters": [
                {
                    "name": "drill_branch",
                    "field": branch,
                    "type": "Categorical",
                    "howCreated": "Drillthrough",
                }
            ]
        },
        "pageBinding": {
            "name": "branch_drillthrough",
            "type": "Drillthrough",
            "referenceScope": "Default",
            "parameters": [
                {"name": "branch_param", "boundFilter": "drill_branch", "fieldExpr": branch}
            ],
        },
        "visibility": "HiddenInViewMode",
    }
    return p


# ---------------------------------------------------------------- write


def write_json(path: Path, data: dict) -> None:
    """Write JSON with LF line endings, as Fabric Git stores it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )


def main() -> None:
    """Rebuild every page, the theme file, and the theme registration."""
    pages = [executive(), branches(), credit(), fraud(), customers(), branch_detail()]
    root = DEFINITION / "pages"
    shutil.rmtree(root, ignore_errors=True)
    for page in pages:
        write_json(
            root / page.name / "page.json",
            {
                "$schema": f"{SCHEMA}/page/2.1.0/schema.json",
                "name": page.name,
                "displayName": page.title,
                "displayOption": "FitToPage",
                "height": 720,
                "width": 1280,
                **page.extra,
            },
        )
        for v in page.visuals:
            write_json(root / page.name / "visuals" / v["name"] / "visual.json", v)
    write_json(
        root / "pages.json",
        {
            "$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [p.name for p in pages],
            "activePageName": pages[0].name,
        },
    )

    write_json(REPORT / "StaticResources" / "RegisteredResources" / THEME_NAME, THEME)
    # report.json is owned by Desktop (it upgrades the schema), so only patch the theme in.
    report = json.loads((DEFINITION / "report.json").read_text(encoding="utf-8"))
    themes = report["themeCollection"]
    themes["customTheme"] = {
        "name": THEME_NAME,
        # Same shape as the base theme's: a string in older schemas, an object since 3.x.
        "reportVersionAtImport": themes["baseTheme"]["reportVersionAtImport"],
        "type": "RegisteredResources",
    }
    packages = [p for p in report.get("resourcePackages", []) if p["name"] != "RegisteredResources"]
    packages.append(
        {
            "name": "RegisteredResources",
            "type": "RegisteredResources",
            "items": [{"name": THEME_NAME, "path": THEME_NAME, "type": "CustomTheme"}],
        }
    )
    report["resourcePackages"] = packages
    write_json(DEFINITION / "report.json", report)
    print(f"{len(pages)} pages, {sum(len(p.visuals) for p in pages)} visuals -> {REPORT}")


if __name__ == "__main__":
    main()
