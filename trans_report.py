"""
trans_report.py — Builds the TRANS module's defect report PDF.

Mirrors chimney_report.py's structure and libraries (ReportLab — already a
dependency, nothing new to install) — same overall shape, adapted for this
module's data: a Project with Divisions -> Lines -> tower photos ->
defects, rather than a single chimney's own defect list.

Layout:
    Page 1  — cover page: project name, client, state, timeline, and
              division/line/tower/defect counts, plus a severity summary.
    Page 2+ — defects grouped BY TOWER (division -> line -> tower), each
              tower getting its own small heading and table — mirrors the
              "All Defects — by Tower" layout on the web Overview page,
              rather than one flat undifferentiated list.
"""
import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT

PAGE_W, PAGE_H = A4  # portrait

SEVERITY_COLORS = {
    'Minor':    colors.HexColor('#1f9d68'),
    'Major':    colors.HexColor('#b9821f'),
    'Critical': colors.HexColor('#c94b42'),
}


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
        name='CoverDetailLabel', fontSize=9.5, leading=13, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1b1e24')))
    styles.add(ParagraphStyle(
        name='CoverDetailValue', fontSize=9.5, leading=13, alignment=TA_LEFT,
        textColor=colors.HexColor('#333333')))
    styles.add(ParagraphStyle(
        name='TowerHeading', fontSize=11.5, leading=15, fontName='Helvetica-Bold',
        textColor=colors.HexColor('#1b1e24'), spaceBefore=14, spaceAfter=4))
    styles.add(ParagraphStyle(
        name='TowerSubheading', fontSize=9, leading=12, textColor=colors.HexColor('#666666'),
        spaceAfter=6))
    return styles


# ── Cover page ────────────────────────────────────────────────────────────

def _build_cover_page(project, summary, styles):
    story = []
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph('TRANSMISSION LINE', styles['CoverTitle']))
    story.append(Paragraph('INSPECTION REPORT', styles['CoverTitle']))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f'DROGO AEROSPACE — {project.module}', styles['CoverSubtitle']))
    story.append(Spacer(1, 12 * mm))

    detail_rows = [
        ['Project', project.name or '—'],
        ['Client', project.client_name or '—'],
        ['State', project.state or '—'],
        ['Timeline', project.timeline or '—'],
    ]
    detail_table = Table(
        [[Paragraph(f'<b>{k}</b>', styles['CoverDetailLabel']), Paragraph(v, styles['CoverDetailValue'])]
         for k, v in detail_rows],
        colWidths=[40 * mm, PAGE_W - 28 * mm - 40 * mm],
    )
    detail_table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#e2e5ea')),
    ]))
    story.append(detail_table)
    story.append(Spacer(1, 10 * mm))

    # Counts + severity summary as a row of stat boxes.
    stat_cells = [
        ('Divisions', summary['division_count']),
        ('Lines', summary['line_count']),
        ('Towers', summary['tower_count']),
        ('Defects', summary['total_defects']),
    ]
    stat_table = Table(
        [[Paragraph(f"<b>{v}</b>", ParagraphStyle('sv', fontSize=18, alignment=TA_CENTER,
                                                    fontName='Helvetica-Bold', textColor=colors.HexColor('#1b1e24')))
          for _, v in stat_cells],
         [Paragraph(k, ParagraphStyle('sl', fontSize=8.5, alignment=TA_CENTER, textColor=colors.HexColor('#666666')))
          for k, _ in stat_cells]],
        colWidths=[(PAGE_W - 28 * mm) / 4] * 4,
    )
    stat_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#c5cad4')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e5ea')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f4f6f9')),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(stat_table)
    story.append(Spacer(1, 8 * mm))

    sev = summary['severity_counts']
    sev_row = ' &nbsp;&nbsp;|&nbsp;&nbsp; '.join(
        f'<font color="{SEVERITY_COLORS.get(s, colors.black).hexval()}"><b>{s}: {sev.get(s, 0)}</b></font>'
        for s in ('Critical', 'Major', 'Minor')
    )
    story.append(Paragraph(f'Severity breakdown &nbsp;—&nbsp; {sev_row}', styles['CoverSubtitle']))
    story.append(PageBreak())
    return story


# ── Per-defect row (used inside each tower's table) ─────────────────────

def _defect_row(defect_dict, static_root, styles, seq_no):
    sev = defect_dict.get('severity') or 'Minor'
    sev_color = SEVERITY_COLORS.get(sev, colors.HexColor('#333333'))
    sev_para = Paragraph(f'<font color="{sev_color.hexval()}"><b>{sev}</b></font>', styles['CellText'])

    defect_id_para   = Paragraph(f"<b>#{seq_no}</b>", styles['CellText'])
    component_para   = Paragraph(defect_dict.get('component_name') or '—', styles['CellText'])
    location_para    = Paragraph(defect_dict.get('location') or '—', styles['CellText'])
    observation_para = Paragraph(defect_dict.get('observation') or '—', styles['CellText'])

    img_cell = Paragraph('No image', styles['CellText'])
    image_path = defect_dict.get('image_path') or ''
    if image_path:
        full_path = os.path.join(static_root, image_path)
        if os.path.exists(full_path):
            try:
                from PIL import Image as PILImage
                with PILImage.open(full_path) as pil_img:
                    pil_img.verify()
                img_cell = Image(full_path, width=22 * mm, height=16 * mm)
            except Exception:
                img_cell = Paragraph('Image unavailable', styles['CellText'])

    return [defect_id_para, component_para, sev_para, location_para, observation_para, img_cell]


def _build_tower_section(group, static_root, styles, seq_counter):
    """One tower's heading + defect table. Returns a list of flowables,
    wrapped in KeepTogether when small enough to reasonably fit on one
    page together — a tower with many defects is still allowed to split
    across a page break rather than being forced to fit."""
    header_cols = ['#', 'Component', 'Severity', 'Location', 'Observation', 'Image']
    col_widths = [10 * mm, 34 * mm, 20 * mm, 22 * mm, 60 * mm, 26 * mm]

    heading = Paragraph(f"Tower {group['tower_label']}", styles['TowerHeading'])
    subheading = Paragraph(
        f"{group['division_name']} &nbsp;·&nbsp; {group['line_name']} &nbsp;·&nbsp; "
        f"{len(group['defects'])} defect{'s' if len(group['defects']) != 1 else ''}",
        styles['TowerSubheading'])

    table_data = [header_cols]
    for d in group['defects']:
        seq_counter[0] += 1
        table_data.append(_defect_row(d, static_root, styles, seq_counter[0]))

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

    section = [heading, subheading, table]
    # Only force-together small groups — a huge one is better allowed to
    # paginate naturally than pushed onto its own mostly-empty page.
    if len(group['defects']) <= 6:
        return [KeepTogether(section)]
    return section


def build_trans_report_pdf(project, summary, static_root):
    """Return a BytesIO containing the finished PDF.

    `summary` is the dict from projects_routes._build_project_defect_summary()
    — the exact same aggregation the web Overview page uses, so the report
    can never show different numbers than what's on screen.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f'{project.name} — Transmission Line Inspection Report',
    )
    styles = _styles()
    story = []

    story.extend(_build_cover_page(project, summary, styles))

    story.append(Paragraph('DROGO AEROSPACE — Defects by Tower', styles['ReportTitle']))
    story.append(Paragraph(f"Total findings: {summary['total_defects']}", styles['ReportSubtitle']))
    story.append(Spacer(1, 4 * mm))

    if not summary['tower_groups']:
        story.append(Paragraph('No defects have been marked on any tower photo yet.', styles['CellTextLeft']))
    else:
        seq_counter = [0]  # mutable int, shared across tower sections for continuous numbering
        for group in summary['tower_groups']:
            story.extend(_build_tower_section(group, static_root, styles, seq_counter))

    doc.build(story)
    buf.seek(0)
    return buf
