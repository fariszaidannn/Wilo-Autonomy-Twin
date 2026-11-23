# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
- **Facility manager / building operations (primary):** needs to know which zone fails first, how long they have to prepare, and how to keep pumps healthy.
- **Emergency response team:** prioritises critical zones (clinic, emergency) and decides which areas a tank serves during an outage.
- **Water utility operator (district):** plans tank supply across areas for a multi-hour outage.
- **Engineer / mentor:** checks assumptions, thresholds and models against data.
- **Hackathon judges (Water AI Hackathon 2026, Wilo Türkiye / İmece, 5 Dec, Istanbul):** look for problem–need–solution fit, meaningful AI use, feasibility and impact. The pitch is in Turkish.

## Product Purpose
Water Autonomy Twin answers: *if service is disrupted now, who loses water first, how much water will each affected area need, which supply point should serve which area, and what should the operator consider doing?* It works at two levels sharing one pattern (forecast → allocate under hard constraints → verify → explain → a person decides): a building twin with a pump load balancer, and a district outage planner (XGBoost forecast + MILP tank allocation).

## Positioning
- Remaining service time and tank allocation per zone or area under combined disturbances (this was not found in public Wilo information; that is a gap in public sources, not proof).
- The outage planner forecasts demand for the outage window, assigns whole areas to tanks with an optimizer, and is scored against actual demand rather than the forecast.
- The balancer re-allocates zone feeds across pumps, respects priority, and verifies pressure in every zone before recommending.

## Operating Context
- Mostly shown as a laptop + projector demo: it must read from across a room. It is also used at a desk and must still work at tablet width.
- Runs locally on hackathon wifi with no CDN (FastAPI backend, vanilla HTML/CSS/JS, hand-drawn SVG).
- The UI language is English with a Turkish toggle (EN / TR).

## Capabilities and Constraints
- Never controls pumps or valves. "Apply" changes only the simulated allocation. Every piece of advice requires operator approval.
- Everything is synthetic. Only Istanbul's 2022 annual consumption and district population are real inputs. No real-world accuracy claim (never "97% accuracy").
- The trained XGBoost model (`backend/models/xgb_demand.json`) and the outage database are the user's own Colab run. The app reproduces the reported figures from them.

## Evidence on Hand
- Reproduced in-app on synthetic data: hourly WAPE 5.38% (XGBoost) vs 7.10% (naive); unserved demand on 48 test scenarios: Oracle 3.68%, MILP + XGBoost 3.80%, Greedy 4.44%.
- Building worked example: Pump A 128% / Pump B 23% → 76% / 76% after moving 15.7 m³/h of Z2.
- No real building, İSKİ data, customers or testimonials exist; do not fabricate them.

## Product Principles
1. A person decides: show the evidence, the checks and the rollback, never an automatic action.
2. Honesty about data: the SIMULATED label, assumptions and limits are always visible.
3. Time is the headline: lead with "who loses water when", then explain why.
4. Priority first: critical zones and areas are protected and visibly so.

## Accessibility & Inclusion
Keyboard-visible focus; colour never the only signal (status words accompany colour); respect reduced motion for the flow animation; responsive down to tablet width; legible on a projector.
