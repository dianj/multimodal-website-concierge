# Calibration: concierge.instruction_in_reply

Model `gemma-4-26b-a4b-it`, prompt `29bf2ee87365`, 40 labelled cases, fitted temperature **T = 2.8145** (2026-11-25).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 0.975 | 0.025 | 0.050 |
| calibrated, 5-fold cross-validated | 0.975 | 0.032 | 0.054 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 0.975 | 40 |
| 0.6 | 1.00 | 0.975 | 40 |
| 0.7 | 1.00 | 0.975 | 40 |
| 0.8 | 1.00 | 0.975 | 40 |
| 0.85 | 0.97 | 0.974 | 39 |
| 0.9 | 0.90 | 0.972 | 36 |
| 0.95 | 0.88 | 0.971 | 35 |
| 0.99 | 0.75 | 0.967 | 30 |

![Reliability diagram](concierge.instruction_in_reply.svg)
