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
import payment_tags as _pt

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

# Tagged payments (SQLite) are the source of truth for spent calculation.
tagged_payments = _pt.get_tagged_payments(trip.id)

# Date-range payments from bunq serve as tagging candidates only.
_all_tagged_ids: set[str] = set()
candidate_payments: list[dict] = []
pay_err = ""
if since_date or until_date:
    _raw, pay_err = _fetch_payments(since_date, until_date)
    _all_tagged_ids = _pt.get_all_tagged_ids()
    candidate_payments = [p for p in _raw if str(p["id"]) not in _all_tagged_ids]

# Spent = explicitly tagged debits in the home currency.
spent = sum(
    abs(p["amount"])
    for p in tagged_payments
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

def _pay_row(p: dict, *, btn_key: str, btn_label: str, btn_help: str) -> bool:
    """Render one payment row with an action button. Returns True if clicked."""
    amount    = abs(float(p["amount"]))
    is_debit  = p["type"] == "debit"
    sign      = "−" if is_debit else "+"
    color     = "#D32F2F" if is_debit else "#1C7C3A"

    if in_dest and conv_rate:
        amt_str = f"{sign}{dest_ccy} {amount * conv_rate:,.2f}"
    else:
        amt_str = f"{sign}{own_ccy} {amount:,.2f}"

    desc = (p["description"] or p["counterparty"] or "—")[:33]
    sub  = (p["counterparty"] or "")[:28] if p["description"] else ""

    date_str = p.get("date", "")
    try:
        date_fmt = datetime.date.fromisoformat(date_str).strftime("%b %d")
    except (ValueError, TypeError):
        date_fmt = date_str or "—"

    c_date, c_desc, c_amt, c_btn = st.columns([1.2, 3.5, 2, 0.8])
    c_date.markdown(f'<div class="pay-meta" style="padding-top:6px">{date_fmt}</div>', unsafe_allow_html=True)
    c_desc.markdown(
        f'<div class="pay-desc">{desc}</div>'
        + (f'<div class="pay-meta">{sub}</div>' if sub else ""),
        unsafe_allow_html=True,
    )
    c_amt.markdown(
        f'<div style="color:{color};font-weight:700;font-size:0.95rem;padding-top:4px">{amt_str}</div>',
        unsafe_allow_html=True,
    )
    clicked = c_btn.button(btn_label, key=btn_key, use_container_width=True, help=btn_help)
    st.markdown('<div style="border-bottom:1px solid #F2F2F7;margin:2px 0 4px"></div>', unsafe_allow_html=True)
    return clicked


with st.container(border=True):
    pay_hdr, pay_btn = st.columns([4, 1])
    with pay_hdr:
        st.markdown(f"**🧾 Trip Payments** &nbsp; spent: {_fmt(spent)}", unsafe_allow_html=True)
    with pay_btn:
        if st.button("↺", key="refresh_pay", use_container_width=True, help="Refresh from bunq"):
            _fetch_payments.clear()
            st.rerun()

    # ── Tagged payments (source of truth) ──────────────────────────────────
    if tagged_payments:
        st.caption(f"🏷 {len(tagged_payments)} tagged expense{'s' if len(tagged_payments) != 1 else ''}")
        for p in tagged_payments:
            if _pay_row(p, btn_key=f"untag_{p['id']}", btn_label="✕", btn_help="Remove from trip"):
                _pt.untag_payment(p["id"])
                st.rerun()
    else:
        st.caption("No expenses tagged yet — add some from bunq below.")

    # ── Candidate payments from bunq ────────────────────────────────────────
    if since_date or until_date:
        st.divider()
        if pay_err:
            st.warning(f"Could not load bunq payments: `{pay_err}`")
        elif not candidate_payments:
            st.caption("No untagged bunq payments in the trip date range.")
        else:
            st.caption(f"📥 {len(candidate_payments)} bunq payment{'s' if len(candidate_payments) != 1 else ''} in date range — tap + to add")
            for p in candidate_payments[:100]:
                if _pay_row(p, btn_key=f"tag_{p['id']}", btn_label="+", btn_help="Add to trip"):
                    _pt.tag_payment(p, trip.id)
                    try:
                        from bunq_balance import push_note_text
                        if push_note_text(p["id"], f"trip:{trip.name}"):
                            _pt.mark_note_pushed(p["id"])
                    except Exception:
                        pass
                    st.rerun()
            if len(candidate_payments) > 100:
                st.caption(f"Showing 100 of {len(candidate_payments)} payments.")
    else:
        st.divider()
        st.caption("Set trip dates to see bunq payment candidates.")


# ── Nearby Recommendations (Claude agent) ──────────────────────────────────────

for _sk in ("nearby_recs", "nearby_geo", "nearby_ts"):
    if _sk not in st.session_state:
        st.session_state[_sk] = None

with st.container(border=True):
    _nh_col, _nb_col = st.columns([5, 1])
    with _nh_col:
        st.markdown("**📍 Nearby Recommendations**")
    with _nb_col:
        _find = st.button("🔍", key="find_nearby", use_container_width=True,
                          help="Find nearby places powered by Claude + Google Maps")

    _cached_recs = st.session_state["nearby_recs"]
    _cached_geo  = st.session_state["nearby_geo"]
    _cached_ts   = st.session_state["nearby_ts"]

    if _find:
        with st.spinner("Locating you · planning search · asking Claude…"):
            try:
                import os
                from geo1 import (
                    get_geo_context as _get_geo1,
                    get_time_context as _get_tc,
                    get_nearby_recommendations as _get_recs,
                    BudgetInfo,
                )
                from api_keys import get_google_maps_api_key as _gmk
                os.environ.setdefault("GOOGLE_MAPS_API_KEY", _gmk())

                _geo1 = _get_geo1()
                if _geo1 is None or _geo1.latitude is None:
                    st.warning("Could not detect your location. Check VPN or network.")
                else:
                    _tc = _get_tc(_geo1)

                    _bi = BudgetInfo(
                        total_budget_eur=trip.budget_eur,
                        spent_eur=spent,
                        remaining_eur=budget_left,
                        home_currency=own_ccy,
                        days_total=total_days if (trip.start_date and trip.end_date) else None,
                        days_remaining=days_remaining if (trip.start_date and trip.end_date) else None,
                        budget_per_day_eur=budget_per_day,
                        budget_left_per_day_eur=budget_left_per_day,
                    )

                    _recs = _get_recs(geo=_geo1, time_context=_tc, budget_info=_bi, limit=5)
                    st.session_state["nearby_recs"] = _recs
                    st.session_state["nearby_geo"]  = _geo1
                    st.session_state["nearby_ts"]   = datetime.datetime.now()
                    _cached_recs = _recs
                    _cached_geo  = _geo1
                    _cached_ts   = st.session_state["nearby_ts"]
            except Exception as _exc:
                st.warning(f"Recommendations unavailable: `{_exc}`")

    if _cached_ts and _cached_geo:
        _loc_str = ", ".join(filter(None, [_cached_geo.city, _cached_geo.country_name]))
        st.caption(f"📍 {_loc_str} · {_cached_ts.strftime('%H:%M')}")

    if _cached_recs:
        for _rec in _cached_recs:
            _rc1, _rc2 = st.columns([5, 1])
            with _rc1:
                _meta_parts = []
                if _rec.distance_m is not None:
                    _meta_parts.append(f"{_rec.distance_m:.0f} m")
                if _rec.rating:
                    _cnt = f" ({_rec.user_rating_count:,})" if _rec.user_rating_count else ""
                    _meta_parts.append(f"⭐ {_rec.rating:.1f}{_cnt}")
                if _rec.price_level_label:
                    _meta_parts.append(_rec.price_level_label)
                st.markdown(f"**{_rec.place_name}**")
                if _meta_parts:
                    st.caption(" · ".join(_meta_parts))
                if _rec.why:
                    st.markdown(
                        f'<div class="pay-meta" style="color:#3C3C43;margin-top:2px">{_rec.why}</div>',
                        unsafe_allow_html=True,
                    )
            with _rc2:
                if _rec.google_maps_uri:
                    st.link_button("📍", _rec.google_maps_uri, use_container_width=True)
            st.markdown(
                '<div style="border-bottom:1px solid #F2F2F7;margin:4px 0 8px"></div>',
                unsafe_allow_html=True,
            )
    elif not _find:
        st.caption("Tap 🔍 to get AI-powered recommendations based on your budget and time of day.")
