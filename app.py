import streamlit as st
import numpy as np
import plotly.figure_factory as ff
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- 1. SETUP PAGE CONFIG & TITLE ---
st.set_page_config(layout="wide", page_title="Factory Pricing & Preemption Dashboard")
st.title("Dynamic Factory Scheduling & Surge-Pricing Simulator")
st.write("Simulate preemptive task splitting, exponential deadline penalties, and real-time quoting.")

# --- 2. SIDEBAR CONFIGURATION (SLIDERS & CONTROLS) ---
st.sidebar.header("Simulation Parameters")

# Global Settings
SETUP_COST = st.sidebar.slider("Fixed Setup/Teardown Cost ($)", 50, 1000, 250, step=50)
PANIC_DIAL = st.sidebar.slider("Deadline Panic Dial (k-factor)", 0.1, 2.0, 0.5, step=0.1)

st.sidebar.markdown("---")
st.sidebar.header("Incoming Contract Parameters")
new_cnc_hours = st.sidebar.slider("New Job CNC Hours Needed", 0, 24, 8, step=1)
new_bend_hours = st.sidebar.slider("New Job Bending Hours Needed", 0, 24, 6, step=1)
new_deadline_days = st.sidebar.slider("New Job Deadline (Days from now)", 1, 10, 3, step=1)
new_base_profit = st.sidebar.slider("New Job Desired Profit Base ($)", 500, 10000, 2000, step=250)

# Placement Slider: This allows the user to visually "squeeze" the job into different queue positions
insertion_slot = st.sidebar.slider(
    "Insert New Job After Existing Job #",
    min_value=0, max_value=3, value=1,
    help="0 = Priority #1 (Bump everything), 3 = Put at the end of the current queue"
)

# --- 3. BACKEND DATA: INITIAL JOB QUEUE ---
# Base contracts currently booked in the factory
@st.cache_data
def get_static_jobs():
    return [
        {"id": "Contract A", "cnc": 12, "bend": 0,  "deadline_days": 2, "profit": 1500, "color": "#1f77b4"},
        {"id": "Contract B", "cnc": 0,  "bend": 16, "deadline_days": 4, "profit": 2200, "color": "#ff7f0e"},
        {"id": "Contract C", "cnc": 8,  "bend": 8,  "deadline_days": 6, "profit": 3100, "color": "#2ca02c"}
    ]

active_jobs = get_static_jobs()

# --- 4. MATHEMATICAL FORMULAS ---
def calculate_urgency(days_left, k):
    """Calculates exponential urgency factor approaching 1.0 as deadline nears."""
    # Enforce a floor of 0 days so urgency doesn't explode infinitely if past deadline in sim
    days_left_clipped = np.maximum(0.01, days_left)
    return np.exp(-k * days_left_clipped)

def calculate_priority_value(base_profit, days_left, k):
    """Computes the instantaneous value of a contract based on its financial panic level."""
    urgency = calculate_urgency(days_left, k)
    return base_profit * urgency

# --- 5. SCHEDULING ENGINE WITH PREEMPTION & INSERTION ---
def generate_schedule(jobs, new_job, insert_pos, setup_penalty):
    """Processes jobs into a timeline, handling task splitting and slot insertion."""
    schedule_data = []
   
    # Deep copy to prevent modifying original data caches
    queue = [dict(j) for j in jobs]
    new_job_copy = dict(new_job)
   
    # Insert incoming job at user-selected slider index
    queue.insert(insert_pos, new_job_copy)
   
    # Track current clock/time states for both single-threaded machines independently
    cnc_clock = datetime.now()
    bend_clock = datetime.now()
   
    last_cnc_job = None
    last_bend_job = None
    displaced_cost_total = 0
   
    for job in queue:
        job_id = job["id"]
        is_new = (job_id == "New Contract")
       
        # Determine remaining days to deadline relative to current simulation run time
        days_remaining = job["deadline_days"]
        priority_hit = calculate_priority_value(job["profit"], days_remaining, PANIC_DIAL)
       
        # --- CNC Stage Processing ---
        if job["cnc"] > 0:
            start_cnc = cnc_clock
            # If a different job was on this machine previously, apply setup penalty
            if last_cnc_job is not None and last_cnc_job != job_id:
                start_cnc += timedelta(hours=1) # 1 hour physical setup time simulation
                if not is_new and insert_pos > 0:
                    # If we disrupted an existing sequence, track the financial damage
                    displaced_cost_total += setup_penalty
           
            end_cnc = start_cnc + timedelta(hours=job["cnc"])
            cnc_clock = end_cnc
            last_cnc_job = job_id
           
            schedule_data.append(dict(Task="CNC Cutting", Start=start_cnc, Finish=end_cnc, Resource=job_id, Color=job["color"]))
           
        # --- Bending Stage Processing ---
        if job["bend"] > 0:
            # Preemption check: Bending can start immediately if CNC isn't a dependency,
            # or must wait until its own CNC routing phase finishes.
            ready_pool_time = end_cnc if job["cnc"] > 0 else datetime.now()
            start_bend = max(bend_clock, ready_pool_time)
           
            if last_bend_job is not None and last_bend_job != job_id:
                start_bend += timedelta(hours=1)
                if not is_new and insert_pos > 0:
                    displaced_cost_total += setup_penalty
                   
            end_bend = start_bend + timedelta(hours=job["bend"])
            bend_clock = end_bend
            last_bend_job = job_id
           
            schedule_data.append(dict(Task="Industrial Bending", Start=start_bend, Finish=end_bend, Resource=job_id, Color=job["color"]))
           
        # Track if this specific layout caused an existing contract to breach its deadline
        final_delivery = max(cnc_clock, bend_clock)
        deadline_time = datetime.now() + timedelta(days=job["deadline_days"])
        if final_delivery > deadline_time and not is_new:
            # The displacement cost climbs based on the displaced priority equation
            displaced_cost_total += priority_hit

    return schedule_data, displaced_cost_total

# --- 6. EXECUTE SIMULATION ---
new_contract = {
    "id": "New Contract",
    "cnc": new_cnc_hours,
    "bend": new_bend_hours,
    "deadline_days": new_deadline_days,
    "profit": new_base_profit,
    "color": "#d62728" # Crimson Red to visually stand out
}

timeline, total_displacement = generate_schedule(active_jobs, new_contract, insertion_slot, SETUP_COST)

# Calculate dynamic breakthrough pricing quote
recommended_quote = new_base_profit + total_displacement

# --- 7. UI DISPLAY & METRICS CONFIGURATION ---
col1, col2, col3 = st.columns(3)
with col1:
    st.metric(
        label="Suggested Minimum Quote Price",
        value=f"${recommended_quote:,.2f}",
        delta=f"${total_displacement:,.2f} Disruption Premium",
        delta_color="inverse"
    )
with col2:
    st.metric(label="Displaced Schedule Penalties", value=f"${total_displacement - (SETUP_COST if total_displacement > SETUP_COST else 0):,.2f}")
with col3:
    st.metric(label="Total Setup Overhead Triggered", value=f"${SETUP_COST if total_displacement > 0 else 0:,.2f}")

# --- 8. GENERATE PLOTLY GANTT CHART ---
st.subheader("Real-Time Machine Allocation Timeline")

# Map custom hex colors to resource names cleanly for Plotly engine
colors_dict = {j["id"]: j["color"] for j in active_jobs}
colors_dict["New Contract"] = "#d62728"

fig = ff.create_gantt(
    timeline,
    colors=colors_dict,
    index_col='Resource',
    show_colorbar=True,
    group_tasks=True,
    showgrid_x=True,
    showgrid_y=True
)

fig.update_layout(
    xaxis_title="Timeline Hours",
    yaxis_title="Machine Stations",
    height=400,
    margin=dict(l=10, r=10, t=10, b=10)
)
st.plotly_chart(fig, use_container_width=True)

# --- 9. MATHEMATICAL URGENCY VISUALIZATION ---
st.subheader("Deadline Urgency Curve Tracker")
st.write("Shows how an active contract's internal displacement cost spikes over time.")

days_range = np.linspace(0, 10, 100)
urgency_vals = calculate_urgency(days_range, PANIC_DIAL)

fig_curve = go.Figure()
fig_curve.add_trace(go.Scatter(x=days_range, y=urgency_vals, mode='lines', name='Urgency Multiplier', line=dict(color='#ff7f0e', width=3)))
fig_curve.update_layout(
    xaxis_title="Days Remaining Until Hard Deadline",
    yaxis_title="Urgency Multiplier Penalty (0.0 to 1.0)",
    xaxis=dict(autorange="reversed"), # Show closer deadlines on the right
    height=300
)
st.plotly_chart(fig_curve, use_container_width=True)
