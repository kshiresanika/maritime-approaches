# Project Context
EDTH Hamburg Hackathon 2026 — Topic 02, Guarding the Maritime Approaches. A 48-hour
build of a maritime situational-awareness tool that fuses an electro-optical feed with
the live AIS picture to flag dark vessels, AIS spoofing, and anomalous behaviour near
critical seabed infrastructure — producing evidence-grade output that tells an operator
which vessel, on what track, with what confidence.

Event: 28–30 Aug 2026, Hamburg. Hacking starts 19:00 Friday.

# About Me
Amol Vivek Kulkarni. Systems architect,
not an ML researcher. Attending solo, recruiting on site. I value precision, honest
numbers, and clear causal reasoning.

# Why This Topic Fits My Constraints
Mac only, no hardware, no purchases — and this topic needs none of them. All data is
open and mostly government-sourced. It also sits in the Baltic/maritime
civilian track (EMSA, BSH, offshore wind, seabed cables).

# Where The Effort Goes
- Criterion 2 (PRIORITIZATION) is where most teams fail. Building a detector is easy;
  saying "send the boat HERE, and here's why, and here's how sure I am" is the real
  problem. Weight effort accordingly.
- Criterion 3 (SPOOFING) is the discriminator. A vessel with AIS switched off is easy.
  A vessel BROADCASTING A FALSE IDENTITY means catching a mismatch between what AIS
  claims and what the camera observes. Build for the harder case.

# Hard Rules
- Decision support, NEVER automated enforcement. A human decides.
- NEVER accuse a real named vessel or operator in anything public-facing. Anonymise
  or use clearly-labelled synthetic identities. This is non-negotiable.
- The LLM writes the RATIONALE, never the VERDICT. Verdicts come from the
  deterministic pipeline with calibrated confidence.
- Every verdict carries a confidence and an explicit defer-to-human threshold.
- Check and cite the licence on every dataset and feed before use.
- If a live camera faces a public waterway: vessels are the subject, not persons.
- Do not commit proprietary code before IP terms are confirmed.
- Never disclose prior unpublished architecture work. Clean-room only.
- Always add explanatory notes in code explaining each step and why.
- Be detailed on cause and effect — show the inference chain: observed vs. claimed,
  why the mismatch matters, confidence, and what would change the answer.
- Prefer government sources (Danish Maritime Authority, Copernicus, EMSA, BSH).
- Show the proposed plan before executing any multi-stage task.
- Save finished deliverables to 05_pitch/ or 04_demo/.

# Demo Target
T1 (guaranteed): full pipeline on recorded EO + historical Danish AIS, with
prioritization and evidence output. BUILD THIS COMPLETELY FIRST.
T2 (differentiator): the same pipeline running live on the Elbe via MacBook camera +
live AIS. Only attempt after T1 works. Keep the recorded path as an instant,
rehearsed fallback.
Verify line of sight to water at the venue on FRIDAY, not Sunday.

# Project Structure
00_brief/     - challenge statement, judging criteria, constraints
01_research/  - prior art, AIS spoofing methods, EMSA/regulatory context
02_data/      - AIS data, EO datasets, download scripts
03_src/       - code
04_demo/      - demo script, recordings, fallback plan
05_pitch/     - slides, one-pager, TRL declaration
06_team/      - role map, recruiting one-pager
99_scratch/   - working notes

# Standing Rules (all chats in this project)
- LIBRARY-FIRST. Check 03_src/LIBRARIES.md before writing any algorithm. Never
  hand-roll NMEA/AIS parsing, geodesy, trajectory resampling, or spatial joins.
  If no library fits, say so and justify in one line before coding.
- NEVER INVENT AN API. Unsure of a signature? Read the docs or run
  `python -c "import x; help(x.y)"` first. State uncertainty rather than guessing.
- NO SPECULATIVE CODE. No placeholders, no "TODO: implement later", no unused
  abstractions, no config framework for one value.
- FILE OWNERSHIP. Only write files your lane owns. To change another lane's file,
  append a request to 99_scratch/requests.md.
- CONTRACTS. All cross-module types come from 03_src/contracts.py. Never pass a bare
  dict across a module boundary.
- LEDGER. End every session with one line in STATUS.md.
- COMMENT THE WHY, and for this project the INFERENCE CHAIN: what was observed, what
  was claimed, why the mismatch matters, confidence, what would change the answer.
- MEASURED NUMBERS ONLY. Never report a benchmark you did not run.
- THE LLM WRITES THE RATIONALE, NEVER THE VERDICT. Verdicts come from the
  deterministic pipeline with a calibrated confidence.
- ANONYMISE REAL VESSELS in every public-facing output. Never label a real named
  ship or operator as suspicious. Use synthetic identities, clearly marked.
- EVERY VERDICT carries a confidence and an explicit defer-to-human threshold.
- CITE THE LICENCE of every dataset and feed before using it.
- HANDOFF. End every lane session with 99_scratch/handoff_<lane>.md.