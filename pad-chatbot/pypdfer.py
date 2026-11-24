from pathlib import Path
from pypdf import PdfReader

pdf = Path(r"C:\Users\skabo\Downloads\ttdd_n94_der.pdf")
sortie = Path("docs") / "PAD_partenariat.txt"
sortie.parent.mkdir(exist_ok=True)

lecteur = PdfReader(pdf)
pages = []
for n, page in enumerate(lecteur.pages, start=1):
    texte = (page.extract_text() or "").strip()
    if texte:
        pages.append(f"[Page {n}]\n{texte}")

sortie.write_text("\n\n".join(pages), encoding="utf-8")
print(f"{len(pages)} pages extraites -> {sortie}")