"""
pages/Trips.py — Trip management page for bunq Travel Buddy.

Create, edit, delete, and activate trips.
The active trip is stored in st.session_state["active_trip_id"] and shared
across all pages of the Streamlit app within the same browser session.
"""

from __future__ import annotations

import streamlit as st
import datetime

from trips import Trip, create_trip, delete_trip, get_trip, load_trips, update_trip

st.set_page_config(
    page_title="My Trips · bunq Travel Buddy",
    page_icon="🧳",
    layout="centered",
)

st.markdown("""
<style>
  .block-container { max-width: 480px; padding-top: 4rem; }

  .trip-card {
    background: #ffffff; border: 2px solid #111; border-radius: 16px;
    padding: 18px 20px; margin-bottom: 12px;
  }
  .trip-card.active { border-color: #FF7819; }

  .trip-name   { font-size: 1.15rem; font-weight: 700; color: #1C1C1E; }
  .trip-budget { font-size: 1.5rem;  font-weight: 700; color: #1C7C3A; }
  .trip-dates  { font-size: 0.85rem; color: #3C3C43; margin-top: 2px; }
  .trip-ccy    { font-size: 0.85rem; color: #FF7819;  margin-top: 2px; font-weight: 600; }
  .trip-id     { font-size: 0.72rem; color: #C7C7CC;  font-family: monospace; }

  .badge-active {
    display: inline-block; padding: 2px 10px; border-radius: 20px;
    font-size: 0.75rem; font-weight: 600; color: white;
    background: #FF7819; margin-left: 8px; vertical-align: middle;
  }

  div.stButton > button { border-radius: 50px; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────────
if "active_trip_id" not in st.session_state:
    st.session_state["active_trip_id"] = None
if "_editing_trip_id" not in st.session_state:
    st.session_state["_editing_trip_id"] = None

# ── Header ─────────────────────────────────────────────────────────────────────
st.markdown("## 🧳 My Trips")

active_id: str | None = st.session_state["active_trip_id"]
if active_id:
    active = get_trip(active_id)
    if active:
        st.success(f"Active trip: **{active.name}** · Budget: **€ {active.budget_eur:,.2f}**")
    else:
        st.session_state["active_trip_id"] = None

st.divider()

# ── Create new trip ────────────────────────────────────────────────────────────
st.markdown("### New trip")

with st.form("create_trip", clear_on_submit=True):
    name_input = st.text_input(
        "Trip name",
        placeholder="e.g.  Summer in Japan",
        max_chars=60,
    )
    budget_input = st.number_input(
        "Budget (EUR)",
        min_value=0.0,
        max_value=1_000_000.0,
        value=1_000.0,
        step=50.0,
        format="%.2f",
    )
    ccy_input = st.text_input(
        "Destination currency (ISO code)",
        placeholder="e.g. JPY, USD, GBP",
        max_chars=3,
        key="create_ccy",
    )
    date_cols = st.columns(2)
    with date_cols[0]:
        start_input = st.date_input("Start date", value=None, key="create_start")
    with date_cols[1]:
        end_min = start_input + datetime.timedelta(days=1) if start_input else None
        end_input = st.date_input("End date", value=None, min_value=end_min, key="create_end")
    submitted = st.form_submit_button("＋ Create trip", use_container_width=True)

if submitted:
    if not name_input.strip():
        st.error("Please enter a trip name.")
    elif start_input and end_input and end_input <= start_input:
        st.error("End date must be after start date.")
    else:
        start_str = start_input.isoformat() if start_input else None
        end_str = end_input.isoformat() if end_input else None
        new_trip = create_trip(
            name_input, budget_input,
            start_date=start_str, end_date=end_str,
            default_currency=ccy_input.upper().strip() or None,
        )
        st.success(f"Created **{new_trip.name}** with budget **€ {new_trip.budget_eur:,.2f}**")
        st.rerun()

st.divider()

# ── Trip list ──────────────────────────────────────────────────────────────────
trips = load_trips()

st.markdown(f"### Your trips  ({len(trips)})")

if not trips:
    st.info("No trips yet — create one above to get started.")
else:
    for trip in trips:
        is_active = trip.id == st.session_state["active_trip_id"]
        is_editing = trip.id == st.session_state["_editing_trip_id"]
        card_class = "trip-card active" if is_active else "trip-card"
        active_badge = '<span class="badge-active">● active</span>' if is_active else ""

        if trip.start_date or trip.end_date:
            start_label = trip.start_date or "?"
            end_label = trip.end_date or "?"
            dates_html = f'<div class="trip-dates">📅 {start_label} → {end_label}</div>'
        else:
            dates_html = ""

        ccy_html = (
            f'<div class="trip-ccy">💱 {trip.default_currency}</div>'
            if trip.default_currency else ""
        )

        st.markdown(f"""
        <div class="{card_class}">
          <div class="trip-name">{trip.name}{active_badge}</div>
          <div class="trip-budget">€ {trip.budget_eur:,.2f}</div>
          {ccy_html}
          {dates_html}
          <div class="trip-id">id: {trip.id}</div>
        </div>
        """, unsafe_allow_html=True)

        btn_cols = st.columns([2, 2, 2])

        with btn_cols[0]:
            if is_active:
                if st.button("✓ Active", key=f"deactivate_{trip.id}", use_container_width=True):
                    st.session_state["active_trip_id"] = None
                    st.rerun()
            else:
                if st.button("Set active", key=f"activate_{trip.id}", use_container_width=True):
                    st.session_state["active_trip_id"] = trip.id
                    st.rerun()

        with btn_cols[1]:
            edit_label = "Cancel" if is_editing else "✏️ Edit"
            if st.button(edit_label, key=f"edit_{trip.id}", use_container_width=True):
                st.session_state["_editing_trip_id"] = None if is_editing else trip.id
                st.rerun()

        with btn_cols[2]:
            if st.button("🗑 Delete", key=f"delete_{trip.id}", use_container_width=True):
                if is_active:
                    st.session_state["active_trip_id"] = None
                if is_editing:
                    st.session_state["_editing_trip_id"] = None
                delete_trip(trip.id)
                st.rerun()

        # ── Inline edit form ───────────────────────────────────────────────────
        if is_editing:
            existing_start = (
                datetime.date.fromisoformat(trip.start_date) if trip.start_date else None
            )
            existing_end = (
                datetime.date.fromisoformat(trip.end_date) if trip.end_date else None
            )
            with st.form(f"edit_form_{trip.id}"):
                new_name = st.text_input(
                    "Name", value=trip.name,
                    max_chars=60, key=f"ename_{trip.id}",
                )
                new_budget = st.number_input(
                    "Budget (EUR)", value=trip.budget_eur,
                    min_value=0.0, max_value=1_000_000.0,
                    step=50.0, format="%.2f",
                    key=f"ebudget_{trip.id}",
                )
                new_ccy = st.text_input(
                    "Destination currency (ISO code)",
                    value=trip.default_currency or "",
                    max_chars=3,
                    key=f"eccy_{trip.id}",
                )
                ecols = st.columns(2)
                with ecols[0]:
                    new_start = st.date_input(
                        "Start date", value=existing_start, key=f"estart_{trip.id}"
                    )
                with ecols[1]:
                    edit_end_min = new_start + datetime.timedelta(days=1) if new_start else None
                    new_end = st.date_input(
                        "End date", value=existing_end, min_value=edit_end_min, key=f"eend_{trip.id}"
                    )
                if st.form_submit_button("Save changes", use_container_width=True):
                    if not new_name.strip():
                        st.error("Name cannot be empty.")
                    elif new_start and new_end and new_end <= new_start:
                        st.error("End date must be after start date.")
                    else:
                        update_trip(
                            trip.id,
                            name=new_name,
                            budget_eur=new_budget,
                            default_currency=new_ccy.upper().strip() or None,
                            start_date=new_start.isoformat() if new_start else None,
                            end_date=new_end.isoformat() if new_end else None,
                        )
                        st.session_state["_editing_trip_id"] = None
                        st.rerun()

        st.write("")  # spacing between cards
