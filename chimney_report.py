"""
chimney_report.py — Builds the Chimney Inspection defect report PDF.

Layout:
    Page 1  — cover page: report title, full chimney photo, chimney details
              (asset name / structure type / inspection type / scope).
    Page 2+ — one findings table per page (portrait A4):
                  Defect ID | Element | Type | Severity | Distance from Ground |
                  Location | Coordinates (Direction) | Area | Image
              7 defects per page.
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

DEFECTS_PER_PAGE = 7
PAGE_W, PAGE_H = A4  # portrait


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='CellText', fontSize=7.5, leading=10, alignment=TA_CENTER))
    styles.add(ParagraphStyle(
        name='CellTextLeft', fontSize=8.5, leading=11, alignment=0))
    styles.add(ParagraphStyle(
        name='ReportTitle', fontSize=17, leading=21, spaceAfter=2, fontName='Helvetica-Bold'))
    styles.add(ParagraphStyle(
        name='ReportSubtitle', fontSize=10, leading=13, textColor=colors.HexColor('#555555')))
    styles.add(ParagraphStyle(
        name='CoverTitle', fontSize=22, leading=27, alignment=TA_CENTER,
        fontName='Helvetica-Bold', textColor=colors.HexColor('#1b1e24')))
    styles.add(ParagraphStyle(
        name='CoverSubtitle', fontSize=11.5, leading=15, alignment=TA_CENTER,
        textColor=colors.HexColor('#555555')))
    styles.add(ParagraphStyle(
        name='CoverSectionHead', fontSize=12.5, leading=16, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1b1e24'), spaceBefore=10, spaceAfter=6))
    styles.add(ParagraphStyle(
        name='CoverDetailLabel', fontSize=9.5, leading=13, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1b1e24')))
    styles.add(ParagraphStyle(
        name='CoverDetailValue', fontSize=9.5, leading=13, alignment=TA_LEFT,
        textColor=colors.HexColor('#333333')))
    return styles


# ── Cover page ────────────────────────────────────────────────────────────────

def _build_cover_page(project, defects, static_root, styles, cover_image_path):
    # Water Tank projects will get their own dedicated cover page later —
    # for now every project (regardless of asset_category) uses the
    # chimney cover page, same as before that field existed.
    story = []
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph('DIGITAL INSPECTION AND', styles['CoverTitle']))
    story.append(Paragraph('HEALTH ASSESSMENT REPORT', styles['CoverTitle']))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph('DROGO AEROSPACE — Chimney / Stack Inspection', styles['CoverSubtitle']))
    story.append(Spacer(1, 8 * mm))

    # Full asset photo, sized to fit the page width while keeping its
    # aspect ratio (falls back gracefully if no screenshot was supplied).
    usable_w = PAGE_W - 28 * mm
    img_added = False
    if cover_image_path and os.path.exists(cover_image_path):
        try:
            from PIL import Image as PILImage
            with PILImage.open(cover_image_path) as pil_img:
                pil_img.verify()
            with PILImage.open(cover_image_path) as pil_img:
                iw, ih = pil_img.size
            aspect = ih / float(iw) if iw else 0.6
            img_w = usable_w
            img_h = img_w * aspect
            max_h = 110 * mm
            if img_h > max_h:
                img_h = max_h
                img_w = img_h / aspect
            story.append(Image(cover_image_path, width=img_w, height=img_h, hAlign='CENTER'))
            img_added = True
        except Exception:
            img_added = False

    if not img_added:
        placeholder = Table([[Paragraph('Chimney photo not available', styles['CellText'])]],
                             colWidths=[usable_w], rowHeights=[60 * mm])
        placeholder.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#c5cad4')),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f4f6f9')),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        story.append(placeholder)

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph('Chimney Details', styles['CoverSectionHead']))

    scope = (project.inspection_scope or '').strip() or (
        'Complete visual assessment of the chimney/stack structure — shell, '
        'base and top rim — captured via drone-based 3D digital-twin inspection.'
    )

    detail_rows = [
        ['Asset Name', project.asset_name or '—'],
        ['Type of Structure', project.structure_type or '—'],
        ['Type of Inspection', project.inspection_type or '—'],
        ['Inspection Scope', scope],
        ['Location (Lat, Long)', f"{project.latitude:.5f}, {project.longitude:.5f}"],
        ['Total Findings Recorded', str(len(defects))],
    ]
    table_rows = [
        [Paragraph(label, styles['CoverDetailLabel']), Paragraph(value, styles['CoverDetailValue'])]
        for label, value in detail_rows
    ]
    detail_table = Table(table_rows, colWidths=[45 * mm, usable_w - 45 * mm])
    detail_table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#dfe3ea')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f4f6f9')),
    ]))
    story.append(detail_table)
    story.append(PageBreak())
    return story


# ── Findings table pages ─────────────────────────────────────────────────────

def _defect_row(defect_dict, static_root, styles, seq_no):
    pos = defect_dict.get('position') or {}
    lat = pos.get('lat')
    lon = pos.get('lon')
    coord_str = f"{lat:.5f}, {lon:.5f}" if lat is not None and lon is not None else '—'
    direction = defect_dict.get('direction') or '—'
    coord_para = Paragraph(f"{coord_str}<br/><b>({direction})</b>", styles['CellText'])

    sev = defect_dict.get('severity') or 'Minor'
    sev_colors = {
        'Minor':    colors.HexColor('#1f9d68'),
        'Moderate': colors.HexColor('#b9821f'),
        'Critical': colors.HexColor('#c94b42'),
    }
    sev_color = sev_colors.get(sev, colors.HexColor('#333333'))
    sev_para = Paragraph(f'<font color="{sev_color.hexval()}"><b>{sev}</b></font>', styles['CellText'])

    defect_id_para = Paragraph(f"<b>D{seq_no}</b>", styles['CellText'])
    element_para   = Paragraph(defect_dict.get('title') or '—', styles['CellText'])
    type_para      = Paragraph(defect_dict.get('defect_type') or '—', styles['CellText'])
    height_para    = Paragraph(defect_dict.get('height') or '—', styles['CellText'])
    location_para  = Paragraph(defect_dict.get('location') or '—', styles['CellText'])
    area_para      = Paragraph(defect_dict.get('area') or '—', styles['CellText'])

    img_cell = Paragraph('No image', styles['CellText'])
    image_url = defect_dict.get('image_url') or ''
    if image_url:
        rel = image_url.lstrip('/')
        if rel.startswith('static/'):
            rel = rel[len('static/'):]
        full_path = os.path.join(static_root, rel)
        if os.path.exists(full_path):
            try:
                from PIL import Image as PILImage
                with PILImage.open(full_path) as pil_img:
                    pil_img.verify()
                img_cell = Image(full_path, width=22 * mm, height=16 * mm)
            except Exception:
                img_cell = Paragraph('Image unavailable', styles['CellText'])

    return [defect_id_para, element_para, type_para, sev_para,
            height_para, location_para, coord_para, area_para, img_cell]


def build_defect_report_pdf(project, defects, static_root, cover_image_path=None):
    """Return a BytesIO containing the finished PDF."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f'{project.asset_name} — Chimney Inspection Report',
    )
    styles = _styles()
    story = []

    story.extend(_build_cover_page(project, defects, static_root, styles, cover_image_path))

    header_cols = ['Defect ID', 'Element', 'Type', 'Severity', 'Distance\nfrom Ground',
                   'Location', 'Coordinates\n(Direction)', 'Area', 'Image']
    col_widths = [12 * mm, 24 * mm, 18 * mm, 14 * mm,
                  16 * mm, 20 * mm, 28 * mm, 14 * mm, 26 * mm]

    def make_title_block():
        story.append(Paragraph('DROGO AEROSPACE — Chimney Inspection Report', styles['ReportTitle']))
        story.append(Paragraph(
            f"Asset: <b>{project.asset_name}</b><br/>"
            f"Structure: {project.structure_type or '—'} &nbsp;|&nbsp; "
            f"Inspection: {project.inspection_type or '—'} &nbsp;|&nbsp; "
            f"Location: {project.latitude:.5f}, {project.longitude:.5f}",
            styles['ReportSubtitle']))
        story.append(Paragraph(f"Total findings: {len(defects)}", styles['ReportSubtitle']))
        story.append(Spacer(1, 6 * mm))

    make_title_block()

    if not defects:
        story.append(Paragraph('No defects have been recorded for this chimney yet.', styles['CellTextLeft']))
    else:
        chunks = [defects[i:i + DEFECTS_PER_PAGE] for i in range(0, len(defects), DEFECTS_PER_PAGE)]
        seq_no = 1
        for page_idx, chunk in enumerate(chunks):
            table_data = [header_cols]
            for d in chunk:
                table_data.append(_defect_row(d.to_dict(), static_root, styles, seq_no))
                seq_no += 1

            table = Table(table_data, colWidths=col_widths, repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1b1e24')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 7.5),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#c5cad4')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6f9')]),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]))
            story.append(table)
            if page_idx < len(chunks) - 1:
                story.append(PageBreak())
                make_title_block()

    doc.build(story)
    buf.seek(0)
    return buf
