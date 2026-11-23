"""Build the outage caches ahead of time (used by the Render build step).

Converts the workbook to outage.db (about 40 s) and writes the 48-scenario
evaluation cache, so the deployed app does not parse the 12 MB workbook at
start-up, which is the most memory-hungry step. Safe to run locally too.
"""
import sys

from app.main import AppState

state = AppState(start_clock=False, warm_outage=False)
state._warm_outage()
o = state.outage
print(f"outage planner: {o['status']} ({o['message']}); evaluation ready: {o['evaluation'] is not None}")
if o["status"] != "ready" or o["evaluation"] is None:
    print(o.get("error") or o.get("eval_message"), file=sys.stderr)
    sys.exit(1)
