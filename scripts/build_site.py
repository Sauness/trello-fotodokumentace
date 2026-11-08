#!/usr/bin/env python3
"""Build the public photo gallery (GitHub Pages) from the exported archive.

    python scripts/build_site.py --archive archive --out _site

Produces:

  _site/index.html        gallery app (copied from scripts/site/index.html)
  _site/data.json         lists, cards, descriptions, comments, photo metadata
  _site/t/<id>.jpg        grid thumbnails
  _site/m/<id>.jpg        lightbox images
Originals are not copied; the page links to them on raw.githubusercontent.com.

Reads the card README.md files written by trello_export.py, so it needs no
Trello access. Requires Pillow.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageOps

Image.MAX_IMAGE_PIXELS = 200_000_000
HERE = Path(__file__).resolve().parent
RAW = "https://raw.githubusercontent.com/Sauness/trello-fotodokumentace/main/archive/"
THUMB = (420, 72)     # max edge px, jpeg quality
MEDIUM = (1600, 80)
YEAR = re.compile(r"(?<!\d)(18[5-9]\d|19\d\d|200\d)(?!\d)")


def section(md: str, name: str) -> str:
    m = re.search(rf"^## {name}[^\n]*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def clean(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]+\]\(https?://trello\.com[^)]*\)", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def comments(md: str) -> list[dict]:
    out = []
    for block in re.findall(r"^## Komentáře\n(.*?)(?=^## (?!Komentáře)|\Z)", md, re.S | re.M):
        for who, date, body in re.findall(
                r"\*\*(.+?)\*\* \((\d{4}-\d\d-\d\d)\):\n\n(.*?)(?=\n\*\*[^*\n]+\*\* \(\d{4}|\Z)", block, re.S):
            body = clean(body)
            if body:
                out.append({"who": who, "date": date, "text": body})
    return out


def years(*texts: str) -> list[int]:
    found = {int(y) for t in texts for y in YEAR.findall(t)}
    return sorted(y for y in found if y <= 2010)


def render(job: tuple[Path, Path, str]) -> dict | None:
    src, out, pid = job
    t, m = out / "t" / f"{pid}.jpg", out / "m" / f"{pid}.jpg"
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                bg = Image.new("RGB", im.size, "white")
                im = im.convert("RGBA")
                bg.paste(im, mask=im.split()[-1])
                im = bg
            w, h = im.size
            for path, (edge, q) in ((m, MEDIUM), (t, THUMB)):
                c = im.copy()
                c.thumbnail((edge, edge), Image.LANCZOS)
                c.convert("RGB").save(path, "JPEG", quality=q, optimize=True, progressive=True)
            return {"w": w, "h": h}
    except Exception as e:  # unreadable or not an image
        print(f"skip {src}: {e}")
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", default="archive")
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()
    archive, out = Path(args.archive), Path(args.out)

    board = (archive / "README.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| \[(.+?)\]\((\d\d-[^/]+)/README\.md\) \| \d+ \|", board, re.M)

    if out.exists():
        shutil.rmtree(out)
    (out / "t").mkdir(parents=True)
    (out / "m").mkdir()

    lists, jobs, photo_refs = [], [], {}
    for li, (lname, ldir) in enumerate(rows):
        lmd = (archive / ldir / "README.md").read_text(encoding="utf-8")
        cards = []
        for cname, cdir in re.findall(r"^- \[(.+?)\]\(([^/]+)/README\.md\)", lmd, re.M):
            cpath = archive / ldir / cdir
            md = (cpath / "README.md").read_text(encoding="utf-8")
            short = cdir.rsplit("-", 1)[-1]
            trello = re.search(r"\*\*Trello:\*\* <([^>]+)>", md)
            captions = {f: a for a, f in re.findall(r"!\[([^\]]*)\]\(([^)]+)\)", md)}
            desc = clean(section(md, "Popis"))
            card = {
                "id": short, "name": cname, "list": li,
                "trello": trello.group(1) if trello else "",
                "desc": desc, "comments": comments(md),
                "years": years(cname, desc), "photos": [],
            }
            files = sorted(p for p in cpath.iterdir()
                           if p.is_file() and p.name != "README.md" and p.stat().st_size > 0)
            for i, f in enumerate(files):
                pid = f"{short}-{i:03d}"
                photo = {"id": pid, "caption": captions.get(f.name, ""),
                         "src": RAW + urllib.parse.quote(f"{ldir}/{cdir}/{f.name}")}
                photo_refs[pid] = (card, photo)
                jobs.append((f, out, pid))
            cards.append(card)
        lists.append({"id": li, "name": lname, "cards": cards})

    with ThreadPoolExecutor(4) as ex:
        dims = list(ex.map(render, jobs))
    for (_, _, pid), d in zip(jobs, dims):
        if d:
            card, photo = photo_refs[pid]
            card["photos"].append(dict(photo, **d))

    data = {"board": "https://trello.com/b/2Fa6uRao",
            "repo": "https://github.com/Sauness/trello-fotodokumentace",
            "lists": lists}
    (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copy(HERE / "site" / "index.html", out / "index.html")
    (out / ".nojekyll").write_text("")

    n = sum(len(c["photos"]) for l in lists for c in l["cards"])
    print(f"{len(lists)} lists, {sum(len(l['cards']) for l in lists)} cards, {n} photos -> {out}")


if __name__ == "__main__":
    main()
