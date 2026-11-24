# Calibration: concierge.approve_site

Model `qwen3.8-27b`, prompt `ee4cd227e186`, 27 labelled cases, fitted temperature **T = 1.0** (2026-11-24).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 1.000 | 0.004 | 0.000 |
| calibrated, 5-fold cross-validated | 1.000 | 0.004 | 0.000 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 1.000 | 27 |
| 0.6 | 1.00 | 1.000 | 27 |
| 0.7 | 1.00 | 1.000 | 27 |
| 0.8 | 1.00 | 1.000 | 27 |
| 0.85 | 1.00 | 1.000 | 27 |
| 0.9 | 1.00 | 1.000 | 27 |
| 0.95 | 1.00 | 1.000 | 27 |
| 0.99 | 0.81 | 1.000 | 22 |

Cases labelled *unclear* (graded by action, not label): 5.

![Reliability diagram](concierge.approve_site.svg)
