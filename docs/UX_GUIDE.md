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
- Overview
- CPU
- IRQ
- Network
- Storage & PCIe
- Collect Logs

Capture history is part of Collect Logs. There are no Admin, Settings, Management, or standalone Systems/Sessions views. See `IRQLENS_UI_RULES.md` for the UI contract.

## Core Context
The top header always shows:
- selected SUT
- online/offline state
- last update age
- current page context
- active time range

This context is preserved while moving between views.

## SUT Context
The header SUT dropdown lists every registered SUT with its status (`ONLINE`, `STALE`, `OFFLINE`) and switches between them. On load the UI restores the last selection, otherwise selects a remote SUT, falling back to local. Statuses refresh every few seconds even when the selected SUT has stopped reporting.

The header also shows the `irqbalance` state of the selected SUT and a **Pause Live Updates** button. While paused, monitoring views keep the frame on screen; Collect Logs keeps updating.

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
- IRQ-to-CPU heatmap of the Top 25 active IRQs (25 / 50 / 100 / All), with columns per CPU, per NUMA node, or per socket
- detail table for evidence, including a Cross-NUMA column

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
- IRQ, SoftIRQ, or CPU utilization metric selection
- CPU utilization history

Click any CPU cell to inspect it.

## Storage & PCIe
Use Storage & PCIe to answer:
- which block device is busy, and how slow is it?
- is a card running below its maximum link speed or width?
- are AER errors present or still increasing?

The page contains:
- KPI strip: total IOPS, read/write MB/s, max await, PCIe link warnings, AER errors
- per-device IOPS, throughput, and latency/queue-depth trends
- block-device table with queue settings and NUMA placement
- PCIe card table with current vs maximum link and AER counters

## Collect Logs
A diagnostic session is a user-created capture period for a selected SUT.

Diagnostics provides a workflow:
- name the capture
- choose duration
- choose data categories
- start capture
- watch capture progress
- view completed session
- download the Excel workbook or ZIP evidence
- inspect remote-command job status and failures

This is separate from live monitoring.

Remote agents execute fixed command categories on the selected SUT. Queued or running command jobs may continue after the timed capture ends. Wait for those jobs to finish before downloading final evidence; missing tools and permissions appear as failed jobs with preserved output.

## Capture History
Recent Captures within Collect Logs shows diagnostic captures with:
- SUT
- start/end
- duration
- captured categories
- generated files
- download actions

Select a capture to review files and remote-command status, generate an HTML report, or download the Excel workbook or a ZIP. Snapshot categories export JSON, CSV, and TXT; time-series files are JSON; each capture has one `.xlsx` workbook. A failed download shows an error message instead of saving a broken file.

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
- CPU utilization depends on receiving two usable CPU-counter samples.
- Interface addresses require `ip` with JSON support on the monitored host and refresh at most every 30 seconds.
- Agents older than 1.1.0 do not execute queued command jobs.
- Some advanced developer/debug surfaces from the prior UI were intentionally removed from the main workflow.
