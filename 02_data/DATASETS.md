# Data Sources — Topic 02

| Source | Type | Access | Priority |
|---|---|---|---|
| Danish Maritime Authority — dma.dk/safety-at-sea/navigational-information/download-data | Bulk historical AIS, Baltic | FREE, GOVERNMENT | **PRIMARY** |
| Copernicus / Sentinel-1 SAR (ESA/EU) | Satellite SAR; standard dark-vessel technique | Free, government | Cross-check |
| Live AIS feed (community aggregators) | Real-time AIS | **VERIFY TERMS BEFORE USE** | T2 live demo |
| MacBook camera | Live EO | Owned | T2 live demo |
| Singapore Maritime Dataset / SeaShips | Labelled vessel imagery | Academic — verify licence | Detector eval |

## Build order
Build against the Danish Maritime Authority historical bulk data FIRST. It is free,
governmental, Baltic-relevant, downloadable immediately, and has no rate limit or
uptime risk. A live feed is a demo upgrade, not a foundation.

## Licence discipline
Check and record the licence for every source before use. Cite all of them in the
pitch. Government open data is not automatically unrestricted.
---

## Recorded licences

Full text, consequences and the open action are in **`02_data/INVENTORY.md` §2 and §8**.
Summary, so nobody has to go looking before a commit:

| Source | Licence | Status |
|---|---|---|
| DMA bulk AIS | Danish act no. 596 of 24 June 2005 (re-use of public sector information). No warranty; **no combining with other datasets to identify individuals** without Danish DPA authorisation; **redistribution not addressed**; DMA may sell AIS commercially. | **RECORDED. In use.** |
| Copernicus / Sentinel-1 | Free, ESA/EU | Not used |
| Live AIS aggregator (T2) | **NOT READ** | **Blocks T2.** Nothing connects until this row is filled |
| MacBook camera | Owned | Available |
| SeaShips / Singapore Maritime | Academic, unverified | Not used |

**"Free to download" is not "openly licensed".** DMA's silence on redistribution means
committing raw AIS rows to a public repo takes an unforced position on terms that were
never granted. Lane A's ruling: **only pseudonymised golden windows are committed**
(`INVENTORY.md` §6). If a pitch slide is going to claim an open licence, get it from
DMA in writing first.
