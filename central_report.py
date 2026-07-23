"""
central_report.py — One combined PDF covering every project across every
module (Transmission Line, Land Survey, TRANS, and 3D Inspection) that the
requesting user can actually see.

Mirrors trans_report.py / tower_report.py's structure and libraries
(ReportLab, already a dependency). The two different "project" concepts
this app has — the generic Project model and the separate ChimneyProject
model — are gathered into one common shape before laying anything out, so
the actual PDF-building code doesn't need to know which kind of project
it's looking at.
"""
from io import BytesIO
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
)
from reportlab.lib.enums import TA_CENTER

PAGE_W, PAGE_H = A4


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='CoverTitle', fontSize=22, leading=27, alignment=TA_CENTER,
                               fontName='Helvetica-Bold', textColor=colors.HexColor('#1b1e24')))
    styles.add(ParagraphStyle(name='CoverSubtitle', fontSize=11, leading=15, alignment=TA_CENTER,
                               textColor=colors.HexColor('#555555')))
    styles.add(ParagraphStyle(name='ModuleHeading', fontSize=15, leading=19, fontName='Helvetica-Bold',
                               textColor=colors.HexColor('#1b1e24'), spaceBefore=6, spaceAfter=10))
    styles.add(ParagraphStyle(name='ProjectHeading', fontSize=12, leading=16, fontName='Helvetica-Bold',
                               textColor=colors.HexColor('#1b1e24'), spaceBefore=12, spaceAfter=4))
    styles.add(ParagraphStyle(name='CellTextLeft', fontSize=9, leading=12, alignment=0))
    return styles


def _cover_page(story, styles, project_summaries):
    total_projects = len(project_summaries)
    total_defects = sum(p['total_defects'] for p in project_summaries)
    by_module = {}
    for p in project_summaries:
        by_module.setdefault(p['module'], {'projects': 0, 'defects': 0})
        by_module[p['module']]['projects'] += 1
        by_module[p['module']]['defects'] += p['total_defects']

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph('CENTRAL INSPECTION REPORT', styles['CoverTitle']))
    story.append(Paragraph('All Modules — Combined Summary', styles['CoverTitle']))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"DROGO AEROSPACE — Generated {datetime.utcnow().strftime('%d %b %Y')}", styles['CoverSubtitle']))
    story.append(Spacer(1, 12 * mm))

    stat_table = Table(
        [[Paragraph(f'<b>{v}</b>', ParagraphStyle('sv', fontSize=18, alignment=TA_CENTER, fontName='Helvetica-Bold'))
          for v in (total_projects, total_defects, len(by_module))],
         [Paragraph(k, ParagraphStyle('sl', fontSize=8.5, alignment=TA_CENTER, textColor=colors.HexColor('#666666')))
          for k in ('Total Projects', 'Total Defects', 'Modules')]],
        colWidths=[(PAGE_W - 28 * mm) / 3] * 3,
    )
    stat_table.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.75, colors.HexColor('#c5cad4')),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e5ea')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f4f6f9')),
        ('TOPPADDING', (0, 0), (-1, -1), 10), ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(stat_table)
    story.append(Spacer(1, 10 * mm))

    rows = [['Module', 'Projects', 'Defects']] + [
        [m, str(v['projects']), str(v['defects'])] for m, v in sorted(by_module.items())
    ]
    module_table = Table(rows, colWidths=[(PAGE_W - 28 * mm) * 0.5, (PAGE_W - 28 * mm) * 0.25, (PAGE_W - 28 * mm) * 0.25])
    module_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1b1e24')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#c5cad4')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f6f9')]),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(module_table)
    story.append(PageBreak())


def _project_section(story, styles, p):
    story.append(Paragraph(p['name'], styles['ProjectHeading']))
    rows = [['Module', p['module']], ['Total Defects', str(p['total_defects'])]]
    if 'division_count' in p:
        rows += [['Divisions', str(p['division_count'])], ['Lines', str(p['line_count'])],
                  ['Towers', str(p['tower_count'])]]
    if 'open_count' in p:
        rows += [['Open', str(p['open_count'])], ['Closed', str(p['closed_count'])]]
    sev = p.get('severity_counts', {})
    if sev:
        rows.append(['Severity', ', '.join(f'{k}: {v}' for k, v in sev.items())])

    table = Table(
        [[Paragraph(f'<b>{k}</b>', styles['CellTextLeft']), Paragraph(str(v), styles['CellTextLeft'])] for k, v in rows],
        colWidths=[40 * mm, PAGE_W - 28 * mm - 40 * mm],
    )
    table.setStyle(TableStyle([
        ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LINEBELOW', (0, 0), (-1, -1), 0.4, colors.HexColor('#e2e5ea')),
    ]))
    story.append(table)
    story.append(Spacer(1, 4 * mm))


def build_central_report_pdf(project_summaries):
    """project_summaries: list of dicts, each either the shape produced by
    projects_routes._build_project_defect_summary() (has division_count/
    line_count/tower_count) plus 'name'/'module', or the chimney-project
    shape (has open_count/closed_count) — see gather functions in
    assistant_api.py's generate_central_report tool."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                             topMargin=16 * mm, bottomMargin=14 * mm,
                             title='DROGO AEROSPACE — Central Inspection Report')
    styles = _styles()
    story = []
    _cover_page(story, styles, project_summaries)

    by_module = {}
    for p in project_summaries:
        by_module.setdefault(p['module'], []).append(p)

    for module_name in sorted(by_module.keys()):
        story.append(Paragraph(module_name, styles['ModuleHeading']))
        for p in by_module[module_name]:
            _project_section(story, styles, p)
        story.append(PageBreak())

    doc.build(story)
    buf.seek(0)
    return buf
