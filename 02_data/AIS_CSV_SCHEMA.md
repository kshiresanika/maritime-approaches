# Danish Maritime Authority AIS — CSV schema

Source: `!_README_information_CSV_files.txt` in the DMA S3 bucket, retrieved 2026-08-27.
Bucket (path-style, HTTPS): `https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk/`

## Columns

| # | Column | Notes |
|---|---|---|
| 1 | Timestamp | From the AIS basestation. Format `31/12/2015 23:59:59` — **day/month/year** |
| 2 | Type of mobile | Class A AIS vessel, Class B AIS vessel, etc. |
| 3 | MMSI | Vessel MMSI |
| 4 | Latitude | Example given as `57,8794` |
| 5 | Longitude | Example given as `17,9125` |
| 6 | Navigational status | e.g. "Engaged in fishing", "Under way using engine" |
| 7 | ROT | Rate of turn, if available |
| 8 | SOG | Speed over ground, if available |
| 9 | COG | Course over ground, if available |
| 10 | Heading | If available |
| 11 | IMO | |
| 12 | Callsign | |
| 13 | Name | Vessel name |
| 14 | Ship type | AIS ship type |
| 15 | Cargo type | |
| 16 | Width | |
| 17 | Length | |
| 18 | Type of position fixing device | |
| 19 | Draught | |
| 20 | Destination | |
| 21 | ETA | |
| 22 | Data source type | e.g. AIS |
| 23 | Size A | GPS to bow |
| 24 | Size B | GPS to stern |
| 25 | Size C | GPS to starboard |
| 26 | Size D | GPS to port |

## Why this schema matters for the judging criteria

Criterion 3 is claimed-vs-observed. This file supplies the **CLAIMED** side directly:

| AIS claim (column) | Compared against camera observation |
|---|---|
| Ship type (14) | Apparent silhouette class from the VLM |
| Length (17), Width (16), Size A–D (23–26) | Apparent size vs. estimated range |
| Heading (10), COG (9) | Observed heading across frames |
| SOG (8) | Observed motion across frames |
| Latitude/Longitude (4,5) | Bearing + estimated range from the EO fix |

Columns 23–26 are more useful than they look: A+B is the vessel's actual length about the
GPS antenna and C+D its beam, so they cross-check the declared Length/Width from a second
field. A vessel spoofing its dimensions has to keep two fields consistent, not one.

## Two parsing landmines — verify on the first read, do not assume

1. **Decimal separator.** The README writes coordinates as `57,8794` (Danish decimal
   comma). If the file is also comma-DELIMITED, that is ambiguous and the real file must
   settle it. Check the first data line before configuring any reader. Getting this wrong
   silently shifts every position.
2. **Date order.** `31/12/2015 23:59:59` is day/month/year. Pandas defaults to
   month-first and will silently mis-parse every date where day <= 12 — roughly the first
   twelve days of every month — while raising no error. Pass an explicit format string.

## Licence
Danish act no. 596 of 24 June 2005 on the further use of public sector information. DMA
guarantees nothing and accepts no liability. AIS may NOT be combined with other datasets
to identify individuals without Danish Data Protection Agency authorisation.
Redistribution/attribution are not addressed and DMA may sell AIS commercially — "free to
download" is not "openly licensed".
