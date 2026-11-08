#!/usr/bin/env python3
"""Export a Trello board (cards, descriptions, checklists, comments and
attachments) into a browsable Markdown archive.

Two input modes:

  Live (default) – reads the board via the Trello REST API and downloads
  uploaded attachments. Needs TRELLO_API_KEY and TRELLO_TOKEN in the env.

      python scripts/trello_export.py --board 2Fa6uRao --out archive

  Offline – renders text only from a JSON dump produced by the Trello MCP
  connector (trelloReadCard action=list_by_board).

      python scripts/trello_export.py --from-json board.json --out archive

Layout produced:

  archive/README.md                          board index
  archive/<NN>-<list>/README.md              list index
  archive/<NN>-<list>/<card>-<shortLink>/    one folder per card
      README.md                              card content
      01-<file>.jpg ...                      attachments

Only stdlib is used so it runs on a bare GitHub runner.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.trello.com/1"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


# --------------------------------------------------------------------------- helpers

def slug(text: str, maxlen: int = 50) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (text[:maxlen].rstrip("-")) or "bez-nazvu"


def safe_filename(name: str) -> str:
    stem, ext = os.path.splitext(name)
    return f"{slug(stem, 60)}{ext.lower()[:10]}"


def short_link(url: str) -> str:
    m = re.search(r"trello\.com/c/([A-Za-z0-9]+)", url or "")
    return m.group(1) if m else ""


def md_escape_title(text: str) -> str:
    return text.replace("\n", " ").strip() or "(bez názvu)"


# --------------------------------------------------------------------------- live API

class Trello:
    def __init__(self, key: str, token: str):
        self.key, self.token = key, token

    def _auth_header(self) -> dict:
        return {"Authorization": f'OAuth oauth_consumer_key="{self.key}", oauth_token="{self.token}"'}

    def get(self, path: str, **params):
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{API}{path}?{qs}", headers=self._auth_header())
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                body = e.read().decode("utf-8", "replace")[:300]
                hint = ""
                if e.code == 401:
                    k, t = self.key, self.token
                    hint = (f" [key: {len(k)} chars, hex={bool(re.fullmatch(r'[0-9a-f]+', k))}, "
                            f"whitespace={k != k.strip()}; token: {len(t)} chars, "
                            f"whitespace={t != t.strip()}]")
                sys.exit(f"Trello API {e.code} on {path}: {body}{hint}")
        raise RuntimeError(f"Trello API failed: {path}")

    def download(self, url: str, dest: Path) -> None:
        req = urllib.request.Request(url, headers=self._auth_header())
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
                    shutil.copyfileobj(r, f)
                return
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise

    def board(self, board_id: str) -> dict:
        board = self.get(f"/boards/{board_id}", fields="name,url,shortLink,desc")
        lists = self.get(f"/boards/{board_id}/lists", filter="open", fields="name,pos")
        cards = self.get(
            f"/boards/{board_id}/cards",
            filter="open",
            fields="name,desc,pos,idList,shortLink,shortUrl,url,dateLastActivity,due,labels",
            attachments="true",
            attachment_fields="id,name,fileName,url,mimeType,bytes,date,isUpload,pos",
            checklists="all",
        )
        comments: dict[str, list] = {}
        before = None
        while True:
            params = {"filter": "commentCard", "limit": 1000}
            if before:
                params["before"] = before
            page = self.get(f"/boards/{board_id}/actions", **params)
            for a in page:
                comments.setdefault(a["data"]["card"]["id"], []).append({
                    "text": a["data"].get("text", ""),
                    "createdAt": a["date"],
                    "creator": (a.get("memberCreator") or {}).get("fullName", ""),
                })
            if len(page) < 1000:
                break
            before = page[-1]["id"]

        by_list: dict[str, list] = {l["id"]: [] for l in lists}
        for c in cards:
            if c["idList"] not in by_list:
                continue
            by_list[c["idList"]].append({
                "name": c["name"],
                "description": c.get("desc", ""),
                "url": c["url"],
                "shortLink": c["shortLink"],
                "position": c["pos"],
                "lastActivityAt": c.get("dateLastActivity"),
                "labels": [l.get("name") or l.get("color") for l in c.get("labels", [])],
                "checklists": [
                    {"name": cl["name"],
                     "items": [{"name": i["name"], "complete": i["state"] == "complete"}
                               for i in sorted(cl["checkItems"], key=lambda i: i["pos"])]}
                    for cl in c.get("checklists", [])
                ],
                "comments": sorted(comments.get(c["id"], []), key=lambda x: x["createdAt"]),
                "attachments": sorted(c.get("attachments", []), key=lambda a: (a.get("pos") or 0, a.get("date") or "")),
            })
        return {
            "name": board["name"],
            "url": board["url"],
            "lists": [
                {"name": l["name"], "cards": sorted(by_list[l["id"]], key=lambda c: c["position"])}
                for l in sorted(lists, key=lambda l: l["pos"])
            ],
        }


# --------------------------------------------------------------------------- offline (MCP dump)

def board_from_connector_json(path: Path, name: str, url: str) -> dict:
    d = json.loads(path.read_text(encoding="utf-8"))
    lists = []
    for l in d["lists"]:
        cards = []
        for c in l["cards"]:
            if c.get("closed"):
                continue
            cards.append({
                "name": c["name"],
                "description": c.get("description", ""),
                "url": c.get("url", ""),
                "shortLink": short_link(c.get("shortUrl") or c.get("url", "")),
                "position": c.get("position", 0),
                "lastActivityAt": c.get("lastActivityAt"),
                "labels": [lb.get("name") or lb.get("color") for lb in c.get("labels", [])],
                "checklists": [
                    {"name": cl.get("name", ""),
                     "items": [{"name": i.get("name", ""), "complete": i.get("state") == "complete" or i.get("complete")}
                               for i in cl.get("items", cl.get("checkItems", []))]}
                    for cl in c.get("checklists", [])
                ],
                "comments": [{"text": cm.get("text", ""), "createdAt": cm.get("createdAt", ""),
                              "creator": (cm.get("creator") or {}).get("fullName", "")}
                             for cm in c.get("comments", [])],
                "attachments": None,  # unknown in offline mode
            })
        lists.append({"name": l["name"], "cards": sorted(cards, key=lambda c: c["position"])})
    return {"name": (d.get("board") or {}).get("name") or name, "url": url, "lists": lists}


# --------------------------------------------------------------------------- rendering

def render(board: dict, out: Path, client: Trello | None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    stats = {"lists": 0, "cards": 0, "files": 0, "downloaded": 0, "links": 0}
    keep_dirs: set[Path] = set()
    board_lines = [f"# {board['name']}", "", f"Export z Trella: <{board['url']}>", "",
                   "| List | Karet |", "|---|---|"]

    for li, lst in enumerate(board["lists"], 1):
        stats["lists"] += 1
        ldir = out / f"{li:02d}-{slug(lst['name'])}"
        ldir.mkdir(exist_ok=True)
        keep_dirs.add(ldir)
        board_lines.append(f"| [{lst['name']}]({ldir.name}/README.md) | {len(lst['cards'])} |")
        list_lines = [f"# {lst['name']}", "", f"[← zpět na board](../README.md)", ""]

        for card in lst["cards"]:
            stats["cards"] += 1
            sl = card["shortLink"] or "x"
            cdir = ldir / f"{slug(card['name'])}-{sl}"
            # card renamed? reuse the folder that ends with the same shortLink
            for old in ldir.glob(f"*-{sl}"):
                if old != cdir and old.is_dir():
                    old.rename(cdir)
            cdir.mkdir(exist_ok=True)
            keep_dirs.add(cdir)

            attachments = card["attachments"]
            files_md: list[str] = []
            if attachments is not None:
                wanted: set[str] = {"README.md"}
                for ai, a in enumerate(attachments, 1):
                    if a.get("isUpload"):
                        fname = f"{ai:02d}-{safe_filename(a.get('fileName') or a.get('name') or 'soubor')}"
                        wanted.add(fname)
                        dest = cdir / fname
                        stats["files"] += 1
                        if client and not (dest.exists() and (not a.get("bytes") or dest.stat().st_size == a["bytes"])):
                            client.download(a["url"], dest)
                            stats["downloaded"] += 1
                        ext = os.path.splitext(fname)[1]
                        if ext in IMAGE_EXT:
                            files_md.append(f"![{a.get('name') or fname}]({fname})")
                        else:
                            files_md.append(f"- [{a.get('name') or fname}]({fname})")
                    else:
                        stats["links"] += 1
                        files_md.append(f"- Odkaz: [{a.get('name') or a['url']}]({a['url']})")
                # remove attachments deleted in Trello
                for f in cdir.iterdir():
                    if f.is_file() and f.name not in wanted:
                        f.unlink()

            title = md_escape_title(card["name"])
            lines = [f"# {title}", "",
                     f"**List:** {lst['name']}  ",
                     f"**Trello:** <{card['url']}>  "]
            if card.get("labels"):
                lines.append(f"**Štítky:** {', '.join(x for x in card['labels'] if x)}  ")
            lines += ["", "[← zpět na list](../README.md)", ""]
            if card["description"].strip():
                lines += ["## Popis", "", card["description"].strip(), ""]
            for cl in card["checklists"]:
                lines += [f"## {cl['name']}", ""]
                lines += [f"- [{'x' if i['complete'] else ' '}] {i['name']}" for i in cl["items"]]
                lines.append("")
            if card["comments"]:
                lines += ["## Komentáře", ""]
                for cm in card["comments"]:
                    lines += [f"**{cm['creator']}** ({cm['createdAt'][:10]}):", "", cm["text"], ""]
            if attachments is not None:
                lines += [f"## Přílohy ({len(attachments)})", ""]
                lines += [x + ("\n" if x.startswith("!") else "") for x in files_md]
            else:
                lines += ["## Přílohy", "", "_Zatím nestaženo – doplní workflow „Trello export“._", ""]
            (cdir / "README.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

            count = f" · {len(attachments)} příloh" if attachments else ""
            list_lines.append(f"- [{title}]({cdir.name}/README.md){count}")

        (ldir / "README.md").write_text("\n".join(list_lines) + "\n", encoding="utf-8")

    # drop folders of lists/cards that no longer exist in Trello
    for p in sorted(out.glob("*/*"), reverse=True):
        if p.is_dir() and p not in keep_dirs:
            shutil.rmtree(p)
    for p in out.glob("*"):
        if p.is_dir() and p not in keep_dirs:
            shutil.rmtree(p)

    board_lines += ["", f"Celkem {stats['cards']} karet."]
    (out / "README.md").write_text("\n".join(board_lines) + "\n", encoding="utf-8")
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default=os.environ.get("TRELLO_BOARD", "2Fa6uRao"),
                    help="board id or shortLink (default: 2Fa6uRao = Fotodokumentace)")
    ap.add_argument("--out", default="archive", type=Path)
    ap.add_argument("--from-json", type=Path, help="render from a Trello MCP connector dump, no API access")
    args = ap.parse_args()

    if args.from_json:
        board = board_from_connector_json(args.from_json, "Fotodokumentace",
                                          f"https://trello.com/b/{args.board}")
        client = None
    else:
        key, token = os.environ.get("TRELLO_API_KEY"), os.environ.get("TRELLO_TOKEN")
        if not key or not token:
            print("Set TRELLO_API_KEY and TRELLO_TOKEN", file=sys.stderr)
            return 2
        client = Trello(key, token)
        board = client.board(args.board)

    stats = render(board, args.out, client)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
