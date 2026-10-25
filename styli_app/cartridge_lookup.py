"""Finding a cartridge's photo and specifications online, from Wikipedia or a manufacturer's or
shop's product page.

Product pages are read for their meta tags, structured data (JSON-LD) and "Label: value" lines,
and specifications are recognised by the patterns in cartridges.SPEC_FIELDS."""

import html
import html.parser
import json
import re
import urllib.parse

from styli_app import cartridges, web
from styli_app.common import key_tokens


def lookup_wikipedia(name):
    """Photo and short description from Wikipedia, when it has an article on the cartridge."""
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": name, "gsrlimit": 6,
              "prop": "pageimages|extracts|info", "inprop": "url", "exintro": 1, "explaintext": 1,
              "exsentences": 3, "piprop": "thumbnail", "pithumbsize": 640}
    data, _, _ = web.http_get("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params))
    pages = sorted((json.loads(data.decode("utf-8")).get("query") or {}).get("pages", {}).values(),
                   key=lambda p: p.get("index", 99))
    want = key_tokens(name)
    models = {w for w in want if re.search(r"\d", w)}
    for p in pages:
        title = key_tokens(p.get("title"))
        if (models and models & title) or len(want & title) >= 2:
            return {"title": p.get("title"), "description": (p.get("extract") or "").strip(),
                    "image_url": (p.get("thumbnail") or {}).get("source"), "url": p.get("fullurl"),
                    "specs": {}, "source": "Wikipedia"}
    return None


class _PageReader(html.parser.HTMLParser):
    """Pulls out meta tags, JSON-LD and 'label: value' lines from a product page."""
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "section", "table", "ul", "ol"}
    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.lines, self.jsonld, self.title = {}, [], [], ""
        self._buf, self._cells, self._cell, self._dt, self._skip, self._in_title = [], None, None, None, 0, False
        self._ld = None

    def _flush(self):
        text = " ".join("".join(self._buf).split())
        if text:
            self.lines.append(text)
        self._buf = []

    def handle_starttag(self, tag, attrs):
        """Note meta tags and structured data, and start a new line at each block, row or cell."""
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._ld = []
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "tr":
            self._flush()
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []
        elif tag in ("dt", "dd"):
            self._flush()
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        """Finish table rows and definition lists as 'Label: value' lines."""
        if tag == "script" and self._ld is not None:
            self.jsonld.append("".join(self._ld))
            self._ld = None
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("td", "th") and self._cell is not None:
            self._cells.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            cells = [c for c in self._cells if c]
            if len(cells) >= 2:
                self.lines.append("{}: {}".format(cells[0], " ".join(cells[1:])))
            elif cells:
                self.lines.append(cells[0])
            self._cells = None
        elif tag == "dt":
            self._dt = " ".join("".join(self._buf).split())
            self._buf = []
        elif tag == "dd":
            text = " ".join("".join(self._buf).split())
            self._buf = []
            if self._dt:
                self.lines.append("{}: {}".format(self._dt, text))
                self._dt = None
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, data):
        """Collect the visible text (scripts and styles are skipped)."""
        if self._ld is not None:
            self._ld.append(data)
            return
        if self._skip:
            return
        if self._in_title:
            self.title += data
        elif self._cell is not None:
            self._cell.append(data)
        else:
            self._buf.append(data)


def _walk_jsonld(obj):
    """Every object in a page's structured data (JSON-LD), including nested ones."""
    if isinstance(obj, list):
        for o in obj:
            yield from _walk_jsonld(o)
    elif isinstance(obj, dict):
        yield obj
        for k in ("@graph", "mainEntity", "offers"):
            if k in obj:
                yield from _walk_jsonld(obj[k])


def extract_specs(lines):
    """Pick cartridge specifications out of 'Label: value' lines, using the patterns in SPEC_FIELDS."""
    specs = {}
    for line in lines:
        m = re.match(r"^\s*([^:]{2,45}?)\s*[:：]\s*(.{1,120})$", line)
        if not m:
            continue
        key, value = m.group(1).strip().lower().lstrip("-•* "), m.group(2).strip()
        for field, _, pattern in cartridges.SPEC_FIELDS:
            if field not in specs and re.search(pattern, key):
                specs[field] = value
                break
    return specs


def name_from_page_title(title):
    """'Ortofon 2M Red MM Cartridge | Hi-Fi Shop' -> 'Ortofon 2M Red MM Cartridge'."""
    for sep in (" | ", " \u2013 ", " \u2014 ", " - ", " :: "):
        if sep in (title or ""):
            title = title.split(sep)[0]
    return " ".join((title or "").split())


def scrape_product_page(url):
    """Photo, description and specs from a manufacturer's or shop's product page."""
    data, ctype, final = web.http_get(url)
    charset = re.search(r"charset=([\w-]+)", ctype or "")
    text = data.decode(charset.group(1) if charset else "utf-8", errors="replace")
    reader = _PageReader()
    reader.feed(text)
    reader.close()
    lines, image, description, title = list(reader.lines), None, "", ""
    for raw in reader.jsonld:
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        for node in _walk_jsonld(parsed):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" not in types:
                continue
            title = title or node.get("name") or ""
            img = node.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            image = image or img
            description = description or html.unescape(re.sub(r"<[^>]+>", " ", node.get("description") or ""))
            for prop in node.get("additionalProperty") or []:
                if isinstance(prop, dict) and prop.get("name"):
                    lines.append("{}: {}".format(prop["name"], prop.get("value", "")))
    image = reader.meta.get("og:image") or reader.meta.get("twitter:image") or image
    description = description or reader.meta.get("og:description") or reader.meta.get("description") or ""
    lines.extend(re.split(r"[\r\n•]+|\s{2,}", description))
    title = reader.meta.get("og:title") or title or reader.title.strip()
    description = " ".join(description.split())
    if len(description) > 400:
        description = description[:400].rsplit(" ", 1)[0] + "..."
    return {"title": title, "description": description, "specs": extract_specs(lines),
            "image_url": urllib.parse.urljoin(final, image) if image else None, "url": final,
            "source": urllib.parse.urlparse(final).netloc}
