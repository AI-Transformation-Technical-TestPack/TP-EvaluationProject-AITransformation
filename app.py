"""Streamlit web interface for the Billing Validation Agent System.

Uses Streamlit's native components and the project theme defined in
`.streamlit/config.toml`. No custom CSS or HTML overrides — the framework
handles layout, typography, color, and responsive behavior so the visual
language stays consistent across sections.
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Billing Validation Agent",
    page_icon=":material/receipt_long:",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ── Helpers ───────────────────────────────────────────────────────────────────
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


def parse_payload(raw: str) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def clean_prose(text) -> str:
    """Normalize AI-generated text for safe display.

    Collapses any run of whitespace (including stray newlines, tabs, and
    zero-width characters) into single spaces, and strips leading/trailing
    whitespace. Prevents broken layouts when AI output contains odd spacing.
    """
    if text is None:
        return ""
    s = str(text)
    s = s.replace("​", "").replace("﻿", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace("\\", "\\\\").replace("$", r"\$")
    return s


def safe_float(x):
    try:
        if x is None or (isinstance(x, float) and pd.isna(x)):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


RISK_META = {
    "high":   {"dot": "🔴", "label": "High risk"},
    "medium": {"dot": "🟠", "label": "Medium risk"},
    "low":    {"dot": "🔵", "label": "Low risk"},
}
MODE_LABEL = {
    "anthropic": "Anthropic Claude",
    "openai": "OpenAI / compatible",
    "deterministic": "Rule-based fallback",
}

FLAG_GLOSSARY = {
    "RATE_MISMATCH":      "Charged hourly rate differs from the contracted rate.",
    "OVERBILLING":        "Hours billed exceed hours actually worked.",
    "UNDERBILLING":       "Hours billed are below hours worked — revenue leakage.",
    "CONTRACT_VIOLATION": "Hours worked exceed the contracted weekly cap.",
    "BILLING_OVER_MAX":   "Hours billed exceed the contracted weekly cap.",
    "GHOST_BILLING":      "Billing record has no matching timesheet entry.",
    "MISSING_BILLING":    "Timesheet entry has no matching billing record.",
    "MISSING_CONTRACT":   "Project has no matching contract — rate/cap unknown.",
    "DUPLICATE_RECORD":   "Two or more billing rows share the same employee + project.",
}

AUDIT_PATH = Path("data/output/audit.log")


def write_audit(agent: str, event: str, detail: str) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    with open(AUDIT_PATH, "a") as f:
        f.write(f"{ts} | {agent:<24} | {event:<8} | {detail}\n")


def read_audit_tail(n: int = 50) -> list[str]:
    if not AUDIT_PATH.exists():
        return []
    lines = AUDIT_PATH.read_text().splitlines()
    return lines[-n:]


def read_audit_df(n: int = 50) -> pd.DataFrame:
    """Parse the audit log tail into a structured DataFrame."""
    rows = []
    for line in read_audit_tail(n):
        parts = [p.strip() for p in line.split("|", 3)]
        if len(parts) == 4:
            rows.append({
                "Timestamp": parts[0],
                "Agent":     parts[1],
                "Event":     parts[2],
                "Detail":    parts[3],
            })
    return pd.DataFrame(rows, columns=["Timestamp", "Agent", "Event", "Detail"])


def provider_health_check(provider: str) -> tuple[bool, str]:
    """Return (ok, message) after a minimal probe of the configured provider."""
    try:
        if provider == "anthropic":
            key = os.getenv("ANTHROPIC_API_KEY")
            if not key:
                return False, "ANTHROPIC_API_KEY not set."
            import anthropic
            client = anthropic.Anthropic(api_key=key)
            client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=1,
                messages=[{"role": "user", "content": "ok"}],
            )
            return True, "Anthropic reachable."
        else:
            key = os.getenv("OPENAI_API_KEY")
            if not key:
                return False, "OPENAI_API_KEY not set."
            import openai
            base_url = os.getenv("OPENAI_BASE_URL") or None
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            client = openai.OpenAI(api_key=key, base_url=base_url)
            client.chat.completions.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "ok"}],
            )
            return True, f"OpenAI/compatible reachable (model={model})."
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {str(e)[:160]}"


def df_to_excel_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="validation")
    return buf.getvalue()


# ── Header ────────────────────────────────────────────────────────────────────
st.title("Billing Validation Agent")
st.caption(
    "Detects rate mismatches, overbilling, contract-cap breaches, and "
    "revenue leakage across timesheets, contracts, and invoices."
)
st.divider()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    # ─── 1. Run profile ──────────────────────────────────────────────────
    st.subheader("Run profile", divider="gray")

    rules_path = Path("config/client_rules.json")
    client_options = ["client_a"]
    if rules_path.exists():
        client_options = [
            k for k in json.loads(rules_path.read_text()).keys()
            if not k.startswith("_")
        ]
    selected_client = st.selectbox("Client rules", client_options)

    role_options = ["analyst", "admin"]
    selected_role = st.selectbox(
        "Acting role", role_options,
        help=(
            "RBAC gate. In production this comes from SSO; in this demo it is "
            "user-selected to make the boundary visible."
        ),
    )
    can_toggle = selected_role == "admin"

    # ─── 2. AI provider ──────────────────────────────────────────────────
    st.subheader("AI provider", divider="gray")

    provider_options = ["anthropic", "openai"]
    configured_provider = os.getenv("AI_PROVIDER", "anthropic").lower()
    provider_index = (
        provider_options.index(configured_provider)
        if configured_provider in provider_options else 0
    )
    selected_provider = st.selectbox(
        "Provider", provider_options, index=provider_index
    )

    provider_key_env = (
        "OPENAI_API_KEY" if selected_provider == "openai" else "ANTHROPIC_API_KEY"
    )
    key_present = bool(os.getenv(provider_key_env))
    use_ai = st.toggle("Use AI explanations", value=key_present)
    if use_ai and not key_present:
        st.warning(
            f"{provider_key_env} not set — deterministic fallback will be used.",
            icon=":material/warning:",
        )
    elif key_present:
        st.success(f"{provider_key_env} detected.", icon=":material/check_circle:")

    if st.button(
        "Test connection", icon=":material/network_check:",
        use_container_width=True,
    ):
        with st.spinner("Pinging provider…"):
            ok, msg = provider_health_check(selected_provider)
        if ok:
            st.success(msg, icon=":material/check_circle:")
        else:
            st.error(msg, icon=":material/error:")

    # ─── 3. Governance ───────────────────────────────────────────────────
    st.subheader("Governance", divider="gray")
    ks_path = Path("config/kill_switch.json")
    ks_active = True
    if ks_path.exists():
        try:
            ks_active = bool(json.loads(ks_path.read_text()).get("active", True))
        except (json.JSONDecodeError, OSError):
            ks_active = True

    if ks_active:
        st.success("Kill switch: Active", icon=":material/shield:")
    else:
        st.error("Kill switch: Halted", icon=":material/block:")

    with st.expander("Kill-switch controls", expanded=False):
        if not can_toggle:
            st.caption(
                "Read-only for analysts. Switch to **admin** to toggle. "
                "Production systems gate this on SSO group / role claim."
            )
        else:
            st.caption(
                "Toggling halts or resumes the validation pipeline. "
                "The action is audit-logged with operator, timestamp, and reason."
            )
            new_state_label = "Halt pipeline" if ks_active else "Resume pipeline"
            new_state_value = not ks_active
            operator = st.text_input(
                "Operator ID", value="admin@local",
                help="In production this is filled from the authenticated identity.",
            )
            reason = st.text_area(
                "Reason (required)",
                placeholder="e.g. Suspected upstream data corruption — pause until reviewed.",
            )
            confirm = st.checkbox(
                "I understand this is an audited governance action.",
            )
            if st.button(
                new_state_label,
                type="primary" if new_state_value is False else "secondary",
                icon=":material/block:" if new_state_value is False else ":material/play_arrow:",
                disabled=not (confirm and reason.strip() and operator.strip()),
            ):
                ks_path.write_text(json.dumps({"active": new_state_value}, indent=2))
                event = "RESUME" if new_state_value else "HALT"
                write_audit(
                    "KillSwitch", event,
                    f"operator={operator.strip()} role={selected_role} "
                    f"reason={reason.strip()}",
                )
                st.success(
                    f"Kill switch {'halted' if new_state_value is False else 'resumed'}. "
                    "Audit entry written.",
                    icon=":material/check_circle:",
                )
                st.rerun()

    # ── Client rules editor (admin) ───────────────────────────────────────
    with st.expander("Client rules editor", expanded=False):
        if not can_toggle:
            st.caption("Read-only for analysts. Switch to **admin** to edit.")
            if rules_path.exists():
                st.json(json.loads(rules_path.read_text()), expanded=False)
        else:
            rules_data = {}
            if rules_path.exists():
                rules_data = json.loads(rules_path.read_text())
            edit_target = st.selectbox(
                "Client", list(rules_data.keys()) or ["client_a"],
                key="rules_target",
            )
            current = rules_data.get(edit_target, {}) if isinstance(rules_data, dict) else {}
            tol  = st.number_input("allow_rate_tolerance", min_value=0.0, value=float(current.get("allow_rate_tolerance", 0)), step=0.5)
            ob   = st.number_input("overbilling_threshold", min_value=0.0, value=float(current.get("overbilling_threshold", 0)), step=0.5)
            ub   = st.number_input("underbilling_threshold", min_value=0.0, value=float(current.get("underbilling_threshold", 0)), step=0.5)
            mhe  = st.checkbox("max_hours_enforcement", value=bool(current.get("max_hours_enforcement", True)))
            if st.button("Save rules", icon=":material/save:"):
                rules_data[edit_target] = {
                    "allow_rate_tolerance": tol,
                    "overbilling_threshold": ob,
                    "underbilling_threshold": ub,
                    "max_hours_enforcement": mhe,
                }
                rules_path.write_text(json.dumps(rules_data, indent=2))
                write_audit("RulesEditor", "UPDATE", f"client={edit_target} role={selected_role}")
                st.success("Rules saved.", icon=":material/check_circle:")
                st.rerun()

    # ─── 4. Audit & reference ────────────────────────────────────────────
    st.subheader("Audit & reference", divider="gray")

    with st.expander("Audit log (last 50 events)", expanded=False):
        audit_df = read_audit_df(50)
        if audit_df.empty:
            st.caption("No audit entries yet.")
        else:
            st.dataframe(
                audit_df, use_container_width=True, hide_index=True,
                column_config={
                    "Timestamp": st.column_config.TextColumn(
                        "Timestamp", width="small",
                    ),
                    "Agent":  st.column_config.TextColumn("Agent",  width="small"),
                    "Event":  st.column_config.TextColumn("Event",  width="small"),
                    "Detail": st.column_config.TextColumn("Detail"),
                },
                height=260,
            )

    with st.expander("Validation flag legend", expanded=False):
        st.dataframe(
            pd.DataFrame(
                [{"Flag": k, "Meaning": v} for k, v in FLAG_GLOSSARY.items()]
            ),
            use_container_width=True, hide_index=True,
        )

    with st.expander("RBAC matrix", expanded=False):
        rbac_path = Path("config/rbac.json")
        if rbac_path.exists():
            rbac = json.loads(rbac_path.read_text())
            roles_df = pd.DataFrame([
                {"Role": r, "Description": meta.get("description", ""),
                 "Permissions": ", ".join(meta.get("permissions", []))}
                for r, meta in rbac.get("roles", {}).items()
            ])
            st.dataframe(roles_df, use_container_width=True, hide_index=True)
        else:
            st.caption("config/rbac.json not found.")

    with st.expander("AI prompt template", expanded=False):
        prompt_path = Path("prompts/discrepancy_prompt.txt")
        if prompt_path.exists():
            st.code(prompt_path.read_text(), language="text")
        else:
            st.caption("Prompt file not found.")

    st.caption(
        "AI recommendations are advisory. A billing supervisor should review "
        "before any invoice adjustment, credit, or client correction."
    )

if not ks_active:
    st.error(
        "Kill switch is HALTED. The validation pipeline is paused and will "
        "raise on run. Resume from the sidebar (admin role required).",
        icon=":material/block:",
    )


# ── 1. Data source ────────────────────────────────────────────────────────────
st.subheader("1 · Choose data source")

tab_sample, tab_upload = st.tabs(
    ["Use bundled sample data", "Upload your own files"]
)

with tab_sample:
    st.info(
        "Runs the validation against the three CSVs in `data/input/` — "
        "5 employees across 3 projects.",
        icon=":material/dataset:",
    )

with tab_upload:
    col1, col2, col3 = st.columns(3)
    with col1:
        st.file_uploader("Billing", type=["csv", "xlsx"], key="bill")
    with col2:
        st.file_uploader("Timesheet", type=["csv", "xlsx"], key="ts")
    with col3:
        st.file_uploader("Contracts", type=["csv", "xlsx"], key="con")

billing_upload = st.session_state.get("bill")
timesheet_upload = st.session_state.get("ts")
contracts_upload = st.session_state.get("con")
has_uploads = bool(billing_upload or timesheet_upload or contracts_upload)
use_sample = not has_uploads

st.divider()


# ── 2. Run ────────────────────────────────────────────────────────────────────
st.subheader("2 · Run validation")

if st.button(
    "Run pipeline", type="primary", icon=":material/play_arrow:",
    use_container_width=False,
):
    if not use_sample and not (
        billing_upload and timesheet_upload and contracts_upload
    ):
        st.error(
            "Please upload all three files, or switch to the bundled "
            "sample data tab.",
            icon=":material/error:",
        )
        st.stop()
    with st.status("Running validation pipeline…", expanded=True) as status:
        from agents.validation_agent import ValidationAgent
        from agents.ai_explanation_agent import AIExplanationAgent

        st.write("📥 Ingesting source files…")
        billing_df = load_df(billing_upload, "data/input/billing.csv")
        timesheet_df = load_df(timesheet_upload, "data/input/timesheet.csv")
        contracts_df = load_df(contracts_upload, "data/input/contracts.csv")
        st.write(f"   ✓ {len(billing_df)} billing · {len(timesheet_df)} timesheet · {len(contracts_df)} contracts")

        st.write(f"🔍 Validating against client rules ({selected_client})…")
        os.environ["AI_PROVIDER"] = selected_provider
        report_df = ValidationAgent().run(
            timesheet_df, contracts_df, billing_df, client=selected_client
        )
        flagged = int((report_df["Status"] == "ERROR").sum())
        st.write(f"   ✓ {len(report_df)} records evaluated · {flagged} flagged")

        st.write(
            f"🧠 Generating explanations via {MODE_LABEL.get(selected_provider, selected_provider)}…"
            if use_ai else "🧠 Generating deterministic explanations…"
        )
        report_df = AIExplanationAgent().run(report_df, use_ai=use_ai)
        st.write("   ✓ Explanations attached to ERROR rows")

        status.update(label="Validation complete.", state="complete", expanded=False)
    st.session_state["report"] = report_df
    st.session_state.setdefault("review_status", {})
    write_audit("UI", "RUN", f"client={selected_client} provider={selected_provider} use_ai={use_ai}")
    st.success("Validation complete.", icon=":material/check_circle:")

st.divider()


# ── 3. Results ────────────────────────────────────────────────────────────────
st.subheader("3 · Results")

if "report" not in st.session_state:
    st.info(
        "Pick a data source above and click **Run pipeline** to see flagged "
        "records and AI explanations.",
        icon=":material/info:",
    )
else:
    df = st.session_state["report"]
    total = len(df)
    errors = int((df["Status"] == "ERROR").sum())
    ok = total - errors
    error_rows = df[df["Status"] == "ERROR"]

    # ── Summary KPIs ──────────────────────────────────────────────────────
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total records", total)
    m2.metric("Passed", ok)
    m3.metric(
        "Flagged", errors,
        delta=f"-{errors}" if errors else None, delta_color="inverse",
    )
    m4.metric("Pass rate", f"{(ok / total * 100) if total else 0:.0f}%")

    diff_series = pd.to_numeric(df["Difference"], errors="coerce").fillna(0)
    overbilled = float(diff_series[diff_series > 0].sum())
    underbilled = float(-diff_series[diff_series < 0].sum())
    net_exposure = float(diff_series.sum())

    f1, f2, f3 = st.columns(3)
    f1.metric("Overbilled (gross)", f"${overbilled:,.2f}")
    f2.metric("Underbilled (gross)", f"${underbilled:,.2f}")
    f3.metric(
        "Net financial exposure", f"${net_exposure:+,.2f}",
        help="Positive = client overcharged; negative = revenue leakage.",
    )

    st.divider()

    # ── Findings (always visible) ─────────────────────────────────────────
    if error_rows.empty:
        st.success(
            "No findings — all records passed validation.",
            icon=":material/check_circle:",
        )
    else:
        st.markdown(f"##### {len(error_rows)} finding(s)")

        # Filters
        all_flags = sorted({
            f.strip() for line in error_rows["Flags"].fillna("")
            for f in line.split(",") if f.strip()
        })
        all_projects = sorted(error_rows["Project"].dropna().unique().tolist())
        fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 2, 1])
        with fcol1:
            sel_flags = st.multiselect("Filter by flag", all_flags, default=[])
        with fcol2:
            sel_risks = st.multiselect(
                "Filter by risk", ["high", "medium", "low"], default=[]
            )
        with fcol3:
            sel_projects = st.multiselect("Filter by project", all_projects, default=[])
        with fcol4:
            sel_status = st.multiselect(
                "Review state",
                ["pending", "approved", "rejected", "deferred"],
                default=[],
            )

        review_status = st.session_state.setdefault("review_status", {})

        def matches(row, payload) -> bool:
            risk = (payload.get("risk_score") or "low").lower()
            row_flags = {f.strip() for f in (row["Flags"] or "").split(",") if f.strip()}
            state = review_status.get(int(row["Employee_ID"]), "pending")
            if sel_flags and not row_flags.intersection(sel_flags):
                return False
            if sel_risks and risk not in sel_risks:
                return False
            if sel_projects and row["Project"] not in sel_projects:
                return False
            if sel_status and state not in sel_status:
                return False
            return True

        shown = 0
        for _, row in error_rows.iterrows():
            payload = parse_payload(row["AI_Explanation"])
            if not matches(row, payload):
                continue
            shown += 1
            risk = (payload.get("risk_score") or "low").lower()
            risk_meta = RISK_META.get(risk, RISK_META["low"])
            explanation = clean_prose(payload.get("explanation")) or "(no explanation)"
            action = clean_prose(payload.get("corrective_action"))
            fin = payload.get("financial_deviation") or {}
            direction = fin.get("direction", "")
            cap_exposure = safe_float(fin.get("over_cap_exposure"))
            mode = (payload.get("metadata") or {}).get("generation_mode", "?")
            mode_label = MODE_LABEL.get(mode, mode)
            diff = safe_float(row["Difference"]) or 0.0
            confidence = safe_float(payload.get("confidence"))
            hr = payload.get("human_review") or {}
            hr_required = hr.get("required", True)
            hr_approver = hr.get("approver_role", "supervisor")
            schema_v = payload.get("schema_version", "—")
            emp_id = int(row["Employee_ID"])
            current_state = review_status.get(emp_id, "pending")

            with st.container(border=True):
                head_l, head_r = st.columns([3, 1])
                with head_l:
                    st.markdown(
                        f"**{row['Employee_Name']}** "
                        f"· ID {emp_id} "
                        f"· Project {row['Project']}"
                    )
                with head_r:
                    state_dot = {
                        "pending":   "⚪",
                        "approved":  "🟢",
                        "rejected":  "🔴",
                        "deferred":  "🟡",
                    }[current_state]
                    st.markdown(
                        f"<div style='text-align:right'>"
                        f"{risk_meta['dot']} **{risk_meta['label']}**  ·  "
                        f"{state_dot} {current_state.title()}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                st.code(row["Flags"] or "—", language=None)

                c1, c2, c3, c4 = st.columns(4)
                c1.metric(
                    "Loose Δ",
                    f"${diff:+,.2f}",
                    help=f"Direction: {direction}" if direction else None,
                )
                if cap_exposure is not None:
                    c2.metric("Strict-cap exposure", f"${cap_exposure:+,.2f}")
                c3.metric(
                    "Confidence",
                    f"{confidence:.0%}" if confidence is not None else "—",
                )
                c4.metric("Source", mode_label)

                st.markdown("**What happened**")
                st.markdown(explanation)
                if action:
                    st.markdown("**Recommended action**")
                    st.markdown(action)

                st.caption(
                    f"Schema v{schema_v} · Human review: "
                    f"{'required' if hr_required else 'optional'} "
                    f"({hr_approver})"
                )

                # Approval workflow (admin only)
                if can_toggle:
                    a1, a2, a3, a4 = st.columns(4)
                    if a1.button("Approve", key=f"ap_{emp_id}", icon=":material/check:"):
                        review_status[emp_id] = "approved"
                        write_audit("Review", "APPROVE", f"emp={emp_id} role={selected_role}")
                        st.rerun()
                    if a2.button("Reject", key=f"rj_{emp_id}", icon=":material/close:"):
                        review_status[emp_id] = "rejected"
                        write_audit("Review", "REJECT", f"emp={emp_id} role={selected_role}")
                        st.rerun()
                    if a3.button("Defer", key=f"df_{emp_id}", icon=":material/schedule:"):
                        review_status[emp_id] = "deferred"
                        write_audit("Review", "DEFER", f"emp={emp_id} role={selected_role}")
                        st.rerun()
                    if current_state != "pending" and a4.button(
                        "Reset", key=f"rs_{emp_id}", icon=":material/undo:",
                    ):
                        review_status.pop(emp_id, None)
                        write_audit("Review", "RESET", f"emp={emp_id} role={selected_role}")
                        st.rerun()
                else:
                    st.caption("Approval actions require admin role.")

                with st.expander("View full JSON contract", expanded=False):
                    st.json(payload, expanded=False)

        if shown == 0:
            st.info(
                "No findings match the current filters.",
                icon=":material/filter_alt:",
            )

    # ── Validated records ─────────────────────────────────────────────────
    st.divider()
    st.markdown("##### Validated records")
    display_cols = [
        "Employee_ID", "Employee_Name", "Project",
        "Status", "Flags", "Difference",
    ]
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Employee_ID": st.column_config.NumberColumn("ID", width="small"),
            "Employee_Name": st.column_config.TextColumn("Name"),
            "Project": st.column_config.TextColumn("Project", width="small"),
            "Status": st.column_config.TextColumn("Status", width="small"),
            "Flags": st.column_config.TextColumn("Flags"),
            "Difference": st.column_config.NumberColumn(
                "Diff", format="$%+,.2f", width="small",
            ),
        },
    )

    st.divider()
    st.markdown("##### Export")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"billing-validation_{selected_client}_{timestamp}"
    e1, e2, e3 = st.columns(3)
    with e1:
        st.download_button(
            "CSV", data=df.to_csv(index=False).encode(),
            file_name=f"{base}.csv", mime="text/csv",
            icon=":material/download:", use_container_width=True,
        )
    with e2:
        st.download_button(
            "JSON", data=df.to_json(orient="records", indent=2).encode(),
            file_name=f"{base}.json", mime="application/json",
            icon=":material/download:", use_container_width=True,
        )
    with e3:
        st.download_button(
            "Excel", data=df_to_excel_bytes(df),
            file_name=f"{base}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:", use_container_width=True,
        )
    st.caption(
        "The `AI_Explanation` column carries the full schema-versioned "
        "JSON contract for each ERROR row."
    )

    # ── Diff against previous run ─────────────────────────────────────────
    st.divider()
    st.markdown("##### Compare to a previous run")
    prev_csv = st.file_uploader(
        "Upload a previously exported validation CSV to diff",
        type=["csv"], key="diff_upload",
    )
    if prev_csv is not None:
        try:
            prev_df = pd.read_csv(prev_csv)
            key_cols = ["Employee_ID", "Project"]
            cur = df.set_index(key_cols)[["Status", "Flags", "Difference"]]
            old = prev_df.set_index(key_cols)[["Status", "Flags", "Difference"]]
            joined = old.join(cur, lsuffix="_prev", rsuffix="_cur", how="outer")
            joined["Δ Difference"] = (
                pd.to_numeric(joined["Difference_cur"], errors="coerce").fillna(0)
                - pd.to_numeric(joined["Difference_prev"], errors="coerce").fillna(0)
            )
            joined["Change"] = joined.apply(
                lambda r: "added" if pd.isna(r["Status_prev"])
                else "removed" if pd.isna(r["Status_cur"])
                else "flags changed" if r.get("Flags_prev") != r.get("Flags_cur")
                else "amount changed" if r["Δ Difference"] != 0
                else "unchanged",
                axis=1,
            )
            changed = joined[joined["Change"] != "unchanged"]
            d1, d2, d3 = st.columns(3)
            d1.metric("Added",   int((changed["Change"] == "added").sum()))
            d2.metric("Removed", int((changed["Change"] == "removed").sum()))
            d3.metric("Modified", int(changed["Change"].isin(
                ["flags changed", "amount changed"]).sum()))
            st.dataframe(
                changed.reset_index(),
                use_container_width=True, hide_index=True,
            )
        except Exception as e:  # noqa: BLE001
            st.error(f"Could not diff: {e}", icon=":material/error:")
