"""Convert the GBA 369-ward KML to GeoJSON + names.csv.

Usage: python -I build.py <kml> <out_dir>
"""

import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

NS = {"k": "http://www.opengis.net/kml/2.2"}
SOURCE = (
    "Greater Bengaluru wards, 369 (notified 2025-11-19, names 2025-12-01), "
    "GBA via OpenCity.in, 2025-12-08 (Public Domain)"
)
CORP = {"Central": "C", "East": "E", "North": "N", "South": "S", "West": "W"}

# (corp, ward_id) -> (display name, note). Notes starting "U:" = unsure, needs PO review.
OVR = {
    ("Central", 7): ("Halasuru", "U: person name (K Kamaraj); ward lies by Halasuru/Ulsoor"),
    ("Central", 9): (
        "Indiranagar",
        "U: 'Hoysala Nagara Central' is not East 5 Hoysala Nagar; centroid is HAL 2nd Stage / Indiranagar north",
    ),
    ("Central", 24): ("Agaram", "U: kept official name; confirm it is a familiar locality"),
    ("Central", 32): (
        "Sudhama Nagar",
        "U: landmark name (Silver Jubilee Park); nearest familiar locality guessed",
    ),
    ("Central", 33): ("Chickpet", "U: landmark name (Dharmaraya Swamy Temple); old-city area"),
    ("Central", 34): ("VV Puram", "U: person name (D.V. Gundappa); next to VV Puram"),
    ("Central", 37): (
        "Lakkasandra",
        "U: company/landmark name (BHEL); nearest familiar locality guessed",
    ),
    ("Central", 39): (
        "Wilson Garden",
        "U: confirm whether 'Venkat Reddy Nagar' is itself a familiar locality",
    ),
    ("Central", 40): ("Jayanagar", "U: landmark name (Ashoka Pillar, Jayanagar 1st Block)"),
    ("Central", 43): ("Chamarajpet", "U: person name (Devaraj Urs); next to Chamarajpet"),
    ("Central", 45): ("Kalasipalya", "U: landmark name (K.R. Market)"),
    ("Central", 47): ("Binnypet", "U: person name (IPD Salappa); next to Binnypet"),
    ("Central", 61): ("Seshadripuram", "U: temple name (Dattatreya); next to Seshadripuram"),
    ("Central", 62): (
        "Srirampura",
        "U: 'Swatantra Palya' believed to be the renamed Srirampura area; confirm",
    ),
    ("East", 5): ("Hoysala Nagar", "directional suffix 'East' dropped"),
    ("East", 13): (
        "Devasandra",
        "U: temple name (Rajarajeshwari Temple); this ward was 'Devasandra' in the 19 Nov 2025 map",
    ),
    ("South", 3): (
        "Yarab Nagar",
        "U: this ward was 'Kumaraswamy Layout' in the 19 Nov 2025 map; renamed Dec 2025",
    ),
    ("East", 14): ("KR Puram", "spelling normalised (K.R Pura)"),
    ("East", 19): ("Dooravani Nagar", "U: person name (K.S. Nissar Ahmed)"),
    ("East", 23): ("Vignan Nagar", "U: generic name (Sangama)"),
    ("East", 33): ("Whitefield", "U: person name (S.M. Krishna)"),
    ("East", 36): ("Mahadevapura", "U: generic name (Bharath Aikya)"),
    ("East", 42): ("Brookefield", "U: person name (Priyadarshini)"),
    ("East", 49): ("Bellandur", "U: generic name (Shivanasamudra)"),
    ("North", 1): ("Yelahanka", "U: person name (Raja Kempegowda); Yelahanka New Town"),
    ("North", 2): ("Yelahanka", "U: 'Aerocity' is a ward label, not a familiar locality"),
    ("North", 3): ("Yelahanka", "U: temple/deity name (Chowdeshwari); Yelahanka old town"),
    ("North", 4): ("Judicial Layout", "U: English name of Nyayanga Badavane"),
    ("North", 42): ("DJ Halli", "U: person name (Aruna Asif Ali); D.J. Halli / Tannery Road area"),
    ("North", 61): ("Jalahalli", "U: company name (HMT)"),
    ("North", 63): ("Yeshwanthpur", "U: landmark name (J.P. Park)"),
    ("North", 71): ("Dasarahalli", "U: temple name (Nele Maheshwaramma Temple)"),
    ("South", 4): ("Banashankari", "landmark (temple) dropped"),
    ("South", 5): ("Kumaraswamy Layout", "U: deity/temple name (Kane Muneshwara)"),
    ("South", 10): (
        "Jayanagar",
        "U: commemorative name (Abdul Kalam Nagar); confirm whether it is a known locality",
    ),
    ("South", 11): ("Jayanagar", "directional suffix dropped"),
    ("South", 13): (
        "JP Nagar",
        "U: 'Marenahalli South' is a different place from West 75 Marenahalli",
    ),
    ("South", 18): ("BTM Layout", "U: person name (Vishwamanava Kuvempu)"),
    ("South", 24): ("Adugodi", "'A' prefix dropped"),
    ("South", 25): ("Koramangala", "U: 'National Games Village' is a housing complex"),
    ("South", 27): ("Koramangala", "U: deity name (Sri Lakshmi Devi)"),
    ("South", 28): ("Koramangala", "East/West halves merged; spelling fixed"),
    ("South", 29): ("Koramangala", "East/West halves merged; spelling fixed"),
    ("South", 54): ("JP Nagar", "U: person name (Kengal Hanumanthaiah)"),
    ("South", 59): ("Vijaya Bank Layout", "U: locality named after a bank; kept"),
    ("West", 13): ("Herohalli", "U: person name (Nadaprabhu Kempegowda)"),
    ("West", 18): ("Kengeri", "U: person name (Kengal Hanumanthaiah)"),
    ("West", 20): ("Kengeri", "'Kote ward' dropped"),
    ("West", 24): ("Jnanabharathi", "U: named after the university campus, also the locality name"),
    ("West", 31): ("Laggere", "U: generic name (Freedom Fighter)"),
    ("West", 36): ("Yeshwanthpur", "U: person name (Nalwadi Krishnaraja Wadiyar)"),
    ("West", 37): ("Nandini Layout", "U: person name (Dr. Puneeth Rajkumar)"),
    ("West", 42): ("Mahalakshmi Layout", "U: person name (Raja Mayura Varma)"),
    ("West", 44): ("Mahalakshmi Layout", "U: 'Shankar Mutt' is a building/institution"),
    ("West", 57): ("Rajajinagar", "U: person name (Kuvempu)"),
    ("West", 59): ("Rajajinagar", "U: landmark name (Bandi Reddy Circle)"),
    ("West", 61): ("Rajajinagar", "U: person name (Da.Ra. Bendre)"),
    ("West", 62): ("Rajajinagar", "U: temple name (Rama Mandira)"),
    ("West", 70): ("Rajajinagar", "U: person name (Dr Rajkumar)"),
    ("West", 73): ("Vijayanagar", "U: person name (Dr. Vishnuvardhan)"),
    ("West", 75): ("Marenahalli", "directional suffix dropped; different place from South 13"),
    ("West", 77): ("Vijayanagar", "U: temple name (Maruthi Mandira)"),
    ("West", 85): ("Hosahalli", "U: religious institution name (Adi Chunchanagiri)"),
    ("West", 88): ("KP Agrahara", "U: person name (Sangolli Rayanna)"),
    ("West", 90): ("Vijayanagar", "U: person name (Krishnadevaraya)"),
    ("West", 91): ("Bapuji Nagar", "U: temple name (Gali Anjaneya Temple)"),
    ("West", 95): ("Banashankari", "U: person name (Swamy Vivekananda)"),
    ("West", 99): ("TR Shamanna Nagar", "U: confirm familiar locality name"),
    ("West", 101): ("Hanumanthanagar", "U: lake name (Kempambudhi)"),
    ("West", 106): ("Banashankari", "U: temple name (Devagiri Temple)"),
    ("West", 107): ("Banashankari", "U: temple name (Dharmagiri)"),
    ("West", 108): ("Banashankari", "U: temple name (Ganesh Mandira)"),
}

# Spelling normalisation to the familiar English form.
FIX = {
    "G.M Palya": "GM Palya",
    "V.V Puram": "VV Puram",
    "L.B Shastri Nagar": "LB Shastri Nagar",
    "N.A.L Layout": "NAL Layout",
    "J.P Nagar": "JP Nagar",
    "N.S Palya": "NS Palya",
    "S.G Palya": "SG Palya",
    "K.G.Halli": "KG Halli",
    "R.T Nagar": "RT Nagar",
    "S.K Garden": "SK Garden",
    "K.P Agrahara": "KP Agrahara",
    "N.R Colony": "NR Colony",
    "Yarab nagar": "Yarab Nagar",
    "Domluru": "Domlur",
    "Bellanduru": "Bellandur",
    "Hagaduru": "Hagadur",
    "Yediyuru": "Yediyur",
    "Munnenkolalu": "Munnekollal",
    "Chickpete": "Chickpet",
    "Cottonpete": "Cottonpet",
    "Binnypete": "Binnypet",
    "Yeshwanthpura": "Yeshwanthpur",
    "Vignananagara": "Vignan Nagar",
    "Dooravaninagar": "Dooravani Nagar",
    "Nayanda Halli": "Nayandahalli",
    "Sane Guruvana Halli": "Saneguruvanahalli",
    "Dodda Nekkundi": "Doddanekkundi",
    "Kasturbha Nagar": "Kasturba Nagar",
    "Hoysala Nagara Central": "Hoysala Nagar",
}
ACRONYM = re.compile(r"^[A-Z]{2,4}$")


def clean(name):
    n = FIX.get(name, name).strip()
    n = re.sub(r"\s+ward$", "", n, flags=re.I)
    n = re.sub(r"nagara$", "nagar", n)
    n = re.sub(r" Nagara$", " Nagar", n)
    return " ".join(w if ACRONYM.match(w) else w[:1].upper() + w[1:] for w in n.split())


def ring(txt):
    pts = []
    for t in txt.split():
        x, y = (round(float(v), 6) for v in t.split(",")[:2])
        if not pts or pts[-1] != [x, y]:
            pts.append([x, y])
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def area(r):
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:])) / 2


def orient(r, ccw):
    return r if (area(r) > 0) == ccw else r[::-1]


def main(kml, out):
    root = ET.parse(kml).getroot()
    feats, rows, problems = [], [], []
    for pm in root.findall(".//k:Placemark", NS):
        d = {sd.get("name"): (sd.text or "").strip() for sd in pm.findall(".//k:SimpleData", NS)}
        corp, wid, off = d["Corporation"], int(d["ward_id"]), d["ward_name"]
        code = f"GBA-{CORP[corp]}{wid:03d}"
        polys = []
        for p in pm.findall(".//k:Polygon", NS):
            outer = ring(p.find("k:outerBoundaryIs//k:coordinates", NS).text)
            inners = [ring(c.text) for c in p.findall("k:innerBoundaryIs//k:coordinates", NS)]
            for r in [outer] + inners:
                if len(r) < 4:
                    problems.append(f"{code}: ring with {len(r)} points")
            polys.append([orient(outer, True)] + [orient(r, False) for r in inners])
        name, note = OVR.get((corp, wid), (None, ""))
        if name is None:
            name = clean(off)
            if name != off:
                note = "spelling/format normalised"
        assert len(name) <= 40 and not re.search(r"\d", name), (code, name)
        official = f"{corp} {wid} - {off}"
        feats.append(
            {
                "type": "Feature",
                "properties": {
                    "ward_code": code,
                    "official_name": official,
                    "name": name,
                    "source": SOURCE,
                },
                "geometry": {"type": "MultiPolygon", "coordinates": polys},
            }
        )
        rows.append((code, official, name, note))
    assert len(SOURCE) <= 120, len(SOURCE)
    feats.sort(key=lambda f: f["properties"]["ward_code"])
    rows.sort()
    os.makedirs(out, exist_ok=True)
    with open(
        os.path.join(out, "bangalore_areas.geojson"), "w", encoding="utf-8", newline="\n"
    ) as f:
        json.dump(
            {"type": "FeatureCollection", "name": "bangalore_areas", "features": feats},
            f,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        f.write("\n")
    with open(os.path.join(out, "names.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["ward_code", "official_name", "proposed_display_name", "note"])
        w.writerows(rows)
    print(
        "features",
        len(feats),
        "unsure",
        sum(1 for r in rows if r[3].startswith("U:")),
        "distinct names",
        len({r[2] for r in rows}),
        "source len",
        len(SOURCE),
        "problems",
        problems,
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
