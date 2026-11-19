#!/usr/bin/env python3
"""Regenerate Installing.md section 9 (Appendix C - Operating Guide) from the
in-app Help, and optionally stamp a new version into sections 1-5.

Section 9 is a copy of HELP_TEXT_HTML in dses_workbench.py; the Help is the
authority and this is the mechanical HTML -> Markdown sync the Release
Workflow asks for at every cut (section 4.3). It reproduces the section's
existing Markdown style exactly, so the diff after a run is the Help changes
and nothing else: one line per paragraph and per list item (build_doc carries
source line breaks into the document), a blank line between blocks, **bold**,
*italic*, `code`.

    .conda/python.exe tools/sync_guide_section9.py            # rewrite section 9
    .conda/python.exe tools/sync_guide_section9.py --check    # report only
    .conda/python.exe tools/sync_guide_section9.py --stamp 1.7.0 1.8.0

--stamp OLD NEW replaces OLD with NEW in the lines BEFORE section 6 only (the
cover line, the download link, the folder names in the install steps);
section 6 keeps its version history ("From 1.6.1 the launcher ...").

History: written as a scratch script for the 1.7.0 cut (2026-11-05) and lost
with the scratch folder; moved here for 1.8.0. The 1.1.8 cut's scripted edit
of this file dropped a backtick from every code fence, so after ANY scripted
change look at the built PDF's command blocks (build_doc refuses damaged
fences, but look anyway).
"""
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUIDE = REPO / "Installing.md"
SECTION = "## 9. Appendix C"
PREAMBLE = "> This appendix"


class _HelpToMarkdown(HTMLParser):
    """The subset of HTML the Help uses: h2-h4, p, ul/ol/li (nested), b, i,
    code, br, a."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # finished blocks
        self.buf = []          # inline text of the block being read
        self.stack = []        # open block tags
        self.list_depth = 0
        self.href = None

    def _flush(self, kind=None):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not text:
            return
        if kind == "li":
            self.out.append("  " * (self.list_depth - 1) + "- " + text)
        elif kind in ("h2", "h3", "h4"):
            self.out.append({"h2": "##", "h3": "###", "h4": "####"}[kind] + " " + text)
        else:
            self.out.append(text)

    def _in_li(self):
        return bool(self.stack) and self.stack[-1] == "li"

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "h2", "h3", "h4"):
            self._flush()
            self.stack.append(tag)
        elif tag in ("ul", "ol"):
            self._flush("li" if self._in_li() else None)
            self.list_depth += 1
            self.stack.append(tag)
        elif tag == "li":
            self._flush("li" if self._in_li() else None)
            self.stack.append("li")
        elif tag in ("b", "strong"):
            self.buf.append("**")
        elif tag in ("i", "em"):
            self.buf.append("*")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "br":
            self.buf.append(" ")
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.buf.append("[")

    def handle_endtag(self, tag):
        if tag in ("p", "h2", "h3", "h4"):
            self._flush(tag if tag != "p" else None)
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
        elif tag in ("ul", "ol"):
            self._flush("li" if self._in_li() else None)
            if self._in_li():
                self.stack.pop()
            self.list_depth -= 1
            if self.stack and self.stack[-1] == tag:
                self.stack.pop()
        elif tag == "li":
            self._flush("li")
            if self._in_li():
                self.stack.pop()
        elif tag in ("b", "strong"):
            self.buf.append("**")
        elif tag in ("i", "em"):
            self.buf.append("*")
        elif tag == "code":
            self.buf.append("`")
        elif tag == "a":
            self.buf.append(f"]({self.href})")

    def handle_data(self, data):
        self.buf.append(data)


def help_markdown():
    """Section 9's body (everything after the preamble) from the live Help."""
    os.environ["PYQTGRAPH_QT_LIB"] = "PySide6"
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(REPO))
    import dses_workbench as app
    conv = _HelpToMarkdown()
    conv.feed(app.HELP_TEXT_HTML)
    conv._flush()
    blocks = [b for b in conv.out if not b.startswith("## ")]     # the <h2> title
    return "\n\n".join(blocks) + "\n", app.APP_VERSION


def main(argv):
    check = "--check" in argv
    stamp = None
    if "--stamp" in argv:
        i = argv.index("--stamp")
        stamp = (argv[i + 1], argv[i + 2])
    raw = GUIDE.read_bytes().decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    try:
        s9 = next(i for i, l in enumerate(lines) if l.startswith(SECTION))
        pre = next(i for i in range(s9, len(lines)) if lines[i].startswith(PREAMBLE))
        s6 = next(i for i, l in enumerate(lines) if l.startswith("## 6."))
    except StopIteration:
        sys.exit("Installing.md: section 9 heading, its preamble or section 6 not found")
    if lines[pre + 1] != "":
        sys.exit("Installing.md: expected a blank line after the section 9 preamble")
    body, version = help_markdown()
    old_body = nl.join(lines[pre + 2:])
    new_body = body.replace("\n", nl)
    head = lines[:pre + 2]
    stamped = []
    if stamp:
        for i in range(s6):
            if stamp[0] in head[i]:
                head[i] = head[i].replace(stamp[0], stamp[1])
                stamped.append(i + 1)
    changed = sum(1 for a, b in zip(old_body.split(nl), new_body.split(nl)) if a != b) \
        + abs(len(old_body.split(nl)) - len(new_body.split(nl)))
    print(f"section 9: {len(new_body.split(nl))} lines from the Help of {version}; "
          f"{changed} line(s) differ from Installing.md")
    if stamp:
        print(f"stamp {stamp[0]} -> {stamp[1]} on lines {stamped}")
    if check:
        return 0
    GUIDE.write_bytes((nl.join(head) + nl + new_body).encode("utf-8"))
    print("Installing.md written")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
