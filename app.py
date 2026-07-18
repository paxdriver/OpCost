import streamlit as st
import numpy as np
import plotly.figure_factory as ff
import plotly.graph_objects as go
from datetime import datetime, timedelta

# --- 1. PAGE CONFIGURATION ---
st.set_page_config(layout="wide", page_title="Factory Pricing & Preemption Dashboard")
st.title("Dynamic Factory Scheduling & Surge-Pricing Simulator")
st.write("Simulate preemptive queue insertion, deadline penalties, and real-time quoting.")

# --- 2. SIDEBAR INPUTS ---
st.sidebar.header("Simulation Parameters")

# Use number inputs instead of sliders where free-form numeric entry is more practical
SETUP_COST = st.sidebar.number_input(
	"Fixed Setup/Teardown Cost ($)",
	min_value=0.0,
	max_value=100000.0,
	value=250.0,
	step=50.0,
	help="Flat machine changeover cost when switching between different contracts."
)

PANIC_DIAL = st.sidebar.number_input(
	"Deadline Panic Dial (k-factor)",
	min_value=0.01,
	max_value=10.0,
	value=0.5,
	step=0.05,
	help="Higher values increase penalty growth as deadlines get tighter."
)

st.sidebar.markdown("---")
st.sidebar.header("Incoming Contract Parameters (All time in HOURS)")

new_cnc_hours = st.sidebar.number_input("New Contract CNC Hours Needed", min_value=0.0, max_value=240.0, value=8.0, step=1.0)
new_bend_hours = st.sidebar.number_input("New Contract Bending Hours Needed", min_value=0.0, max_value=240.0, value=6.0, step=1.0)
new_deadline_hours = st.sidebar.number_input("New Contract Deadline (Hours from now)", min_value=1.0, max_value=24.0 * 30.0, value=72.0, step=1.0)
new_base_profit = st.sidebar.number_input("New Contract Desired Profit Base ($)", min_value=0.0, max_value=1000000.0, value=2000.0, step=100.0)

# --- 3. STATIC JOB QUEUE ---
@st.cache_data
def get_static_jobs():
	# Added extra contracts for clearer overlap / displacement testing
	# All deadlines normalized to HOURS
	return [
		{"id": "Contract A", "cnc": 12.0, "bend": 0.0,  "deadline_hours": 48.0,  "profit": 1500.0, "reschedule_penalty": 280.0, "color": "#1f77b4"},
		{"id": "Contract B", "cnc": 0.0,  "bend": 16.0, "deadline_hours": 96.0,  "profit": 2200.0, "reschedule_penalty": 350.0, "color": "#ff7f0e"},
		{"id": "Contract C", "cnc": 8.0,  "bend": 8.0,  "deadline_hours": 144.0, "profit": 3100.0, "reschedule_penalty": 400.0, "color": "#2ca02c"},
		{"id": "Contract D", "cnc": 10.0, "bend": 4.0,  "deadline_hours": 120.0, "profit": 2600.0, "reschedule_penalty": 300.0, "color": "#9467bd"},
		{"id": "Contract E", "cnc": 6.0,  "bend": 10.0, "deadline_hours": 84.0,  "profit": 2000.0, "reschedule_penalty": 260.0, "color": "#8c564b"},
	]

active_jobs = get_static_jobs()
max_possible_slots = len(active_jobs)

# --- 4. MATH HELPERS ---
def calculate_urgency(hours_left: float, k: float) -> float:
	"""Exponential urgency approaching 1 as remaining time approaches 0."""
	hours_left_clipped = np.maximum(0.01, hours_left)
	return np.exp(-k * (hours_left_clipped / 24.0))  # Convert hours basis to days-scale for k sensitivity

def calculate_priority_value(base_profit: float, hours_left: float, k: float) -> float:
	"""Urgency-weighted value used for late/breach-related impact."""
	return base_profit * calculate_urgency(hours_left, k)

# --- 5. SCHEDULING ENGINE ---
def generate_schedule(jobs, new_job, insert_pos, setup_penalty, panic_k):
	"""
	Build machine schedule and compute displacement penalties.
	Rule update:
	  - If a contract is bumped by insertion, use THAT bumped contract's reschedule_penalty.
	  - Not the new contract penalty.
	"""
	now = datetime.now()
	queue_before = [dict(j) for j in jobs]
	queue_after = [dict(j) for j in jobs]
	queue_after.insert(insert_pos, dict(new_job))

	schedule_data = []
	cnc_clock = now
	bend_clock = now
	last_cnc_job = None
	last_bend_job = None

	# Displacement penalty from queue movement:
	# Every existing contract that was originally at/after insert_pos got pushed back by the insertion.
	displaced_cost_total = 0.0
	bumped_jobs = queue_before[insert_pos:] if insert_pos < len(queue_before) else []
	for bumped in bumped_jobs:
		displaced_cost_total += float(bumped.get("reschedule_penalty", 0.0))

	new_job_completion_time = now

	# Simulate machine execution
	for job in queue_after:
		job_id = job["id"]
		is_new = (job_id == "New Contract")
		job_cnc_end = now

		# CNC
		if job["cnc"] > 0:
			start_cnc = cnc_clock
			if last_cnc_job is not None and last_cnc_job != job_id:
				start_cnc += timedelta(hours=1)
				displaced_cost_total += setup_penalty
			end_cnc = start_cnc + timedelta(hours=float(job["cnc"]))
			cnc_clock = end_cnc
			last_cnc_job = job_id
			job_cnc_end = end_cnc
			schedule_data.append({
				"Task": "CNC Cutting",
				"Start": start_cnc,
				"Finish": end_cnc,
				"Resource": job_id
			})

		# Bending
		if job["bend"] > 0:
			ready_pool_time = job_cnc_end if job["cnc"] > 0 else now
			start_bend = max(bend_clock, ready_pool_time)
			if last_bend_job is not None and last_bend_job != job_id:
				start_bend += timedelta(hours=1)
				displaced_cost_total += setup_penalty
			end_bend = start_bend + timedelta(hours=float(job["bend"]))
			bend_clock = end_bend
			last_bend_job = job_id
			schedule_data.append({
				"Task": "Industrial Bending",
				"Start": start_bend,
				"Finish": end_bend,
				"Resource": job_id
			})

		final_delivery = max(cnc_clock, bend_clock)
		if is_new:
			new_job_completion_time = final_delivery

		# Late breach cost for existing jobs
		deadline_time = now + timedelta(hours=float(job["deadline_hours"]))
		if final_delivery > deadline_time and not is_new:
			hours_remaining = max(0.01, (deadline_time - now).total_seconds() / 3600.0)
			displaced_cost_total += calculate_priority_value(float(job["profit"]), hours_remaining, panic_k)

	return schedule_data, displaced_cost_total, new_job_completion_time

# --- 6. NEW CONTRACT OBJECT ---
new_contract = {
	"id": "New Contract",
	"cnc": float(new_cnc_hours),
	"bend": float(new_bend_hours),
	"deadline_hours": float(new_deadline_hours),
	"profit": float(new_base_profit),
	"reschedule_penalty": 400.0,  # Explicit penalty field for the incoming contract
	"color": "#d62728"
}

# --- 7. SESSION STATE ---
if "selected_slot" not in st.session_state:
	st.session_state.selected_slot = 1

# --- 8. STRATEGY BUTTONS ---
st.subheader("Quick Strategy Presets")
btn_col1, btn_col2, btn_col3 = st.columns(3)

with btn_col1:
	if st.button("Highest Priority (Instant Run)", use_container_width=True, help="Insert at front of queue (slot 0)."):
		st.session_state.selected_slot = 0
		st.rerun()

with btn_col2:
	if st.button("Median Priority (Balanced Queue)", use_container_width=True, help="Insert in middle of queue."):
		st.session_state.selected_slot = max_possible_slots // 2
		st.rerun()

with btn_col3:
	if st.button("Cheapest Price (Lowest Disruption)", use_container_width=True, help="Find least-penalty slot; tie-breaker prefers BACK of queue."):
		best_slot = max_possible_slots
		lowest_penalty = float("inf")
		for test_slot in range(max_possible_slots + 1):
			_, penalty, _ = generate_schedule(active_jobs, new_contract, test_slot, SETUP_COST, PANIC_DIAL)
			# Fix: ensure cheapest works; and if equal penalty, choose lower priority (further back)
			if (penalty < lowest_penalty) or (penalty == lowest_penalty and test_slot > best_slot):
				lowest_penalty = penalty
				best_slot = test_slot
		st.session_state.selected_slot = best_slot
		st.rerun()

# Optional manual slot override still kept as numeric input
st.sidebar.markdown("---")
st.sidebar.header("Queue Placement")
insertion_slot = st.sidebar.number_input(
	"Insertion Position Slot # (0 = front, N = back)",
	min_value=0,
	max_value=max_possible_slots,
	value=int(st.session_state.selected_slot),
	step=1
)
st.session_state.selected_slot = int(insertion_slot)

# --- 9. RUN SIMULATION ---
timeline, total_displacement, eta_completion = generate_schedule(
	active_jobs, new_contract, insertion_slot, SETUP_COST, PANIC_DIAL
)

recommended_quote = float(new_base_profit) + float(total_displacement)
time_delta_hours = max(0.1, (eta_completion - datetime.now()).total_seconds() / 3600.0)

# --- 10. METRICS ---
col1, col2, col3, col4 = st.columns(4)

with col1:
	st.metric(
		label="Suggested Minimum Quote Price",
		value=f"${recommended_quote:,.2f}",
		delta=f"${total_displacement:,.2f} Disruption Premium",
		delta_color="inverse"
	)

with col2:
	st.metric(label="Displacement + Late Penalties", value=f"${total_displacement:,.2f}")

with col3:
	st.metric(label="Configured Setup Cost (Per Changeover)", value=f"${SETUP_COST:,.2f}")

with col4:
	meets_deadline = time_delta_hours <= float(new_deadline_hours)
	st.metric(
		label="Required Target Deadline",
		value=f"{time_delta_hours:.1f} Hours",
		delta="Within requested deadline" if meets_deadline else "Breaches requested deadline",
		delta_color="normal" if meets_deadline else "inverse"
	)

# --- 11. GANTT CHART ---
st.subheader("Real-Time Machine Allocation Timeline")

colors_dict = {j["id"]: j["color"] for j in active_jobs}
colors_dict["New Contract"] = "#d62728"

fig = ff.create_gantt(
	timeline,
	colors=colors_dict,
	index_col="Resource",
	show_colorbar=True,
	group_tasks=True,
	showgrid_x=True,
	showgrid_y=True
)

fig.update_layout(
	xaxis_title="Timeline",
	yaxis_title="Machine Stations",
	height=450,
	margin=dict(l=10, r=10, t=10, b=10)
)
st.plotly_chart(fig, use_container_width=True)

# --- 12. URGENCY CURVE ---
st.subheader("Deadline Urgency Curve Tracker")
st.write("Shows urgency multiplier as remaining time decreases (hours normalized).")

hours_range = np.linspace(1, 240, 200)
urgency_vals = calculate_urgency(hours_range, PANIC_DIAL)

fig_curve = go.Figure()
fig_curve.add_trace(
	go.Scatter(
		x=hours_range,
		y=urgency_vals,
		mode="lines",
		name="Urgency Multiplier",
		line=dict(color="#ff7f0e", width=3)
	)
)
fig_curve.update_layout(
	xaxis_title="Hours Remaining Until Hard Deadline",
	yaxis_title="Urgency Multiplier (0.0 to 1.0)",
	xaxis=dict(autorange="reversed"),
	height=320
)
st.plotly_chart(fig_curve, use_container_width=True)
