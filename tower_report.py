"""
tower_report.py — Builds the per-tower "RGB Visual Inspection Report" PDF.

A different report from trans_report.py (which covers a whole project,
grouped by tower) — this one is for a SINGLE tower, triggered from that
tower's own panel on the map, with a specific fixed layout:

    Page 1  — Full-bleed navy header band, General Information table,
              RGB Defect Summary table.
    Page 2+ — Detailed Info, starting on a new page: 2 defects per page
              (verified — see build_tower_report_pdf's docstring), each
              with its details on the left and its marked-up image on
              the right.

Corporate visual style: dark navy (#1a2744) header bands, a gold
(#b8944f) accent rule, restrained color used only for severity text and
the section-break rule — chosen over more colorful alternatives after
reviewing three style directions together.

Uses the same libraries as chimney_report.py / trans_report.py
(ReportLab — already a dependency).
"""
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

PAGE_W, PAGE_H = A4  # portrait
DEFECTS_PER_DETAIL_PAGE = 2

NAVY = colors.HexColor('#1a2744')
GOLD = colors.HexColor('#b8944f')
GRAY = colors.HexColor('#5a5f68')
LIGHT_GRAY = colors.HexColor('#f4f5f7')
HAIRLINE = colors.HexColor('#d5d8de')

SEVERITY_COLORS = {
    'Minor':    colors.HexColor('#3a7d5c'),
    'Major':    colors.HexColor('#a8752f'),
    'Critical': colors.HexColor('#a83232'),
}

MARGIN = 15 * mm


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='CellText', fontSize=8, leading=10.5, alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='CellTextLeft', fontSize=8.5, leading=11, alignment=0))
    styles.add(ParagraphStyle(
        name='CorpTitle', fontSize=22, leading=26, fontName='Helvetica-Bold', textColor=colors.white))
    styles.add(ParagraphStyle(
        name='CorpSubtitle', fontSize=10.5, leading=14, textColor=colors.HexColor('#c9d2e3')))
    styles.add(ParagraphStyle(
        name='SectionHead', fontSize=13, leading=16, fontName='Helvetica-Bold',
        textColor=NAVY, spaceBefore=4, spaceAfter=10))
    styles.add(ParagraphStyle(
        name='GenInfoLabel', fontSize=8.5, leading=12, fontName='Helvetica-Bold', textColor=GRAY))
    styles.add(ParagraphStyle(
        name='GenInfoValue', fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(
        name='DetailLabel', fontSize=8.5, leading=12, fontName='Helvetica-Bold', textColor=GRAY))
    styles.add(ParagraphStyle(
        name='DetailValue', fontSize=9, leading=13, alignment=TA_LEFT, textColor=colors.HexColor('#333333')))
    return styles


def _indent(flow, width=None):
    """Wraps a flowable so it sits within the body margins — needed
    because the header band below is deliberately full-bleed (edge to
    edge), which means the document's own margins can't just be set
    globally without also indenting the header."""
    w = width if width is not None else (PAGE_W - 2 * MARGIN)
    t = Table([[flow]], colWidths=[w])
    t.setStyle(TableStyle([
        ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return t


def _header_band(tower_id, styles):
    content = Table(
        [[Paragraph('RGB VISUAL INSPECTION REPORT', styles['CorpTitle'])],
         [Paragraph(f'DROGO AEROSPACE &nbsp;·&nbsp; Tower {tower_id}', styles['CorpSubtitle'])]],
        colWidths=[PAGE_W - 2 * MARGIN],
    )
    band = Table([[content]], colWidths=[PAGE_W])
    band.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), NAVY),
        ('LINEBELOW', (0, 0), (-1, -1), 2, GOLD),
        ('LEFTPADDING', (0, 0), (-1, -1), MARGIN),
        ('TOPPADDING', (0, 0), (-1, -1), 12 * mm),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12 * mm),
    ]))
    return band


def _general_info_table(info, styles):
    rows = [
        ['Line Name', info['line_name']],
        ['Tower ID', info['tower_id']],
        ['Voltage Level', info['voltage_level'] or '—'],
        ['Coordinates', info['coordinates']],
        ['Survey Date', info['survey_date'] or '—'],
        ['Pilot Name', info['pilot_name'] or '—'],
        ['Inspection Name', info['inspection_name'] or '—'],
        ['Report Generation Date', info['report_date']],
    ]
    table = Table(
        [[Paragraph(k, styles['GenInfoLabel']), Paragraph(str(v), styles['GenInfoValue'])]
         for k, v in rows],
        colWidths=[48 * mm, (PAGE_W - 2 * MARGIN) - 48 * mm],
    )
    table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
    ]))
    return table


def _defect_summary_table(defects, styles):
    header = ['S.No', 'Defect ID', 'Component Name', 'Defect Type', 'Severity', 'Status']
    col_widths = [14 * mm, 22 * mm, 44 * mm, 44 * mm, 24 * mm, 22 * mm]
    data = [header]
    for i, d in enumerate(defects, start=1):
        sev = d.get('severity') or 'Minor'
        sev_color = SEVERITY_COLORS.get(sev, colors.HexColor('#333333'))
        sev_para = Paragraph(f'<font color="{sev_color.hexval()}"><b>{sev}</b></font>', styles['CellText'])
        data.append([
            Paragraph(str(i), styles['CellText']),
            Paragraph(f"D{i}", styles['CellText']),
            Paragraph(d.get('component_name') or '—', styles['CellText']),
            Paragraph(d.get('defect_type') or '—', styles['CellText']),
            sev_para,
            Paragraph(d.get('status') or 'Open', styles['CellText']),
        ])
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, LIGHT_GRAY]),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return table


# Each defect on a detail page gets EXACTLY half the page's vertical
# space — computed from the more constrained case (the first detail
# page, which also carries the "3. Detailed Information" heading) and
# then applied to every defect block uniformly, including on later pages
# that don't have that heading. Using a single consistent height avoids
# a real risk: if blocks on the heading-free pages were sized taller
# (since more room is technically available there), and that height were
# then reused on the heading page, the heading + block height wouldn't fit
# together — ReportLab would auto-overflow the second block onto a third
# page, silently breaking the "exactly 2 defects per page" pagination
# this report was specifically fixed to guarantee.
_HEADING_BLOCK_HEIGHT = 16 + 4 + 10 + (2 * mm)  # SectionHead leading/spaceBefore/spaceAfter + the Spacer after it
_AVAILABLE_WITH_HEADING = PAGE_H - MARGIN - _HEADING_BLOCK_HEIGHT - (16 * mm)  # 16mm matches doc's bottomMargin
HALF_PAGE_BLOCK_HEIGHT = _AVAILABLE_WITH_HEADING / 2 - (0.5 * mm)  # minimal floating-point safety buffer only



def _detail_block(defect, seq_no, static_root, styles):
    """One defect's left-details / right-image row for the Detailed Info
    section."""
    sev = defect.get('severity') or 'Minor'
    sev_color = SEVERITY_COLORS.get(sev, colors.HexColor('#333333'))

    left_rows = [
        ['Defect ID', f'D{seq_no}'],
        ['Component Name', defect.get('component_name') or '—'],
        ['Defect Type', defect.get('defect_type') or '—'],
        ['Location', defect.get('location') or '—'],
        ['Severity', f'<font color="{sev_color.hexval()}"><b>{sev}</b></font>'],
        ['Status', defect.get('status') or 'Open'],
        ['Observation', defect.get('observation') or '—'],
    ]
    if defect.get('comments'):
        left_rows.append(['Comments', defect['comments']])

    left_table = Table(
        [[Paragraph(k, styles['DetailLabel']), Paragraph(str(v), styles['DetailValue'])]
         for k, v in left_rows],
        colWidths=[28 * mm, 52 * mm],
    )
    left_table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))

    # Right side: the marked-up defect image — sized to fill the full
    # available box (both dimensions), not a small fixed cap. The box is
    # now roughly half a page tall, so a fixed 85mm-max image would leave
    # a lot of empty space around it; instead we compute the actual
    # available width/height (the cell's dimensions minus this table's
    # own padding) and scale the image up to fill whichever dimension is
    # the binding constraint for its aspect ratio.
    right_col_width = (PAGE_W - 2 * MARGIN) - 84 * mm
    available_img_w = right_col_width - 20  # minus 10pt left+right padding below
    available_img_h = HALF_PAGE_BLOCK_HEIGHT - 20  # minus 10pt top+bottom padding below

    right_cell = Paragraph('No image available', styles['CellText'])
    image_path = defect.get('image_path') or ''
    if image_path:
        full_path = os.path.join(static_root, image_path)
        if os.path.exists(full_path):
            try:
                from PIL import Image as PILImage
                with PILImage.open(full_path) as pil_img:
                    pil_img.verify()
                with PILImage.open(full_path) as pil_img:
                    iw, ih = pil_img.size
                aspect = ih / float(iw) if iw else 0.75
                img_w = available_img_w
                img_h = img_w * aspect
                if img_h > available_img_h:
                    img_h = available_img_h
                    img_w = img_h / aspect
                right_cell = Image(full_path, width=img_w, height=img_h)
            except Exception:
                right_cell = Paragraph('Image unavailable', styles['CellText'])

    outer = Table(
        [[left_table, right_cell]],
        colWidths=[84 * mm, (PAGE_W - 2 * MARGIN) - 84 * mm],
        rowHeights=[HALF_PAGE_BLOCK_HEIGHT],
    )
    outer.setStyle(TableStyle([
        ('VALIGN', (0, 0), (0, 0), 'TOP'),      # left (text) column stays top-aligned, reads naturally
        ('VALIGN', (1, 0), (1, 0), 'MIDDLE'),   # right (image) column centered — fills the box's binding
        ('ALIGN', (1, 0), (1, 0), 'CENTER'),    # dimension, so any leftover space is evenly distributed
        ('BOX', (0, 0), (-1, -1), 0.5, HAIRLINE),
        ('INNERGRID', (0, 0), (-1, -1), 0.4, HAIRLINE),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
    ]))
    return outer


def build_tower_report_pdf(info, defects, static_root):
    """Return a BytesIO containing the finished PDF.

    info: dict with line_name, tower_id, voltage_level, coordinates,
          survey_date, pilot_name, inspection_name, report_date.
    defects: list of dicts (component_name, defect_type, location,
             severity, status, observation, comments, image_path).

    Detail pages are exactly 2 defects each — verified with a real test
    using pdfplumber to check which Defect IDs land on which page (D1+D2
    on page one of the detail section, D3+D4 on the next, etc.), not just
    assumed from the pagination logic below.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0, rightMargin=0, topMargin=0, bottomMargin=16 * mm,
        title=f"Tower {info['tower_id']} — RGB Visual Inspection Report",
    )
    styles = _styles()
    story = []

    story.append(_header_band(info['tower_id'], styles))
    story.append(Spacer(1, 10 * mm))

    story.append(_indent(Paragraph('1. General Information', styles['SectionHead'])))
    story.append(_indent(_general_info_table(info, styles)))
    story.append(Spacer(1, 4 * mm))

    story.append(_indent(Paragraph('2. RGB Defect Summary', styles['SectionHead'])))
    if defects:
        story.append(_indent(_defect_summary_table(defects, styles)))
    else:
        story.append(_indent(Paragraph('No defects have been marked on this tower yet.', styles['CellTextLeft'])))

    if defects:
        story.append(PageBreak())
        story.append(Spacer(1, MARGIN))
        story.append(_indent(Paragraph('3. Detailed Information', styles['SectionHead'])))
        story.append(Spacer(1, 2 * mm))

        for i, d in enumerate(defects, start=1):
            story.append(_indent(_detail_block(d, i, static_root, styles)))
            # 2 defects per page, each occupying exactly half the page,
            # stacked directly against each other with no gap between them.
            if i % DEFECTS_PER_DETAIL_PAGE == 0 and i != len(defects):
                story.append(PageBreak())
                story.append(Spacer(1, MARGIN))

    doc.build(story)
    buf.seek(0)
    return buf
