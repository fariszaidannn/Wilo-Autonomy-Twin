---
version: 1
slug: "frontend-index-html"
primary_target: "frontend/index.html"
related_targets: []
---

# Surface: Water Autonomy Twin dashboard (frontend/index.html)

Mode: Operate. Audience: facility managers, district water operators and hackathon judges, shown mostly on a laptop + projector. Task: see who loses water when, review the AI's recommendation, approve or dismiss in the twin, compare outage policies. Constraints: vanilla JS + hand-drawn SVG, no CDN, EN/TR toggle, SIMULATED badge and limits always visible, never controls pumps.

## Direction contract

THESIS: Water service drawn as a transit network. Supply points are termini, demand units are stations, and the line a station sits on is who feeds it. The screen refuses the KPI-card grid on grey that every utility dashboard ships.

OWN-WORLD: White ground, deep navy ink #0E1A2B, line colours taken from Istanbul's network (red M-line, blue, green, ochre, plus teal and violet for extra tanks). A thick navy rule under the header, pill tabs, round line bullets with a two-letter code, station pips (white with a navy ring, a large ring for interchange/termini), and dashed red rings for disrupted stations. Tabular numerals everywhere.

STORY: The operator reads the status line first ("Pump A crowded while Pump B has room" / "Z3 loses service first in 1 h 41 min"), sees the network diagram showing which pump or tank feeds whom, reads the recommendation with its checks, then approves, dismisses or rolls back. In the planner they pick a scenario, see each area's line to its tank, and compare Oracle / MILP + XGBoost / Greedy.

FIRST VIEWPORT: The header with the brand, pill tabs (Building, Scenario lab, Usage & data, Outage planner), the SIMULATED pill and the EN/TR switch. Below, a 2-column body: left (about 65%), a status headline at 40px, the building drawn as a line diagram (tank as the origin terminus, pumps as interchanges, zones as stations; line width = flow; pump load bar with 85/100 marks), the disturbance switches under it. Right rail: the balancer card with status bullet, the change in words, before/after bars, checks and the Approve / Dismiss / Roll back / Re-evaluate buttons. Live trends span below the fold.

FORM: Istanbul Line Map, position 1 on the ordered list (IMPECCABLE'S PICK, chosen over the dealt İznik Glaze), seed:ac273bfd60c39b49. Signature move: assignment-as-line. Every feed (pump→zone, tank→area) is a coloured line whose colour is the supplier; a proposed change appears as a dashed new line; an unserved station gets a dashed red ring and drops off its line.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

Memorable moment: approving the plan redraws Z2's station onto the blue line as the dashed proposal turns solid.
