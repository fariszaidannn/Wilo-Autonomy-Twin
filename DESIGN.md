---
name: Water Autonomy Twin
description: Water service drawn as a transit network; who loses water first, and which supply should feed whom.
colors:
  ground: "#ffffff"
  tint: "#f3f5f8"
  ink: "#0e1a2b"
  ink-2: "#33415a"
  muted: "#586478"
  hair: "#d3d9e2"
  line-1: "#0072bc"
  line-2: "#c27c00"
  line-3: "#00968f"
  line-4: "#7b3f99"
  line-5: "#6e8b00"
  line-6: "#c2187b"
  good: "#1b7f4c"
  warn: "#a65800"
  crit: "#c8102e"
  good-bg: "#e7f4ec"
  warn-bg: "#fbefe0"
  crit-bg: "#fbe7ea"
  simulated-bg: "#ffe9a8"
  simulated-ink: "#4a3500"
typography:
  display:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "40px"
    fontWeight: 800
    lineHeight: 1.12
    letterSpacing: "-0.02em"
    fontFeature: "\"tnum\" 1"
  headline:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "22px"
    fontWeight: 800
    letterSpacing: "-0.01em"
  title:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 800
    letterSpacing: "-0.01em"
  figure:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "30px"
    fontWeight: 800
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontFeature: "\"tnum\" 1"
  body:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "\"tnum\" 1"
  label:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 800
  caption:
    fontFamily: "Overpass, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
rounded:
  bar: "4px"
  field: "8px"
  frame: "12px"
  pill: "999px"
spacing:
  s1: "4px"
  s2: "8px"
  s3: "12px"
  s4: "16px"
  s5: "24px"
  s6: "32px"
  s7: "48px"
components:
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.ink-2}"
    rounded: "{rounded.pill}"
    padding: "8px 16px 6px"
  tab-hover:
    backgroundColor: "{colors.tint}"
    textColor: "{colors.ink}"
  tab-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ground}"
  button:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    padding: "8px 16px 6px"
  button-hover:
    backgroundColor: "{colors.tint}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ground}"
    rounded: "{rounded.pill}"
    padding: "8px 16px 6px"
  button-sm:
    rounded: "{rounded.pill}"
    padding: "5px 12px 3px"
  segment:
    backgroundColor: "{colors.ground}"
    rounded: "{rounded.pill}"
    padding: "6px 14px 4px"
  segment-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ground}"
  field:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    rounded: "{rounded.field}"
    padding: "7px 10px 5px"
  status-good:
    backgroundColor: "{colors.good-bg}"
    textColor: "{colors.good}"
    rounded: "{rounded.pill}"
    padding: "3px 10px 1px"
  status-warn:
    backgroundColor: "{colors.warn-bg}"
    textColor: "{colors.warn}"
    rounded: "{rounded.pill}"
    padding: "3px 10px 1px"
  status-crit:
    backgroundColor: "{colors.crit-bg}"
    textColor: "{colors.crit}"
    rounded: "{rounded.pill}"
    padding: "3px 10px 1px"
  line-bullet:
    textColor: "{colors.ground}"
    rounded: "{rounded.pill}"
    size: "30px"
  simulated-pill:
    backgroundColor: "{colors.simulated-bg}"
    textColor: "{colors.simulated-ink}"
    rounded: "{rounded.pill}"
    padding: "5px 12px 3px"
---

# Design System: Water Autonomy Twin

## Overview

**Creative North Star: "The Istanbul Line Map"**

Water service is drawn as a transit network. Supply points (the building tank, pumps, district tanks) are termini and interchanges, demand units (zones, areas) are stations, and the coloured line a station sits on is the supplier that feeds it. Line width is flow. A proposed reassignment is a dashed new line; approving it turns the line solid. A station that loses water gets a dashed red ring and drops off its line. The network diagram is the hero of every view, and everything around it reads like transit wayfinding: white ground, deep navy ink, round line bullets, pill tabs, thick rules.

The system is flat and typographic. There are no cards on grey; sections are separated by navy rules laid across white, the way a line map separates its legend. Density is operator-grade but sized for a projector at the back of a room: 16px body, 40px status headline, 800-weight figures, tabular numerals everywhere. It is light-only by use scene.

**Key Characteristics:**
- Assignment-as-line: a feed is a coloured line whose colour is the supplier.
- Six line colours in a fixed, validated categorical order; status colours never double as line colours.
- Navy rules (5px header, 3px panel, 2px table head) instead of cards and shadows.
- Pills for every control: tabs, buttons, segments, status, language switch.
- One self-hosted variable face, Overpass, at 400 for prose and 700-800 for everything that names, controls or counts.

## Colors

A white wayfinding ground with one deep navy ink, six categorical line colours, and three reserved status colours.

### Primary
- **Network Navy** (ink): all text, every structural rule, station-pip rings, terminus frames, selected tabs and primary buttons. It is the map's line-work and the only "brand" colour.

### Secondary
- **The six line colours**, in fixed order: **Line Blue** (line-1), **Line Ochre** (line-2), **Line Teal** (line-3), **Line Violet** (line-4), **Line Olive** (line-5), **Line Magenta** (line-6). The order was validated for colour-vision-deficiency and normal-vision separation and for contrast on white. Pumps own slots 1-2 (PA blue, PB ochre); building zones take slots 3-5; district tanks T01-T06 take slots 1-6, and T07-T12 reuse the same six hues with a white centre stripe (a "cased" line and a white ring inside the bullet or terminus). Line Blue doubles as the link and focus-ring colour.

### Tertiary
- **Reserved status**: **Service Green** (good), **Caution Amber** (warn), **Outage Red** (crit), each with a pale wash (good-bg, warn-bg, crit-bg) for pill and toast backgrounds. Outage Red also draws the dashed ring of a station with no water and the dashed frame of a failed pump or overdrawn tank.
- **Simulated Amber** (simulated-bg on simulated-ink): only the always-visible SIMULATED DATA pill in the header.

### Neutral
- **Ground** (ground): the page and every surface. **Platform Tint** (tint): hover fills, bar tracks, the disclaimer banner. **Hairline** (hair): table row dividers, field borders, footer and details rules. **Ink 2** (ink-2): secondary text, inactive tab text, notes beside values. **Muted** (muted): captions, chart axis labels, panel notes.

### Named Rules
**The Line Is The Supplier Rule.** A line colour means exactly one thing: which supplier feeds this station. Never use a line colour as decoration, a section accent, or a status.

**The Reserved Status Rule.** Green, amber and red are status only, never a series colour, and they always appear with an icon and a word (check, alert-triangle, clock, dot). Colour alone never carries a state.

**The Fixed Order Rule.** Assign line colours by slot in the fixed order 1-6; beyond six, repeat the order with a white centre stripe rather than introducing a seventh hue.

## Typography

**Display Font:** Overpass (self-hosted variable woff2, weights 100-900, latin and latin-ext subsets, with system-ui fallback)
**Body Font:** Overpass
**Label/Mono Font:** Overpass with tabular numerals; no mono face.

**Character:** Overpass descends from Highway Gothic, the road-sign face, so it reads as wayfinding at distance. The system uses three weights: 400 for running prose, 700 for tabs, segments and field labels, and 800 for headings, figures, bullets, buttons, status and anything an operator scans for.

### Hierarchy
- **Display** (800, 40px, 1.12, -0.02em; 28px under 820px): the status headline at the top of a view ("Z3 loses service first in 1 h 41 min"), max 30ch.
- **Headline** (800, 22px): panel titles that sit on a panel rule. The recommendation title runs at 21px/1.2.
- **Title** (800, 17px): sub-headings and emphasized clock values.
- **Figure** (800, 30px, 1.1, -0.02em): the route-strip figures; in the schematic, zone pressure is set at 27px/800.
- **Body** (400, 16px, 1.45): prose and controls; secondary lines at 17px in ink-2 under the headline.
- **Label** (800, 14px): control-group headings, status pills, table headers (13px, muted).
- **Caption** (400, 13px): chart axes, tooltips, footnotes, details under checks.

### Named Rules
**The Tabular Everywhere Rule.** `font-variant-numeric: tabular-nums` is set on body, so every figure, clock and bar value aligns in columns. Do not switch it off.

**The Optical Baseline Rule.** Overpass sits high in its box, so every pill-shaped control carries 2px more padding on top than on the bottom (8px/6px, 5px/3px, 3px/1px) and round bullets get 2px top padding. Keep that asymmetry on any new pill.

## Layout

A sticky 68px header (brand, pill tabs, SIMULATED pill, EN/TR switch) sits on a 5px navy rule, followed by a tint disclaimer banner that is always visible. Main content has 24px/32px padding and a spacing scale of 4, 8, 12, 16, 24, 32, 48px.

- **Building view:** a two-column split, fluid left column plus a 420px right rail (outage outlook, then the balancer) with 32px gap; the rail is sticky at 92px and scrolls on its own when taller than the screen. Panels stack with 24px between them. The outlook's bento is two up in the rail (time to threshold spans both), three up when the rail goes full width below 1100px, one up at 700px.
- **Planner view:** three columns (300px, fluid map, 360px); the right column drops below at 1280px; everything stacks at 820px.
- **Controls:** auto-fit grid with 230px minimum columns. **Charts:** two up, one up at 820px. **Route strips:** auto-fit, 180px minimum.
- **At 1100px** the split becomes a single column with the rail moved directly under the status headline, the header wraps, and padding tightens to 16px/24px. **At 820px** tabs move to their own scrolling row and the building schematic scrolls horizontally at a 680px minimum rather than shrinking illegibly.

## Elevation & Depth

Flat. Depth comes from navy rules and tint fills, never from layered surfaces. The one shadow is the chart tooltip, a navy chip that floats over the plot (`0 6px 20px rgba(14, 26, 43, .25)`).

### Named Rules
**The Rules Not Cards Rule.** A section is a 3px navy rule across the top with its title on it, not a bordered or shadowed card. The header carries the heaviest rule (5px), panels 3px, table heads 2px, row dividers 1px hairline. The only framed surface is the planner map (1px hairline, 12px corners, off-white #fbfcfd), which is a map sheet, not a card. The one exception is the outage-outlook bento in the Building rail: its tiles take the same map-sheet frame (1px hairline, 12px corners) on white, still with no shadow and no grey ground, because each tile is a separate reading of one simulation and the frame keeps the three readings apart inside one ruled panel.

## Shapes

Round where it is transit: pills for every control (999px), circles for bullets, station pips and step numbers, rounded stadium shapes for pump interchanges and zone stations in the schematic, 9px-cornered squares for tank termini. Soft 4px corners on bars and tracks, 8px on fields, toasts and tooltips. Lines in diagrams use round caps and joins and run octilinear (horizontal, vertical, 45 degrees), as on a line map.

Dash vocabulary is semantic and fixed:
- **Dashed in the supplier colour** (10 8): a proposed new line, not yet approved.
- **Dashed red ring or frame** (about 5 4): a station with no water, a failed pump, a cut city supply.
- **Dashed navy outline** on a bar: the "after" value of a proposed change.
- **Dotted white flow** (2 14) animated along a live line: water moving; paused under reduced motion.

## Components

### Buttons
Pill-shaped, confident, navy-outlined.
- **Shape:** full pill (999px), 2px navy border.
- **Default:** ground fill, ink text, 800 at 15px, padding 8px 16px 6px.
- **Primary:** navy fill, white text; hover deepens to #22324a.
- **Hover / Active / Focus:** tint fill on hover; press scales to 0.97 over 100ms; focus is a 3px Line Blue outline with 2px offset. Disabled is 45% opacity.
- **Small:** 5px 12px 3px at 14px.

### Tabs
- Borderless pills in ink-2 at 700/16px; hover fills tint; the selected tab is a solid navy pill with white text. Proper `role="tablist"` with roving tabindex.

### Segmented control and language switch
- **Segments:** hairline-bordered pills that turn solid navy when pressed (`aria-pressed`). The EN/TR switch is a single 2px navy-outlined pill split in two, the active half filled navy.

### Status pills
- Pale wash background, saturated text, 800/14px, an inline 15px authored SVG icon plus a word. Tones: good (check), warn (alert), crit (alert), info (clock, #0a4f85 on #e3eef8), neutral (dot, ink-2 on tint).

### Inputs / Fields
- **Style:** 2px hairline border, 8px corners, ground fill, padding 7px 10px 5px. Field labels are 700/14px ink-2 above the field.
- **Focus:** border turns navy; no glow.
- **Switches:** 44 by 26px track, grey #c3cad5 off, navy on, red for a destructive disturbance; the knob slides with the ease-out curve.

### Line bullet (signature)
- A 30px circle filled with the line colour carrying a white 800/12px two-character code (pump or tank number). A striped (T07-T12) bullet adds an inset white ring. Used beside load bars, in the tank table (24px) and as termini on the map.

### Station pip (signature)
- White circle with a 3px navy ring for an affected station; a small grey pip (#c3cad5) for an unaffected one. An unserved station adds a 15px dashed red ring with a red wash and a red label. Focus rings the pip in Line Blue at 4px.

### Route strip (signature)
- Key figures laid along a 5px navy line with a white, 4px-navy-ringed station pip above each figure: 30px/800 value, 14px ink-2 key. This replaces the KPI-card row.

### Load bar
- A 8px fill in the supplier's line colour on a tint track, navy tick marks at 85% and 100%, a dashed navy outline for the proposed after-value, the value right-aligned at 800. Fill turns Outage Red at or above 100%. Width transitions over 600ms ease-out.

### Network schematic
- Hand-drawn SVG. Tank as origin terminus, pumps as stadium interchanges with a load bar inside, zones as stadium stations whose height grows with the number of lines feeding them. Line width encodes flow. On approval a new line draws in over 800ms (`cubic-bezier(.16, 1, .3, 1)`), shown instantly under reduced motion.

## Do's and Don'ts

### Do:
- **Do** draw every feed as a line in its supplier's colour, with width as flow, and show a proposal as a dashed line in the receiving supplier's colour.
- **Do** separate sections with a 3px navy rule and a 22px/800 title on it.
- **Do** pair every status colour with an icon and a word, and keep the SIMULATED DATA pill and the never-controls-pumps banner visible on every view.
- **Do** use Overpass at 400 or 800 only, with tabular numerals, and the 2px-heavier top padding on pills.
- **Do** extend the line palette by repeating the six hues with a white centre stripe.
- **Do** honour `prefers-reduced-motion`: flow dots stop, new lines appear drawn.

### Don't:
- **Don't** put content in shadowed or bordered cards on a grey ground; the world is white with rules.
- **Don't** use green, amber or red as a line or series colour, or a line colour as a status.
- **Don't** add a seventh line hue, or reorder the six.
- **Don't** add a dark theme; the use scene is a laptop and a projector, and `color-scheme` is light.
- **Don't** use icon fonts or emoji; icons are authored inline SVG at one stroke weight (2.4).
