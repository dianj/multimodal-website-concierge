# Calibration: concierge.instruction_in_reply

Model `qwen3.8-27b`, prompt `29bf2ee87365`, 32 labelled cases, fitted temperature **T = 1.0** (2026-11-24).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 1.000 | 0.034 | 0.014 |
| calibrated, 5-fold cross-validated | 1.000 | 0.034 | 0.014 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 1.000 | 32 |
| 0.6 | 0.97 | 1.000 | 31 |
| 0.7 | 0.97 | 1.000 | 31 |
| 0.8 | 0.97 | 1.000 | 31 |
| 0.85 | 0.97 | 1.000 | 31 |
| 0.9 | 0.91 | 1.000 | 29 |
| 0.95 | 0.88 | 1.000 | 28 |
| 0.99 | 0.47 | 1.000 | 15 |

![Reliability diagram](concierge.instruction_in_reply.svg)
