"""Finite offline scenarios: every displayed value comes from executed SQL."""

SEED_ID = "demo_commerce"
CSV = """order_id,month,region,category,revenue,cost,orders
O001,2026-01,East,Hardware,1200,720,12
O002,2026-01,West,Hardware,900,540,9
O003,2026-01,East,Software,800,160,8
O004,2026-01,West,Software,600,120,6
O005,2026-02,East,Hardware,1500,900,15
O006,2026-02,West,Hardware,1000,600,10
O007,2026-02,East,Software,1200,240,12
O008,2026-02,West,Software,800,160,8
"""

CASES = [
    {
        "id": "revenue",
        "question": "总收入是多少？",
        "title": "收入总览",
        "label": "总收入",
        "sql": "SELECT SUM(revenue) AS revenue FROM {table}",
        "column": "revenue",
        "expected": 8000,
        "unit": "USD",
        "matches": ["总收入", "total revenue"],
    },
    {
        "id": "orders",
        "question": "总订单数是多少？",
        "title": "订单总览",
        "label": "总订单数",
        "sql": "SELECT SUM(orders) AS orders FROM {table}",
        "column": "orders",
        "expected": 80,
        "unit": "orders",
        "matches": ["总订单", "total orders"],
    },
    {
        "id": "profit",
        "question": "总毛利是多少？",
        "title": "毛利分析",
        "label": "总毛利",
        "sql": "SELECT SUM(revenue - cost) AS profit FROM {table}",
        "column": "profit",
        "expected": 4560,
        "unit": "USD",
        "matches": ["总毛利", "total profit"],
    },
    {
        "id": "regions",
        "question": "按地区比较收入",
        "title": "地区收入",
        "label": "领先地区收入",
        "sql": "SELECT region,SUM(revenue) AS revenue FROM {table} "
        "GROUP BY region ORDER BY revenue DESC,region",
        "column": "revenue",
        "expected": 4700,
        "unit": "USD",
        "chart_label": "region",
        "matches": ["地区", "region"],
    },
    {
        "id": "categories",
        "question": "按品类比较收入",
        "title": "品类收入",
        "label": "领先品类收入",
        "sql": "SELECT category,SUM(revenue) AS revenue FROM {table} "
        "GROUP BY category ORDER BY revenue DESC,category",
        "column": "revenue",
        "expected": 4600,
        "unit": "USD",
        "chart_label": "category",
        "matches": ["品类", "category"],
    },
    {
        "id": "trend",
        "question": "查看月度收入趋势",
        "title": "月度收入",
        "label": "首月收入",
        "sql": "SELECT month,SUM(revenue) AS revenue FROM {table} GROUP BY month ORDER BY month",
        "column": "revenue",
        "expected": 3500,
        "unit": "USD",
        "chart_label": "month",
        "matches": ["月度", "monthly"],
    },
    {
        "id": "margin",
        "question": "整体毛利率是多少？",
        "title": "毛利率分析",
        "label": "整体毛利率",
        "sql": "SELECT ROUND(100.0 * SUM(revenue-cost) / NULLIF(SUM(revenue),0),2) "
        "AS margin FROM {table}",
        "column": "margin",
        "expected": 57.0,
        "unit": "%",
        "matches": ["毛利率", "profit margin"],
    },
]
EXAMPLES = [
    {"id": case["id"], "question": case["question"], "dataset_id": SEED_ID} for case in CASES
]


def seed_demo(store):
    try:
        return store.dataset(SEED_ID)[0]
    except KeyError:
        return store.import_csv("Demo Commerce", CSV, dataset_id=SEED_ID)


def match_case(question):
    # Only explicit fixture questions are covered; freeform/new questions require live mode.
    text = question.strip().casefold().rstrip("?？")
    return next(
        (
            case
            for case in CASES
            if text == case["question"].rstrip("?？") or text in case["matches"]
        ),
        None,
    )
