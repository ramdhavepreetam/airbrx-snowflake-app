"""
Airbrx Snowflake App — interactive UI.
Runs as a Streamlit in Snowflake app; auto-authenticated via get_active_session().
"""
import pandas as pd
import streamlit as st
from snowflake.snowpark.context import get_active_session

LOOKBACK_DAYS = 30

st.set_page_config(page_title="Airbrx — Cost Intelligence", layout="wide")


@st.cache_resource
def get_session():
    return get_active_session()


def run_query(sql: str) -> pd.DataFrame:
    try:
        session = get_session()
        return session.sql(sql).to_pandas()
    except Exception as e:
        st.error(f"Query error: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
st.sidebar.title("Airbrx")
page = st.sidebar.radio(
    "View",
    ["Coverage", "Redundant Spend", "Projected vs. Realized", "Per-Rule Attribution"],
)
lookback = st.sidebar.slider("Lookback days", min_value=7, max_value=90,
                              value=LOOKBACK_DAYS, step=7)
st.sidebar.markdown("---")
st.sidebar.caption("Costs from QUERY_ATTRIBUTION_HISTORY. Cache hits are invisible by design.")


# ---------------------------------------------------------------------------
# Screen 1 — Coverage
# ---------------------------------------------------------------------------
if page == "Coverage":
    st.title("Gateway Coverage")
    st.caption("Percentage of warehouse traffic routed through Airbrx.")

    with st.spinner("Loading..."):
        df = run_query(f"""
            SELECT d, gateway_routed, total,
                   ROUND(coverage_ratio * 100, 1) AS coverage_pct
            FROM state.coverage_daily
            WHERE d >= DATEADD('day', -{lookback}, CURRENT_DATE())
            ORDER BY d
        """)

    if df.empty:
        st.info("No coverage data yet — the analysis task is still populating this.")
    else:
        df["COVERAGE_PCT"] = pd.to_numeric(df["COVERAGE_PCT"], errors="coerce")
        df["TOTAL"]         = pd.to_numeric(df["TOTAL"],         errors="coerce")
        df["GATEWAY_ROUTED"] = pd.to_numeric(df["GATEWAY_ROUTED"], errors="coerce")

        col1, col2, col3 = st.columns(3)
        col1.metric("Avg Coverage",     f"{df['COVERAGE_PCT'].mean():.1f}%")
        col2.metric("Total Queries",    f"{int(df['TOTAL'].sum()):,}")
        col3.metric("Gateway Routed",   f"{int(df['GATEWAY_ROUTED'].sum()):,}")

        import plotly.express as px
        fig = px.area(df, x="D", y="COVERAGE_PCT",
                      labels={"D": "Date", "COVERAGE_PCT": "Coverage %"},
                      title="Daily Gateway Coverage %")
        fig.update_yaxes(range=[0, 100])
        st.plotly_chart(fig, use_container_width=True)

        untagged = 100 - df["COVERAGE_PCT"].mean()
        if untagged > 10:
            st.warning(
                f"**{untagged:.1f}% of spend is untagged** — this connection isn't "
                "routed through the Airbrx gateway yet."
            )


# ---------------------------------------------------------------------------
# Screen 2 — Redundant Spend
# ---------------------------------------------------------------------------
elif page == "Redundant Spend":
    st.title("Redundant Spend")
    st.caption("Top query patterns hitting the warehouse more than once.")

    with st.spinner("Loading..."):
        df = run_query(f"""
            SELECT ck,
                   COUNT(*)                                        AS execs,
                   ROUND(SUM(total_elapsed_time) / 1000.0, 1)     AS total_sec,
                   MIN(d)                                         AS first_seen,
                   MAX(d)                                         AS last_seen,
                   COALESCE(MAX(rule_id), 'unmatched')            AS rule_id
            FROM state.fingerprint_history
            WHERE d >= DATEADD('day', -{lookback}, CURRENT_DATE())
            GROUP BY ck
            HAVING COUNT(*) > 1
            ORDER BY execs DESC
            LIMIT 50
        """)

    if df.empty:
        st.info("No redundant queries yet — analysis task may still be running.")
    else:
        df["EXECS"] = pd.to_numeric(df["EXECS"], errors="coerce")
        st.metric("Redundant fingerprints", len(df))
        st.dataframe(
            df.rename(columns={
                "CK": "Fingerprint", "EXECS": "Executions",
                "TOTAL_SEC": "Total Duration (s)", "FIRST_SEEN": "First Seen",
                "LAST_SEEN": "Last Seen", "RULE_ID": "Rule",
            }),
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Screen 3 — Projected vs. Realized
# ---------------------------------------------------------------------------
elif page == "Projected vs. Realized":
    st.title("Projected vs. Realized Savings")

    with st.spinner("Loading..."):
        waterfall = run_query(f"""
            SELECT d, warehouse_name, SUM(usd) AS usd, layer
            FROM state.waterfall_daily
            WHERE d >= DATEADD('day', -{lookback}, CURRENT_DATE())
            GROUP BY d, warehouse_name, layer
            ORDER BY d
        """)
        effectiveness = run_query("""
            SELECT COALESCE(SUM(realized_usd), 0) AS total_realized
            FROM state.rule_effectiveness
        """)

    if waterfall.empty:
        st.info("No waterfall data yet — run the analysis task first.")
    else:
        waterfall["USD"] = pd.to_numeric(waterfall["USD"], errors="coerce")
        raw       = waterfall[waterfall["LAYER"] == "raw"]
        total_usd = raw["USD"].sum() if not raw.empty else 0
        realized  = pd.to_numeric(
            effectiveness["TOTAL_REALIZED"].iloc[0], errors="coerce"
        ) if not effectiveness.empty else 0

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Warehouse Spend", f"${total_usd:,.0f}")
        col2.metric("Realized Savings",
                    f"${realized:,.0f}" if realized else "Pending baseline")
        col3.metric("Savings Rate",
                    f"{(realized/total_usd*100):.1f}%"
                    if total_usd and realized else "—")

        import plotly.express as px
        if not raw.empty:
            fig = px.bar(raw, x="D", y="USD", color="WAREHOUSE_NAME",
                         labels={"D": "Date", "USD": "USD",
                                 "WAREHOUSE_NAME": "Warehouse"},
                         title="Daily Warehouse Spend by Warehouse")
            st.plotly_chart(fig, use_container_width=True)

        if not realized:
            st.info("Realized savings require 30+ days of baseline data.")


# ---------------------------------------------------------------------------
# Screen 4 — Per-Rule Attribution
# ---------------------------------------------------------------------------
elif page == "Per-Rule Attribution":
    st.title("Per-Rule Attribution")
    st.caption("Realized savings per Airbrx rule.")

    with st.spinner("Loading..."):
        df = run_query("""
            SELECT re.rule_id, ru.description, re.window,
                   re.baseline_rate, re.observed, re.avoided,
                   ROUND(re.realized_usd, 2) AS realized_usd,
                   re.updated_at
            FROM state.rule_effectiveness re
            LEFT JOIN state.rules ru ON re.rule_id = ru.rule_id
            ORDER BY re.realized_usd DESC NULLS LAST
        """)

    if df.empty:
        st.info("No rule attribution data yet.")
    else:
        st.dataframe(
            df.rename(columns={
                "RULE_ID": "Rule ID", "DESCRIPTION": "Description",
                "WINDOW": "Window", "BASELINE_RATE": "Baseline (exec/day)",
                "OBSERVED": "Observed", "AVOIDED": "Avoided",
                "REALIZED_USD": "Realized ($)", "UPDATED_AT": "Updated",
            }),
            use_container_width=True,
        )
