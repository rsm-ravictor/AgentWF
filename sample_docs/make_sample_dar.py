"""Generate a sample Daily Activity Report PDF with real yellow/red highlighting.

Lets you exercise highlight detection without a real tenant document.

    python sample_docs/make_sample_dar.py
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent / "sample_dar_highlighted.pdf"

YELLOW = colors.Color(1, 0.94, 0.45)
RED = colors.Color(1, 0.6, 0.58)

# (time, unit, description, highlight)
ROWS = [
    ("19:05", "—", "Began patrol at south gate. All quiet.", None),
    ("19:40", "12A", "Noise complaint from 11A — loud music and shouting. Advised occupant to reduce volume.", "yellow"),
    ("20:15", "—", "Checked mail room and laundry. Secure.", None),
    ("20:52", "8C", "Unleashed dog in courtyard, no owner present. Dog returned to 8C by resident.", "yellow"),
    ("21:30", "4B", "Bags of household trash left on balcony, visible from street. Third occurrence this month.", "red"),
    ("22:10", "—", "Pool gate found propped open. Secured.", None),
    ("22:48", "12A", "Second noise complaint from 11A. Music resumed after earlier warning.", "red"),
    ("23:20", "22C", "Cigarette smoke reported in non-smoking corridor near 22C. No contact made.", "yellow"),
    ("23:55", "—", "Perimeter walk complete. Lighting out at north stair.", None),
    ("00:40", "9A", "Vehicle parked in fire lane, plate 7XYZ123. Tagged; towing not required.", "yellow"),
    ("01:25", "4B", "Resident confronted patrol regarding trash notice. Verbally aggressive; no threat made.", "red"),
    ("02:10", "—", "Quiet. No further activity to report.", None),
]


def main() -> None:
    c = canvas.Canvas(str(OUT), pagesize=letter)
    width, height = letter

    c.setFont("Helvetica-Bold", 15)
    c.drawString(54, height - 54, "DAILY ACTIVITY REPORT")
    c.setFont("Helvetica", 9.5)
    c.drawString(54, height - 72, "Property: Harbor View Apartments        Report Date: 2026-08-08")
    c.drawString(54, height - 86, "Shift: 19:00 - 03:00        Officer: R. Delgado, Badge 4471")

    y = height - 116
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(54, y, "TIME")
    c.drawString(96, y, "UNIT")
    c.drawString(146, y, "ACTIVITY / OBSERVATION")
    c.setLineWidth(0.6)
    c.line(54, y - 5, width - 54, y - 5)

    y -= 20
    for time, unit, desc, highlight in ROWS:
        # Wrap description to the column width.
        words, lines, current = desc.split(), [], ""
        for w in words:
            trial = f"{current} {w}".strip()
            if c.stringWidth(trial, "Helvetica", 8.5) > width - 146 - 54:
                lines.append(current)
                current = w
            else:
                current = trial
        lines.append(current)

        block_h = 11 * len(lines) + 4
        if highlight:
            c.setFillColor(YELLOW if highlight == "yellow" else RED)
            c.rect(50, y - block_h + 9, width - 100, block_h, stroke=0, fill=1)

        c.setFillColor(colors.black)
        c.setFont("Helvetica", 8.5)
        c.drawString(54, y, time)
        c.drawString(96, y, unit)
        for i, line in enumerate(lines):
            c.drawString(146, y - (11 * i), line)

        y -= block_h + 5
        if y < 90:
            c.showPage()
            y = height - 60

    c.setFont("Helvetica-Oblique", 7.5)
    c.setFillColor(colors.grey)
    c.drawString(54, 52, "Highlighted rows require follow-up. Yellow = monitor. Red = escalate to management.")

    c.save()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
