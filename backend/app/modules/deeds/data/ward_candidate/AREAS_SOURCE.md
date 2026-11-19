> **PO decision (2026-11-19):** use this dataset now under OpenCity's "Other (Public Domain)" label; credit it on `/terms` as "Ward boundaries: Greater Bengaluru Authority, via OpenCity (data.opencity.in)"; ask OpenCity/GBA to confirm provenance and terms in parallel (before-launch item). The display names still need PO review.

# Area names: ward dataset source (DATA1, Q28)

Status: **candidate, waiting for PO review.** Nothing here is seeded yet. The licence verdict and the name table both need PO sign-off before DB4 copies `bangalore_areas.geojson` over the placeholder at `data/bangalore_areas.geojson`.

## Dataset

| Field | Value |
| --- | --- |
| Title | GBA Wards Delimitation 2025: "GBA Final Wards Map With Population - Dec 2025" (KML) |
| What it is | Final ward boundaries of the five Greater Bengaluru Authority (GBA) city corporations (Central, East, North, South, West). The State Government notified them on 19 Nov 2025. Ward names include the renaming notifications of 1 Dec 2025. |
| Original publisher | Greater Bengaluru Authority / Government of Karnataka. On OpenCity, the companion "Final Wards Map - 369 Wards" resource has **Source: GBA** and **Credit: Vivek Mathew**. |
| Republisher | OpenCity (Oorvani Foundation), organisation "Greater Bengaluru Authority (GBA)" on data.opencity.in |
| Dataset page | https://data.opencity.in/dataset/gba-wards-delimitation-2025 |
| Resource page | https://data.opencity.in/dataset/gba-wards-delimitation-2025/resource/9013d656-8051-4e2d-9648-46efd0d86d3d |
| Download URL | https://data.opencity.in/dataset/863209cb-4ced-4f51-b5c5-156939c50922/resource/9013d656-8051-4e2d-9648-46efd0d86d3d/download/gba-369-wards-december-2025.kml |
| Publication date | Notification 19 Nov 2025; ward renaming 1 Dec 2025; resource created and last updated on OpenCity 8 Dec 2025 |
| Download date | 2026-11-19 (see "How it was obtained") |
| Licence | OpenCity label: **"Other (Public Domain)"**, license id "Public Domain", on both the dataset page and the resource pages |
| Ward count | **369**: West 112, North 72, South 72, Central 63, East 50 |
| Format | KML 2.2, WGS84 lon/lat. 365 single-polygon wards, 3 two-part wards, 1 three-part ward, 1 hole. |
| SHA-256 (KML used) | `b61575248b0492a65d7fb06773c11a21b0e46539f4dff46f1b3021894c146c28` |

Corroboration:
- Final notification on 19 Nov 2025 with 369 wards (one more than the 368 in the draft, added to West): Deccan Herald, https://www.deccanherald.com/india/karnataka/bengaluru/greater-bengaluru-areas-five-corporations-to-have-369-wards-3803924, and South First, https://thesouthfirst.com/karnataka/karnataka-approves-369-wards-under-gba-raising-hopes-for-long-delayed-civic-polls
- 13 wards renamed or renumbered about three weeks later, and "Akash" ward dropped: Deccan Herald, https://www.deccanherald.com/amp/story/india%2Fkarnataka%2Fbengaluru%2Fkarnataka-govt-drops-akash-ward-after-objections-renames-13-areas-3817785. The renaming PDFs for each corporation are on the same OpenCity dataset page.
- Independent republication of the same 369 wards: bharatlas, https://bharatlas.com/view/wards_bengaluru_gba. It labels the data CC0-1.0, gives the source as "OpenCity" and dates its snapshot 2026-05-26. We did not use its copy.
- No newer delimitation was found as of 2026-11-19. The 2026 coverage (reservations, election timetable) is still about these 369 wards.

## Licence verdict and attribution

**Verdict: usable in the app *if the PO accepts OpenCity's public-domain label*. This is not a formal government open-data licence.**

- OpenCity publishes the dataset and every resource as "Other (Public Domain)", with GBA as the source. Under that label we may redistribute the data inside the app, and attribution is not legally required.
- We found **no licence statement from GBA or the Karnataka government itself**: no GODL-India and no data.gov.in or KGIS listing for the 2025 wards. The public-domain status therefore rests on OpenCity's label. Under Indian copyright law, government works are normally government copyright (Copyright Act s.17(dd)). However, s.52(1)(q) allows reproducing matter published in the Official Gazette, which covers the delimitation notification itself. The KML is a digitised map, not the gazette text. This is not legal advice. If the PO wants certainty, ask OpenCity (or GBA) where the KML came from and on what terms.
- The older openly licensed BBMP data (198 wards, 2015 delimitation; and the 243-ward 2022/2023 delimitation) is out of date: BBMP was dissolved and the corporations' wards were redrawn in 2025. Do not use it as a fallback without a PO decision.

**Attribution (recommended even though not required; for `/terms`):**

> Ward boundaries: Greater Bengaluru Authority (GBA), final ward delimitation notified 19 November 2025 (ward names as amended 1 December 2025), obtained via OpenCity (data.opencity.in), Public Domain. Area names shown in the app are simplified from the official ward names.

`areas.source` value (117 characters, fits varchar(120)):
`Greater Bengaluru wards, 369 (notified 2025-11-19, names 2025-12-01), GBA via OpenCity.in, 2025-12-08 (Public Domain)`

## How it was obtained

- data.opencity.in answers scripted requests (curl, the CKAN API and the fetch tool) with a Cloudflare challenge (HTTP 403). bharatlas's catalogue records the same problem.
- The KML was therefore taken from the Internet Archive's capture of the exact OpenCity download URL, dated 2026-08-08: `https://web.archive.org/web/20260808183921id_/<download URL above>`. The `id_` form returns the original bytes.
- The resource metadata (licence, dates, size 3.8 MiB) was read from the archive's capture of the resource page, dated 2026-08-12.
- **Before seeding, someone should download the KML in a normal browser from the OpenCity URL and check that its SHA-256 matches the value above.** If it differs, OpenCity has updated the file: rebuild from the new file.
- The 19 Nov 2025 KML (resource `dbe57ae6-…`, SHA-256 `83ef1574…833474f`) was also downloaded for comparison:
  - 356 of 369 geometries are identical;
  - 13 wards were redrawn or relabelled in the December file. Examples: Akash → Aerocity; Kumaraswamy Layout → Yarab Nagar; Devasandra → Rajarajeshwari Temple Ward; Yelahanka Old Town and Raja Kempegowda swapped numbers.

## Conversion steps

These steps used pure Python (stdlib `xml.etree`, run with `python -I`), with no GIS libraries.
1. Read the 369 `Placemark`s and their `SimpleData` fields `Corporation`, `ward_id` and `ward_name`. Every other field (population, assembly constituency, Kannada names, RO/ARO codes) is **dropped**.
2. `ward_code` = `GBA-` + corporation initial (C/E/N/S/W) + 3-digit ward number, e.g. `GBA-W025`. Corporation + ward number is unique in this release. The ward code is for audit only and is never shown.
3. `official_name` = `"<Corporation> <ward no> - <official ward name>"`, kept for audit only.
4. Geometry: each KML `Polygon` (outer ring + inner rings) becomes one part of a GeoJSON `MultiPolygon`.
   - Coordinates are rounded to 6 decimal places (about 0.1 m).
   - Consecutive duplicate points are removed and rings are closed.
   - Rings are oriented per RFC 7946 (outer counter-clockwise, holes clockwise).
   - CRS is EPSG:4326 lon/lat; the source KML is already WGS84, so no reprojection was needed.
   - Bounding box: 77.4599–77.7841 E, 12.8335–13.1426 N.
5. `name` = the proposed display name from `names.csv`. `source` = the string above.
6. Output: `bangalore_areas.geojson` (FeatureCollection, 369 features, sorted by `ward_code`, about 2 MB) and `names.csv`.

## Name clean-up (Q28)

`names.csv` has columns `ward_code, official_name, proposed_display_name, note`. **369 wards → 321 distinct display names.**

Rules applied:
- no ward numbers or codes;
- no road, street, building or landmark names;
- title case, with acronyms such as HSR, BTM, JP, KR, RT and NAL kept;
- at most 40 characters (checked);
- no digits (checked).

Mechanical fixes:
- dropped a trailing "Ward";
- dropped directional suffixes that only split one area, e.g. "Kormangala East/West" → "Koramangala";
- "Nagara" → "Nagar";
- dotted initials collapsed, e.g. "K.R Pura" → "KR Puram", "J.P Nagar" → "JP Nagar";
- familiar English spellings, e.g. "Domluru" → "Domlur", "Bellanduru" → "Bellandur", "Chickpete" → "Chickpet", "Yeshwanthpura" → "Yeshwanthpur".

**63 rows have a note starting `U:`. These are names I am unsure about and the reviewer should check them first.** Most are wards that the 2025 delimitation named after a person, temple, landmark, company or institution, e.g.:
- "Dr Rajkumar Ward"
- "Dharmaraya Swamy Temple Ward"
- "BHEL Ward"
- "Bandi Reddy Circle Ward"
- "K.R. Market"
- "J.P. Park"
- "Ashoka Pillar"

For these I proposed the familiar neighbourhood the ward lies in. I chose it from the ward's centroid and its neighbouring wards, computed locally with no external geocoder, plus local knowledge. Several are therefore shared, coarser names:
- Rajajinagar ×6
- Banashankari ×5
- Koramangala ×4
- Vijayanagar ×3
- Yelahanka ×3

Two pairs of similar official names are **different places** and were deliberately not merged:
- "Marenahalli South" (JP Nagar side) and "Marenahalli West" (Vijayanagar side);
- "Hoysala Nagara Central" (Indiranagar side) and "Hoysala Nagara East" (Ramamurthy Nagar side).

## Open concerns for the PO

1. **Licence:** accept OpenCity's "Public Domain" label (recommended, with the attribution above on `/terms`), or first ask OpenCity or GBA to confirm the source and terms.
2. **Integrity:** the file came from an Internet Archive capture, because OpenCity blocks scripted downloads. Check the SHA-256 with a browser download before seeding.
3. **Geometry validity was not checked** with a GIS library (none installed). DB4 should check `ST_IsValid` and `ST_MakeValid` when seeding. It should also report any overlaps between wards: the lookup takes the lowest `id` on a shared border, which already handles slivers.
4. **Accuracy:** OpenCity's KML is a digitisation of the notified wards, not the gazette text itself. Boundaries may differ slightly from the legal description. That is acceptable for coarse area names.
5. **Currency:** re-check before launch whether any later renaming or corrigendum has been issued. The Central Corporation had a corrigendum to the 19 Nov notification, which the Dec 2025 file should include.
6. **Name review:** the 63 `U:` rows need a person who knows Bangalore. Also check that rows without a note read naturally, e.g. "K Narayanapura", "A Narayanapura", "SK Garden", "JJR Nagar".
