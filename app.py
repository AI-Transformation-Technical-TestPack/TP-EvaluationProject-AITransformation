"""Streamlit web interface — TP brand colors."""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

TP_RED = "#E61E2A"
TP_DARK = "#1A1A2E"

st.set_page_config(
    page_title="Billing Validation — TP Group",
    page_icon="🔍",
    layout="wide",
)

st.markdown(f"""
<style>
    .stApp {{ background-color: #FFFFFF; }}
    .main-header {{
        background-color: {TP_RED};
        padding: 1.2rem 2rem;
        border-radius: 6px;
        margin-bottom: 1.5rem;
    }}
    .main-header h1 {{ color: white; margin: 0; font-size: 1.6rem; }}
    .main-header p  {{ color: #FFD0D0; margin: 0.2rem 0 0; font-size: 0.9rem; }}
    .metric-ok    {{ color: #16a34a; font-weight: 700; }}
    .metric-error {{ color: {TP_RED}; font-weight: 700; }}
    div[data-testid="stExpander"] {{ border-left: 3px solid {TP_RED}; }}
</style>
""", unsafe_allow_html=True)

st.markdown(f"""
<div class="main-header">
    <h1>Billing Validation Agent System</h1>
    <p>TP Group · AI Transformation · Billing Validation Prototype</p>
</div>
""", unsafe_allow_html=True)


def load_df(uploaded, fallback_path: str) -> pd.DataFrame:
    if uploaded is not None:
        suffix = Path(uploaded.name).suffix.lower()
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(uploaded)
        return pd.read_csv(uploaded)
    path = Path(fallback_path)
    if path.exists():
        if path.suffix.lower() in {".xlsx", ".xls"}:
            return pd.read_excel(path)
        return pd.read_csv(path)
    return pd.DataFrame()


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/5/5a/Teleperformance_logo.svg/320px-Teleperformance_logo.svg.png", width=180)
    st.markdown("### Configuration")

    import json
    rules_path = Path("config/client_rules.json")
    client_options = ["teleperformance"]
    if rules_path.exists():
        client_options = list(json.loads(rules_path.read_text()).keys())
    selected_client = st.selectbox("Client rules", client_options)

    provider_options = ["anthropic", "openai"]
    configured_provider = os.getenv("AI_PROVIDER", "anthropic").lower()
    provider_index = (
        provider_options.index(configured_provider)
        if configured_provider in provider_options
        else 0
    )
    selected_provider = st.selectbox("AI provider", provider_options, index=provider_index)
    provider_key_env = (
        "OPENAI_API_KEY" if selected_provider == "openai" else "ANTHROPIC_API_KEY"
    )
    use_ai = st.toggle("Use AI explanations", value=bool(os.getenv(provider_key_env)))
    if use_ai and not os.getenv(provider_key_env):
        st.warning(f"{provider_key_env} not set — deterministic fallback will be used.")

    st.markdown("---")
    st.markdown("**Governance**")
    ks_path = Path("governance/kill_switch.json")
    if ks_path.exists():
        ks = json.loads(ks_path.read_text())
        active = ks.get("active", True)
        st.markdown(f"Kill switch: {'🟢 Active' if active else '🔴 Halted'}")

# ── File Upload ───────────────────────────────────────────────────────────────
st.markdown("### Upload Data Files")
col1, col2, col3 = st.columns(3)
with col1:
    billing_file = st.file_uploader("billing.csv", type=["csv", "xlsx"])
with col2:
    timesheet_file = st.file_uploader("timesheet.csv", type=["csv", "xlsx"])
with col3:
    contracts_file = st.file_uploader("contracts.csv", type=["csv", "xlsx"])

use_sample = st.checkbox("Use sample data (data/input/)", value=True)

# ── Run Validation ────────────────────────────────────────────────────────────
if st.button("▶  Run Validation", type="primary", use_container_width=True):
    if use_sample:
        billing_df   = load_df(billing_file,   "data/input/billing.csv")
        timesheet_df = load_df(timesheet_file, "data/input/timesheet.csv")
        contracts_df = load_df(contracts_file, "data/input/contracts.csv")
    else:
        if not billing_file or not timesheet_file or not contracts_file:
            st.error("Please upload all three CSV files or enable 'Use sample data'.")
            st.stop()
        billing_df = load_df(billing_file, "data/input/billing.csv")
        timesheet_df = load_df(timesheet_file, "data/input/timesheet.csv")
        contracts_df = load_df(contracts_file, "data/input/contracts.csv")

    with st.spinner("Running validation pipeline…"):
        from agents.validation_agent import ValidationAgent
        from agents.ai_explanation_agent import AIExplanationAgent

        os.environ["AI_PROVIDER"] = selected_provider
        report_df = ValidationAgent().run(
            timesheet_df, contracts_df, billing_df, client=selected_client
        )
        report_df = AIExplanationAgent().run(report_df, use_ai=use_ai)

    st.session_state["report"] = report_df
    st.success("Validation complete.")

# ── Results ───────────────────────────────────────────────────────────────────
if "report" in st.session_state:
    df = st.session_state["report"]
    total  = len(df)
    errors = int((df["Status"] == "ERROR").sum())
    ok     = total - errors

    st.markdown("### Results")
    m1, m2, m3 = st.columns(3)
    m1.metric("Total Records", total)
    m2.metric("Passed ✓", ok)
    m3.metric("Failed ✗", errors, delta=f"-{errors}" if errors else None, delta_color="inverse")

    def _style_row(row):
        color = "#FEE2E2" if row["Status"] == "ERROR" else "#F0FDF4"
        return [f"background-color: {color}"] * len(row)

    display_cols = ["Employee_ID", "Employee_Name", "Project", "Hours_Worked",
                    "Hours_Billed", "Rate_Charged", "Contract_Rate",
                    "Status", "Flags", "Difference"]
    styled = df[display_cols].style.apply(_style_row, axis=1)
    st.dataframe(styled, use_container_width=True, hide_index=True)

    error_rows = df[df["Status"] == "ERROR"]
    if not error_rows.empty:
        st.markdown("### AI Explanations")
        for _, row in error_rows.iterrows():
            with st.expander(f"🔴 {row['Employee_Name']} (ID {row['Employee_ID']}) — {row['Flags']}"):
                st.markdown(row["AI_Explanation"] or "_No explanation available._")

    csv_bytes = df.to_csv(index=False).encode()
    st.download_button(
        label="⬇  Download validation_report.csv",
        data=csv_bytes,
        file_name="validation_report.csv",
        mime="text/csv",
        use_container_width=True,
    )
