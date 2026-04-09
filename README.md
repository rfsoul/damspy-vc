# damspy-vc

## Content

## Overview

`damspy-vc` is the visualisation layer of the DAMSpy system.

It takes live measurement state produced by DAMSpy and turns it into clear, human-readable views. It is responsible for presenting what the system is doing in a way that can be easily understood by an operator in the lab or viewed remotely on a phone.

---

## What This System Does

`damspy-vc` reads the current state of a running measurement and:

- displays it in a desktop-friendly interface for use in the lab
- generates a simplified mobile-friendly view
- produces a snapshot of that mobile view that can be shared externally

In simple terms, it answers:

“What is the system doing right now?”

---

## Why This Exists

DAMSpy measurements run in a controlled lab environment and can take time to complete. During a run, it is useful to:

- see progress at a glance
- confirm the system is behaving correctly
- quickly check status without being physically present

`damspy-vc` provides that visibility.

It also enables a lightweight way to view the system remotely without exposing the internal lab network.

---

## Who This Is For

This repository is intended for:

- RF engineers running measurements
- operators monitoring test progress
- developers working on the DAMSpy system

It is especially useful when:

- working inside the RF chamber environment
- checking progress from outside the lab
- validating that a measurement is proceeding as expected

---

## How It Fits Into the DAMSpy System

The DAMSpy system is made up of three main parts:

- `DAMspy-core` → runs measurements and produces system state
- `damspy-vc` → reads that state and renders it (this repository)
- `damspy-com` → displays a public snapshot of the system

The flow of data is one-way:

DAMspy-core → damspy-vc → damspy-com

`damspy-vc` sits in the middle, acting as the bridge between the measurement engine and any human-facing view.

---

## Typical Workflow

A typical usage flow looks like:

1. A measurement is started using DAMSpy
2. `DAMspy-core` updates the current system state
3. `damspy-vc` reads that state and renders:
   - a desktop view for local use
   - a mobile view for quick access
4. A snapshot of the mobile view can be generated and shared externally

This allows both local and remote visibility of the same system state.

---

## Key Outputs

`damspy-vc` produces:

- a desktop interface for in-lab use
- a mobile-friendly view of the current state
- a portable snapshot (image) representing that mobile view

The snapshot acts as the simplest external representation of the system.

---

## Design Principles

The system is designed to be:

- simple to understand at a glance
- stable and predictable in layout
- tolerant of slight delays or staleness
- independent of the measurement logic itself

It focuses on clarity rather than complexity.

---

## Summary

`damspy-vc` is the layer that makes DAMSpy visible.

It takes internal system state and turns it into something a human can quickly interpret, whether standing in the lab or checking from a phone.
