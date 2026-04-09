# implementation_strategy.md

## Content

## Overview

This repository will provide the visualisation and capture layer for the DAMSpy ecosystem.

The first implementation must prove a simple end-to-end operator-facing loop on the Raspberry Pi:

1. load current state from a known source
2. render a local page that can be viewed in a browser on the Pi
3. keep the page simple, readable, and stable
4. establish a clean base for later mobile snapshot export and external publishing

The immediate goal is not the full external pipeline. The immediate goal is to get a working page up on the Pi that reflects DAMSpy state in a useful way.

---

## Intended Role of damspy-vc

`damspy-vc` sits between `DAMspy-core` and any human-facing display.

For the first implementation it should:

- read a simple state input
- render a local operator page
- run reliably on the Pi
- stay easy to understand and easy to extend

It must not take over measurement logic, hardware control, or public hosting responsibilities.

---

## Chosen Technical Approach

The first implementation should favour simplicity and low dependency count.

Recommended approach:

- Python 3
- a very small local web server
- HTML generated from a template or simple string-based rendering
- static CSS kept minimal
- JSON input for state

Preferred runtime style:

- one lightweight local process
- one HTTP page for viewing current state
- optional periodic reload in the browser if needed

Framework choice for first pass:

- prefer Python standard library where practical
- Flask is acceptable if it materially speeds up getting a page running
- avoid large frontend frameworks for the first pass
- avoid React, Next.js, or any SPA approach in this repo for the first implementation

This repo should start as a lightweight local viewer, not a full web application platform.

---

## First Implementation Shape

The first pass should create a very small runnable application with a shape close to:

- `src/main.py`
- `src/state_loader.py`
- `src/render.py`
- `src/templates/`
- `src/static/`
- `README.md`
- `implementation_strategy.md`

The first pass only needs one main page, for example:

- `/` or `/int`

The page should show a simple current-state summary such as:

- run name or session name
- current step or status text
- current angle / azimuth if available
- channel if available
- antenna if available
- power if available
- last updated time if available

If a field is unavailable, the page should still render cleanly with placeholder text rather than failing.

---

## State Input Strategy

For the first implementation, state should come from a simple JSON source.

Preferred initial behaviour:

- read a local JSON file
- parse only the fields needed for display
- tolerate missing keys
- fail gracefully with a readable error state on the page

This keeps the first implementation independent from deeper coupling decisions.

The first pass should not require direct imports from `DAMspy-core` runtime code unless that is already clean and stable. File-based integration is preferred initially because it is simple to test and easy to reason about.

---

## Runtime Behaviour

The first implementation must be runnable locally on the Pi with a single command.

Expected behaviour:

- start local server
- bind to a predictable local port
- serve a readable operator page
- remain LAN-local for now
- no authentication required for first local test unless needed by environment

The page should be usable from:

- the Pi browser itself
- another machine on the same LAN, if allowed

The system should prioritise “works now and is easy to inspect” over polish.

---

## Validation Strategy

The first implementation is successful if all of the following are true:

1. the app starts cleanly on the Pi
2. a browser can open the local page
3. the page displays DAMSpy-related state in a readable form
4. missing or partial JSON does not crash the app
5. the code is simple enough that later mobile rendering can be added without rewrite

Initial validation can be manual.

Minimum manual test:

- create or point to a sample JSON state file
- run the service
- open the page on the Pi
- confirm visible fields update when the JSON changes
- confirm bad or incomplete input results in a visible error or placeholder state, not a crash page

---

## Constraints

The first implementation must obey these constraints:

- do not add public internet hosting concerns to this repo
- do not implement Vercel, ISR, or public publishing here
- do not overbuild a frontend stack
- do not move measurement logic out of `DAMspy-core`
- do not depend on speculative future architecture to get the first page running
- do not import or execute legacy reference code directly as a shortcut
- do not make the first pass dependent on multiple cooperating services if one local process can do the job

This repo is responsible for local visualisation first.

---

## Forbidden Shortcuts

The following are not acceptable for the first implementation:

- embedding large amounts of measurement logic into the view layer
- building a full SPA before proving the operator page
- tightly coupling the page to unstable internal structures
- requiring cloud services for the first local Pi page
- creating multiple runtime modes before one simple mode works

The first pass should prove clarity and usefulness, not architectural ambition.

---

## First Milestone

The first milestone is:

> A DAMSpy VC page running on the Pi that can be opened in a browser and shows current system state clearly.

That milestone is sufficient even if:

- the styling is basic
- the data is from a sample JSON file
- mobile snapshot export is not yet implemented
- external publishing does not yet exist

Once that milestone is working, the next phase can extend the same codebase toward:

- improved page layout
- dedicated `/mobile` rendering
- image capture/export
- publishing outward to `damspy-com`

---

## Summary

The first implementation of `damspy-vc` should be a small, robust, Pi-friendly local viewer.

It should prove that DAMSpy state can be rendered clearly for an operator with minimal runtime complexity, and it should establish a clean foundation for later mobile and external viewing work.

## DO NOT MODIFY BELOW THIS LINE

### Purpose of This Document

`implementation_strategy.md` defines **how the system will be built** during its initial implementation phase.

It translates the intent described in:

- `docs/setup/short_description.md`
- `README.md`
- relevant guide documents in `reference/`

into a **practical construction plan** for the repository.

This document is used by both humans and automated agents to understand:

- the selected technical approach
- the intended runtime structure
- the first implementation shape
- validation expectations
- constraints that must not be violated

---

### Relationship to Other Documents

Default Phase 1 flow:

`short_description → reference/ → README → implementation_strategy → liveplan → code`

Each stage becomes progressively more concrete.

`implementation_strategy.md` bridges **project intent and implementation**.

`project_definition.md` is not part of the default Phase 1 path. If it exists later, it may provide additional structure, but this document must remain valid without it.

---

### What Belongs in the Content Section

The content section should include:

- chosen technology stack
- intended runtime architecture approach
- first implementation shape
- validation strategy
- important constraints or forbidden shortcuts
- any implementation guidance taken from relevant `reference/` documents

The goal is to make the **first coding pass clear and well-directed**.

---

### First Implementation Expectations

The strategy should explicitly define, where applicable:

- what files or components should be created first
- what libraries or frameworks may be used
- the minimal required behaviour
- what runtime pattern is intended
- what shortcuts are not acceptable

This ensures the **initial code establishes the correct foundation**.

Later refactoring may improve structure, but should not drift away from the intended approach without the document being updated.

---

### Use of Reference Material

Relevant documents in `reference/` may contain:

- prior scripts
- operating notes
- protocol details
- examples of known-good behaviour
- constraints from existing systems

These documents may strongly inform the implementation strategy.

However:

- `short_description.md` remains the source of truth for project intent
- `README.md` remains the user-facing interpretation
- `reference/` should guide implementation, not silently redefine the project

If a reference guide conflicts with the active setup documents, the conflict should be resolved explicitly in the Content section.

---

### Relationship to Liveplan

`liveplan.md` breaks the strategy into concrete implementation steps.

Each active liveplan step should remain aligned with the strategy.

If the strategy changes significantly, the liveplan should be updated to match.

---

### Editing Rules

When editing the **Content section**:

- keep it concise and directive
- avoid speculation or brainstorming
- avoid historical notes unless they directly affect implementation
- avoid vague future-roadmap language
- avoid deep architecture discussion beyond what is needed for the first implementation

The document should describe **what will be built first and how**.

---

### Important Constraint

The strategy should **prevent architectural drift** during early coding.

If an implementation rule is important, it should be written explicitly in the strategy.

Examples:

- runtime code must live in a specific location
- reference materials must not be executed directly
- the service must remain LAN-local
- interfaces must remain stable during the first implementation

---

### Phase 1 Scope Reminder

For involved projects, the strategy should describe a **minimum working first implementation**.

This does not need to solve everything.

It should define a practical first build that proves the core concept and provides a clean foundation for later expansion.
