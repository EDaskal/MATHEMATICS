"""Δοκιμή παραγωγής Word από αποθηκευμένες μεταγραφές (JSON):
python scripts/build_cli.py out.docx "a.json" "b.json+c.json" ...   (κάθε όρισμα = ένα θέμα· + για πολλές ασκήσεις)
"""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import points
from app.docx_build import build_docx
from app.figures import render_to_files

out = Path(sys.argv[1])
themes = []
tot = points.theme_points(len(sys.argv[2:]))
for ti, arg in enumerate(sys.argv[2:]):
    exs = []
    for f in arg.split("+"):
        ex = json.loads(Path(f).read_text(encoding="utf-8"))
        if ex.get("figure"):
            spec_file = Path(f).with_suffix(".spec.json")
            spec = spec_file.read_text(encoding="utf-8") if spec_file.exists() else ex["figure"]["spec_json"]
            ex["figure_render"] = render_to_files(spec, out.parent / "figs" / Path(f).stem)
        exs.append(ex)
    n = len(exs[0]["items"]) if len(exs) == 1 else len(exs)
    themes.append({"exercises": exs, "points": points.item_points(tot[ti], n)})
exam = {"title": "ΔΙΑΓΩΝΙΣΜΑ Β’ ΛΥΚΕΙΟΥ ΑΛΓΕΒΡΑ", "subtitle": "ΣΥΣΤΗΜΑΤΑ – ΠΟΛΥΩΝΥΜΑ – ΛΟΓΑΡΙΘΜΟΙ", "date": "", "themes": themes}
res = build_docx(exam, out, footer_text="Β’ ΛΥΚΕΙΟΥ ΑΛΓΕΒΡΑ\tΕπιμέλεια: Δοκιμή")
print(json.dumps({k: v for k, v in res.items() if k != "markdown"}, ensure_ascii=False))
(out.with_suffix(".md")).write_text(res["markdown"], encoding="utf-8")
