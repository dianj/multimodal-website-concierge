# Calibration: concierge.approve_site

Model `qwen3.8-27b`, prompt `dd83a782c961`, 34 labelled cases, fitted temperature **T = 1.0** (2026-11-25).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 1.000 | 0.012 | 0.002 |
| calibrated, 5-fold cross-validated | 1.000 | 0.012 | 0.002 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 1.000 | 34 |
| 0.6 | 1.00 | 1.000 | 34 |
| 0.7 | 1.00 | 1.000 | 34 |
| 0.8 | 1.00 | 1.000 | 34 |
| 0.85 | 1.00 | 1.000 | 34 |
| 0.9 | 0.97 | 1.000 | 33 |
| 0.95 | 0.94 | 1.000 | 32 |
| 0.99 | 0.79 | 1.000 | 27 |

Cases labelled *unclear* (graded by action, not label): 6.

![Reliability diagram](concierge.approve_site.svg)
