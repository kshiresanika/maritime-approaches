# Judging Criteria — Topic 02

| # | Criterion | Owner module | Status |
|---|---|---|---|
| 1 | Working pipeline: EO frame + AIS in → **MATCH / dark-vessel / spoofing** verdict out, with bearing and rough range | eo_detector + ais_ingest + association + verdict | ☐ |
| 2 | **Prioritization step** ranking which contact is worth a scarce asset — not a flat alert list | prioritizer.py | ☐ |
| 3 | Explicit handling of **AIS spoofing**, not only vessels that switch AIS off | consistency.py | ☐ |
| 4 | **Evidence-grade output** — identity, track, timestamp, short rationale — plus honest limits where it defers to a human | evidence.py + report.py | ☐ |

## Where to weight effort
Criterion 2 is where most teams will fail. A detector is easy. A defensible
recommendation — "send the boat here, because X, confidence Y" — is the actual problem
in the brief.

Criterion 3 is the discriminator. AIS-off is easy (a ship with no transponder).
AIS-spoofing means catching the mismatch between CLAIMED and OBSERVED. Build for the
harder case and demonstrate it explicitly.

## The architectural guardrail judges will notice
The deterministic pipeline produces the VERDICT and the CONFIDENCE.
The LLM produces the RATIONALE only.
Keep this separation visible in the architecture diagram.