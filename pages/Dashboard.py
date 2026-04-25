"""
pages/Dashboard.py — Trip Dashboard for bunq Travel Buddy.

The main trip overview: budget tracking, currencies, and bunq payment history.
All data is scoped to the active trip.
"""

from __future__ import annotations

import datetime

import requests as _req
import streamlit as st

from trips import get_trip, load_trips, update_trip

st.set_page_config(
    page_title="Dashboard · bunq Travel Buddy",
    page_icon="🏖️",
    layout="centered",
)

st.markdown("""
<style>
  .block-container { max-width: 430px; padding-top: 4rem; }

  .pay-row {
    display: flex; justify-content: space-between; align-items: flex-start;
    padding: 10px 0; border-bottom: 1px solid #F2F2F7;
  }
  .pay-row:last-child { border-bottom: none; }
  .pay-meta  { font-size: 0.72rem; color: #8E8E93; }
  .pay-desc  { font-size: 0.9rem;  font-weight: 500; color: #1C1C1E; margin-top: 1px; }
  .pay-debit  { font-size: 0.95rem; font-weight: 700; color: #D32F2F; white-space: nowrap; }
  .pay-credit { font-size: 0.95rem; font-weight: 700; color: #1C7C3A; white-space: nowrap; }

  div.stButton > button { border-radius: 50px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "active_trip_id" not in st.session_state:
    st.session_state["active_trip_id"] = None

# ── Trip selector ──────────────────────────────────────────────────────────────
trips = load_trips()
if not trips:
    st.markdown("## 🏖️ Trip Dashboard")
    st.warning("No trips yet — go to **My Trips** to create one.")
    st.stop()

# Auto-select when only one trip exists
if st.session_state["active_trip_id"] is None and len(trips) == 1:
    st.session_state["active_trip_id"] = trips[0].id

trip_ids   = [t.id for t in trips]
trip_names = {t.id: t.name for t in trips}

current_idx = 0
if st.session_state["active_trip_id"] in trip_ids:
    current_idx = trip_ids.index(st.session_state["active_trip_id"])

title_col, select_col = st.columns([2, 3])
title_col.markdown("## 🏖️ Dashboard")
with select_col:
    selected_id = st.selectbox(
        "Trip",
        options=trip_ids,
        format_func=lambda tid: trip_names[tid],
        index=current_idx,
        label_visibility="collapsed",
    )

if selected_id != st.session_state.get("active_trip_id"):
    st.session_state["active_trip_id"] = selected_id
    st.rerun()

trip = get_trip(selected_id)

# Date note under the selector
if trip and (trip.start_date or trip.end_date):
    s = trip.start_date or "?"
    e = trip.end_date   or "?"
    st.caption(f"📅 {s} → {e}")
if not trip:
    st.error("Trip not found.")
    st.stop()

own_ccy  = trip.default_own_currency or "EUR"
dest_ccy = trip.default_currency  # may be None


# ── Cached loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_payments(since: str | None, until: str | None) -> tuple[list[dict], str]:
    try:
        from bunq_balance import get_payments
        return get_payments(since_date=since, until_date=until), ""
    except Exception as exc:
        return [], str(exc)


@st.cache_data(ttl=300, show_spinner=False)
def _fx_rate(from_ccy: str, to_ccy: str) -> float | None:
    try:
        r = _req.get(
            f"https://api.frankfurter.app/latest?from={from_ccy}&to={to_ccy}",
            timeout=5,
        )
        return float(r.json()["rates"][to_ccy])
    except Exception:
        return None


# ── Load payments for this trip ────────────────────────────────────────────────
if trip.start_date:
    since_date = trip.start_date
elif trip.created_at:
    since_date = trip.created_at[:10]
else:
    since_date = None

until_date = trip.end_date

payments, pay_err = _fetch_payments(since_date, until_date)

# Total spent: sum of outgoing payments in the own currency
spent = sum(
    abs(p["amount"])
    for p in payments
    if p["type"] == "debit" and p["currency"] == own_ccy
)
budget_left = trip.budget_eur - spent


# ── Currency toggle ────────────────────────────────────────────────────────────
in_dest = False
conv_rate: float | None = None

if dest_ccy and dest_ccy != own_ccy:
    display_ccy = st.segmented_control(
        "display_ccy",
        options=[own_ccy, dest_ccy],
        default=own_ccy,
        key="dash_toggle",
        label_visibility="collapsed",
    )
    in_dest = display_ccy == dest_ccy
    if in_dest:
        conv_rate = _fx_rate(own_ccy, dest_ccy)
else:
    display_ccy = own_ccy


def _fmt(amount: float | None) -> str:
    """Format amount (denominated in own_ccy), converting to dest if toggle active."""
    if amount is None:
        return "—"
    if in_dest and conv_rate:
        return f"{dest_ccy} {amount * conv_rate:,.2f}"
    return f"{own_ccy} {amount:,.2f}"


# ── Budget section ─────────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("**💰 Budget**")

    today = datetime.date.today()
    budget_per_day: float | None = None
    budget_left_per_day: float | None = None

    if trip.start_date and trip.end_date:
        start_dt = datetime.date.fromisoformat(trip.start_date)
        end_dt   = datetime.date.fromisoformat(trip.end_date)
        total_days     = max(1, (end_dt - start_dt).days)
        days_remaining = max(1, (end_dt - today).days) if end_dt >= today else 1
        budget_per_day      = trip.budget_eur / total_days
        budget_left_per_day = budget_left / days_remaining

    col1, col2 = st.columns(2)
    col1.metric("Initial budget", _fmt(trip.budget_eur))
    col2.metric("Remaining", _fmt(budget_left))

    if budget_per_day is not None:
        col3, col4 = st.columns(2)
        col3.metric("Per day", _fmt(budget_per_day))
        col4.metric("Per day left", _fmt(budget_left_per_day))


# ── Currencies section ─────────────────────────────────────────────────────────
with st.container(border=True):
    st.markdown("**💱 Currencies**")

    cc1, cc2 = st.columns(2)
    with cc1:
        new_dest = st.text_input(
            "Destination currency",
            value=dest_ccy or "",
            max_chars=3,
            placeholder="e.g. JPY",
            key="dash_dest",
        ).upper().strip()
    with cc2:
        new_own = st.text_input(
            "Home currency",
            value=own_ccy,
            max_chars=3,
            placeholder="EUR",
            key="dash_own",
        ).upper().strip()

    btn1, btn2 = st.columns(2)
    with btn1:
        if st.button("💾 Save", key="dash_save_ccy", use_container_width=True):
            update_trip(
                selected_id,
                default_currency=new_dest or None,
                default_own_currency=new_own or None,
            )
            for k in ("dash_dest", "dash_own", "dash_toggle"):
                st.session_state.pop(k, None)
            st.rerun()
    with btn2:
        try:
            from geo import get_location as _get_loc
            _geo = _get_loc()
            loc_label = f"📍 {_geo.currency_iso}" if _geo else "📍 From location"
        except Exception:
            _geo = None
            loc_label = "📍 From location"

        if st.button(loc_label, key="dash_loc", use_container_width=True):
            if _geo:
                update_trip(selected_id, default_currency=_geo.currency_iso)
                st.session_state.pop("dash_dest", None)
                st.session_state.pop("dash_toggle", None)
                st.rerun()
            else:
                st.warning("Location unavailable.")


# ── Payments section ───────────────────────────────────────────────────────────
with st.container(border=True):
    pay_hdr, pay_btn = st.columns([4, 1])
    with pay_hdr:
        spent_label = _fmt(spent)
        st.markdown(f"**🧾 Trip Payments** &nbsp; spent: {spent_label}", unsafe_allow_html=True)
    with pay_btn:
        if st.button("↺", key="refresh_pay", use_container_width=True, help="Refresh payments"):
            _fetch_payments.clear()
            st.rerun()

    if pay_err:
        st.warning(f"Could not load bunq payments: `{pay_err}`")
    elif not payments:
        st.caption("No payments found for this trip period.")
    else:
        rows_html = ""
        for p in payments[:100]:
            amount    = abs(float(p["amount"]))
            is_debit  = p["type"] == "debit"
            sign      = "−" if is_debit else "+"
            amt_class = "pay-debit" if is_debit else "pay-credit"

            if in_dest and conv_rate:
                amt_str = f"{sign}{dest_ccy} {amount * conv_rate:,.2f}"
            else:
                amt_str = f"{sign}{own_ccy} {amount:,.2f}"

            desc = p["description"] or p["counterparty"] or "—"
            # Truncate long descriptions
            if len(desc) > 35:
                desc = desc[:33] + "…"

            date_str = p["date"]
            try:
                d = datetime.date.fromisoformat(date_str)
                date_fmt = d.strftime("%b %d")
            except (ValueError, TypeError):
                date_fmt = date_str

            rows_html += f"""
            <div class="pay-row">
              <div>
                <div class="pay-meta">{date_fmt}</div>
                <div class="pay-desc">{desc}</div>
              </div>
              <div class="{amt_class}">{amt_str}</div>
            </div>"""

        st.markdown(rows_html, unsafe_allow_html=True)

        if len(payments) > 100:
            st.caption(f"Showing 100 of {len(payments)} payments.")
