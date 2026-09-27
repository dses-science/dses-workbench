"""Check a built PDF the way DOCUMENT_STANDARDS section 8 asks: a real text
layer (nothing rasterized), fonts embedded, and any version stamps present.

    python tools/verify_pdf.py <file.pdf> [needle ...]

Prints pages, extractable characters, image count and every font with the
kind of font program embedded; each needle must occur in the extracted text.
Exit status 1 when the text layer is missing or a needle is not found.
Needs pypdf (pip install pypdf) — a check-time tool, not a dependency of the
application or of the document build.

Reading the font list: Distiller output names its subsets (ABCDEF+MinionPro-
Regular, CFF/OpenType programs); 'Microsoft Print to PDF' — build_doc.py's
automatic fallback when Distiller fails — embeds them as anonymized CIDFont+F1…
TrueType programs. Both are selectable, searchable text; Word's own exporter
would show image-only pages and almost no characters."""
import sys


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        from pypdf import PdfReader
    except ImportError:
        print("pypdf is not installed for this interpreter: pip install pypdf")
        return 2
    path, needles = argv[1], argv[2:]
    reader = PdfReader(path)
    chars, images, fonts, text = 0, 0, {}, []
    for page in reader.pages:
        t = page.extract_text() or ""
        chars += len(t)
        text.append(t)
        try:
            images += len(page.images)
        except Exception:
            pass
        res = page.get("/Resources")
        if res is None:
            continue
        fdict = res.get_object().get("/Font")
        if not fdict:
            continue
        for key in fdict.get_object():
            try:
                f = fdict.get_object()[key].get_object()
            except Exception:
                continue
            desc = f.get("/FontDescriptor")
            if desc is None and f.get("/DescendantFonts"):
                try:
                    desc = f["/DescendantFonts"][0].get_object().get("/FontDescriptor")
                except Exception:
                    desc = None
            program = "not embedded"
            if desc is not None:
                desc = desc.get_object()
                for k, label in (("/FontFile", "Type1"), ("/FontFile2", "TrueType"),
                                 ("/FontFile3", "CFF/OpenType")):
                    if k in desc:
                        program = label
            fonts[str(f.get("/BaseFont", "?"))] = (str(f.get("/Subtype", "?")), program)
    print(f"file   : {path}")
    print(f"pages  : {len(reader.pages)} | chars: {chars} | images: {images}")
    for name in sorted(fonts):
        print(f"  font : {name}  {fonts[name][0]}  {fonts[name][1]}")
    joined = " ".join(text)
    ok = True
    for n in needles:
        hit = n in joined
        ok &= hit
        print(f"  text : {n!r} -> {'OK' if hit else 'MISSING'}")
    if chars < 2000:
        print("RESULT: FAIL — no usable text layer (rasterized?)")
        return 1
    if not fonts:
        print("RESULT: FAIL — no fonts at all")
        return 1
    if any(p == "not embedded" for _, p in fonts.values()):
        print("RESULT: FAIL — a font is not embedded")
        return 1
    if not ok:
        print("RESULT: FAIL — expected text missing")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
