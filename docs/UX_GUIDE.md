# IRQLENS UX Guide

## Product Model
IRQLENS is organized around one question sequence:
1. Is my SUT healthy?
2. What is consuming IRQ and CPU attention?
3. Which CPU or NUMA area is affected?
4. Which interface is responsible?
5. Can I collect evidence?

## Navigation
Primary navigation:
- Systems
- Overview
- IRQ
- Network
- CPU
- Diagnostics
- Sessions

Secondary navigation:
- Settings

## Core Context
The top header always shows:
- selected SUT
- online/offline state
- last update age
- current page context
- active time range

This context is preserved while moving between views.

## Systems
Use Systems to choose a target SUT.

`Open Dashboard` will:
- select the SUT
- load its current telemetry
- load its history
- load its topology
- preserve the selected SUT across navigation
- open Overview automatically

## Overview
Overview is intentionally limited to:
- a short KPI strip
- IRQ trend
- network trend
- CPU / NUMA activity map
- findings list

Use Overview to decide whether the system looks healthy, warning, or problematic within a few seconds.

## IRQ
Use IRQ to answer:
- which IRQs are hottest?
- which CPU is handling them?
- is a NIC related?

The IRQ page contains:
- ranked IRQ list
- IRQ-to-CPU heatmap
- detail table for evidence

## Network
Use Network to answer:
- which interface is busy?
- current RX/TX rate
- packet rate
- drops/errors
- related IRQs

Selecting an interface updates:
- current traffic summary
- history trend
- related IRQ list
- interface metadata/statistics

If IRQ mapping is not reliable, IRQLENS states that directly.

## CPU
Use CPU to answer:
- which CPU is hot?
- which NUMA node it belongs to?
- which IRQs are contributing?

The CPU page contains:
- topology-based CPU / NUMA map
- CPU detail panel
- hottest CPU ranking
- NUMA summary

Click any CPU cell to inspect it.

## Diagnostics
A diagnostic session is a user-created capture period for a selected SUT.

Diagnostics provides a workflow:
- choose duration
- choose data categories
- start capture
- watch capture progress
- view completed session
- download ZIP evidence

This is separate from live monitoring.

## Sessions
Sessions shows completed diagnostic captures with:
- SUT
- start/end
- duration
- captured categories
- generated files
- download actions

Use Sessions to review evidence after capture.

## Time Range
Time range is controlled from the page header, not inside charts.

Supported ranges:
- 30s
- 1m
- 5m
- 15m
- 30m
- 1h
- Custom

## Empty and Unsupported States
IRQLENS avoids fake values.

Examples:
- `Topology unavailable`
- `IRQ mapping unavailable`
- `Collecting history...`
- `No diagnostic sessions`

## Known Functional Limits
- CPU load is not currently exposed in the frontend telemetry model, so the CPU metric selector supports IRQ and SoftIRQ now and leaves CPU Load unavailable.
- Some advanced developer/debug surfaces from the prior UI were intentionally removed from the main workflow.
