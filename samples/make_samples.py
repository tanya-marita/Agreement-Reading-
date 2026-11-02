"""Create filled sample agreements in every supported format for testing:
text PDF, scanned PDF, photo (JPG) and Word (.docx)."""
import io
import os

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

HERE = os.path.dirname(os.path.abspath(__file__))

TITLE = "SERVICE AGREEMENT"
PARAS = [
    "Agreement No.: SA-2026-014",
    "THIS AGREEMENT is made and entered into on 15/03/2026 (\"Effective Date\")",
    "BETWEEN: Lanka Data Solutions (Pvt) Ltd, a company incorporated under the laws of Sri Lanka, with its registered "
    "office at No. 45, Galle Road, Colombo 03, registration no. PV 123456 (the \"Company\"),",
    "AND: Nimal Perera, an individual holding NIC no. 198512345678, residing at 12/4 Temple Road, Kandy "
    "(the \"Service Provider\").",
    "WHEREAS the Company wishes to engage the Service Provider to design and maintain its customer data dashboard.",
    "1. Term. This Agreement commences on 01/04/2026 and continues for a period of twelve (12) months, unless terminated earlier.",
    "2. Services. The Service Provider shall build the dashboard, deliver monthly reports and provide support during office hours.",
    "3. Fees. The Company shall pay the Service Provider a monthly fee of Rs. 150,000.00, payable within 14 days of invoice. "
    "A one-time setup fee of LKR 75,000 is payable on signing. Late payments attract interest of 2% per month.",
    "4. Termination. Either party may terminate this Agreement by giving thirty (30) days' written notice.",
    "5. Governing Law. This Agreement is governed by the laws of the Democratic Socialist Republic of Sri Lanka.",
    "IN WITNESS WHEREOF the parties have signed: For Lanka Data Solutions (Pvt) Ltd — Kamala Silva, Managing Director. "
    "Service Provider — Nimal Perera.",
]


def text_pdf(path):
    ss = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm)
    story = [Paragraph(TITLE, ss["Title"])]
    for p in PARAS:
        story += [Paragraph(p, ss["BodyText"]), Spacer(1, 6)]
    doc.build(story)


def scanned_versions(text_pdf_path, scan_pdf_path, jpg_path):
    from pdf2image import convert_from_path
    from PIL import ImageFilter

    pages = convert_from_path(text_pdf_path, dpi=150)
    imgs = [p.convert("L").rotate(0.7, expand=True, fillcolor=255).filter(ImageFilter.GaussianBlur(0.4)).convert("RGB")
            for p in pages]
    imgs[0].save(scan_pdf_path, save_all=True, append_images=imgs[1:], resolution=150)  # image-only PDF
    imgs[0].save(jpg_path, quality=85)


def word(path):
    import docx
    d = docx.Document()
    d.add_heading(TITLE, 0)
    for p in PARAS[:-1]:
        d.add_paragraph(p)
    t = d.add_table(rows=3, cols=2)
    for r, (a, b) in enumerate([("For Lanka Data Solutions (Pvt) Ltd", "Service Provider"),
                                ("Kamala Silva", "Nimal Perera"), ("Managing Director", "")]):
        t.cell(r, 0).text, t.cell(r, 1).text = a, b
    d.save(path)


if __name__ == "__main__":
    tp = os.path.join(HERE, "service_agreement_text.pdf")
    text_pdf(tp)
    scanned_versions(tp, os.path.join(HERE, "service_agreement_scanned.pdf"), os.path.join(HERE, "service_agreement_photo.jpg"))
    word(os.path.join(HERE, "service_agreement.docx"))
    print("Samples written to", HERE)
