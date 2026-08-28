# Hard Constraints

## Ethical / legal — non-negotiable
1. Decision support, never automated enforcement. A human decides.
2. NEVER accuse a real named vessel or operator in public-facing output. Live AIS
   names real ships and real companies. Anonymise, or use clearly-labelled synthetic
   identities. A demo that labels a real named tanker a saboteur is defamatory.
3. Every verdict carries a calibrated confidence and an explicit defer-to-human
   threshold.
4. The system never claims to determine INTENT. It flags inconsistency and anomaly.
5. Privacy: if a camera faces a public waterway, vessels are the subject, not persons.
   Keep people out of frame; do not store identifiable imagery of individuals.

## Data
6. Check and cite the licence on every dataset and feed. Community AIS aggregators
   have varying terms; some prohibit redistribution or commercial demonstration.
7. Government and institutional sources first: Danish Maritime Authority, Copernicus,
   EMSA, BSH.

## Commercial / IP
8. Read the event's IP and open-sourcing terms before the first commit.
9. No disclosure of the mothership/child-drone architecture or FARU.
10. Pre-incorporation. Nothing that triggers company registration.

## Technical
11. macOS only — and sufficient. No VM, no hardware, no flight dependency.
12. 32K context ceiling on Featherless: chunk AIS by time window or per-contact.
    Never plan a single call over a whole corpus.
13. LLM writes rationale, never verdict.