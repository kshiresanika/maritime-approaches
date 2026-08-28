# AIS Spoofing — What We Are Actually Detecting

This file exists so the consistency check is built on a threat model rather than
guesswork, and so the pitch can explain WHAT KIND of deception is caught.

## The three cases, in ascending difficulty

**1. AIS off ("dark vessel")**
The transponder is switched off. Detection: an EO contact with no corresponding AIS
track in the same place at the same time. Comparatively easy — this is the case most
teams will build. It is criterion 1, not criterion 3.

**2. AIS identity spoofing**
The transponder broadcasts a false MMSI, vessel name, or vessel type. Detection: the
AIS-CLAIMED attributes do not match the OBSERVED attributes. If AIS says "fishing
vessel, 40 m" and the camera shows a tanker silhouette 250 m long, that is the
mismatch. **This is criterion 3 and where the marks are.**

**3. AIS position spoofing**
The transponder broadcasts a false position while the vessel is elsewhere. Detection:
an AIS track with no EO contact where it claims to be, or kinematically implausible
tracks — impossible speeds, teleports, straight lines through land. Cheap to check and
worth including as a kinematic plausibility filter.

## The observable attributes to compare

| AIS claims | Camera observes | Mismatch signal |
|---|---|---|
| Vessel type (cargo/tanker/fishing/passenger) | Apparent silhouette class | Class mismatch |
| Length / dimensions | Apparent size vs. estimated range | Size mismatch |
| Course over ground | Observed heading | Heading mismatch |
| Speed over ground | Observed motion across frames | Speed mismatch |
| Position | Bearing + estimated range | Position mismatch |

## Where a VLM helps
A vision-language model can describe an observed vessel's apparent class and relative
size from a frame. That description feeds the class-mismatch check directly. This is
a legitimate and well-scoped use — it produces an OBSERVATION, which the deterministic
pipeline then compares against the AIS CLAIM. The model is not deciding anything.

## Anomalous behaviour — beyond identity
- Loitering over a known cable route or near critical infrastructure
- Speed profile inconsistent with declared vessel type or voyage
- Repeated AIS gaps in the same geographic area
- Deviation from an expected route without a plausible reason

Loitering over a cable route is the scenario named explicitly in the brief. Make sure
the prioritizer weights proximity to critical infrastructure heavily.