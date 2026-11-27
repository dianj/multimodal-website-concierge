# Calibration: concierge.instruction_in_reply

Model `qwen3.8-27b`, prompt `29bf2ee87365`, 40 labelled cases, fitted temperature **T = 1.0** (2026-11-25).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 1.000 | 0.030 | 0.011 |
| calibrated, 5-fold cross-validated | 1.000 | 0.030 | 0.011 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 1.000 | 40 |
| 0.6 | 0.97 | 1.000 | 39 |
| 0.7 | 0.97 | 1.000 | 39 |
| 0.8 | 0.97 | 1.000 | 39 |
| 0.85 | 0.97 | 1.000 | 39 |
| 0.9 | 0.93 | 1.000 | 37 |
| 0.95 | 0.90 | 1.000 | 36 |
| 0.99 | 0.45 | 1.000 | 18 |

![Reliability diagram](concierge.instruction_in_reply.svg)
