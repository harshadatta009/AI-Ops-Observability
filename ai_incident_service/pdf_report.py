import html
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class PdfReportRenderer:
    def __init__(self, brand_icon_path: Path) -> None:
        self.brand_icon_path = brand_icon_path
        self.page_width, self.page_height = A4

    def render(self, title: str, report_text: str) -> BytesIO:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=42,
            leftMargin=42,
            topMargin=46,
            bottomMargin=54,
            title=title,
            author="AI Incident Service",
        )

        styles = self._styles()
        story = self._cover_block(title, styles)
        story.extend(self._report_flowables(title, report_text, styles))

        doc.build(
            story,
            onFirstPage=lambda canvas, document: self._draw_page(canvas, document, title, include_header=False),
            onLaterPages=lambda canvas, document: self._draw_page(canvas, document, title, include_header=True),
        )
        buffer.seek(0)
        return buffer

    def _cover_block(self, title: str, styles: dict[str, ParagraphStyle]) -> List:
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logo = self._logo(width=0.34 * inch, height=0.34 * inch)
        brand_text = Paragraph(
            "<b>Varsapradaya AI Observability</b>",
            styles["brand"],
        )
        meta = Paragraph(
            f"<font color='#64748b'>Generated</font><br/><b>{html.escape(generated_at)}</b>",
            styles["meta"],
        )

        header = Table(
            [[logo, brand_text, meta]],
            colWidths=[0.45 * inch, 4.15 * inch, 2.05 * inch],
            hAlign="LEFT",
        )
        header.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )

        title_panel = Table(
            [
                [
                    "",
                    Paragraph("24-hour system health report", styles["eyebrow"]),
                    Paragraph("AI Incident Service", styles["pill"]),
                ],
                ["", Paragraph(html.escape(title), styles["title"]), ""],
                [
                    "",
                    Paragraph(
                        "Executive operational summary for leadership review.",
                        styles["subtitle"],
                    ),
                    "",
                ],
            ],
            colWidths=[0.08 * inch, 4.72 * inch, 1.85 * inch],
            hAlign="LEFT",
        )
        title_panel.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#2563eb")),
                    ("BACKGROUND", (1, 0), (-1, -1), colors.HexColor("#f8fafc")),
                    ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dbeafe")),
                    ("SPAN", (1, 1), (2, 1)),
                    ("TOPPADDING", (0, 0), (-1, -1), 13),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
                    ("LEFTPADDING", (1, 0), (-1, -1), 18),
                    ("RIGHTPADDING", (1, 0), (-1, -1), 18),
                    ("LEFTPADDING", (0, 0), (0, -1), 0),
                    ("RIGHTPADDING", (0, 0), (0, -1), 0),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )

        context_cards = Table(
            [
                [
                    Paragraph("<font color='#64748b'>Scope</font><br/><b>Last 24 hours</b>", styles["card"]),
                    Paragraph("<font color='#64748b'>Signals</font><br/><b>Prometheus + Loki</b>", styles["card"]),
                    Paragraph("<font color='#64748b'>Audience</font><br/><b>Executive + SRE</b>", styles["card"]),
                ]
            ],
            colWidths=[2.12 * inch, 2.12 * inch, 2.12 * inch],
            hAlign="LEFT",
        )
        context_cards.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]
            )
        )

        return [
            header,
            Spacer(1, 0.12 * inch),
            HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#e2e8f0")),
            Spacer(1, 0.2 * inch),
            title_panel,
            Spacer(1, 0.14 * inch),
            context_cards,
            Spacer(1, 0.22 * inch),
        ]

    def _report_flowables(
        self,
        title: str,
        report_text: str,
        styles: dict[str, ParagraphStyle],
    ) -> List:
        flowables: List = []
        bullet_items: List[str] = []
        table_lines: List[str] = []
        current_section = 0

        def flush_bullets() -> None:
            if not bullet_items:
                return
            rows = [
                [
                    Paragraph("<font color='#2563eb'><b>-</b></font>", styles["bullet_marker"]),
                    Paragraph(self._inline_markup(item), styles["body"]),
                ]
                for item in bullet_items
            ]
            bullet_table = Table(
                rows,
                colWidths=[0.16 * inch, 6.18 * inch],
                hAlign="LEFT",
            )
            bullet_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ]
                )
            )
            flowables.append(bullet_table)
            flowables.append(Spacer(1, 0.05 * inch))
            bullet_items.clear()

        def flush_table() -> None:
            if not table_lines:
                return
            table = self._markdown_table(table_lines, styles)
            if table is not None:
                flowables.append(table)
                flowables.append(Spacer(1, 0.12 * inch))
            table_lines.clear()

        for raw_line in report_text.splitlines():
            clean_line = self._clean_line(raw_line)
            if not clean_line:
                flush_bullets()
                flush_table()
                flowables.append(Spacer(1, 0.05 * inch))
                continue
            if self._is_separator(clean_line) or self._is_duplicate_title(clean_line, title):
                continue
            if self._is_table_line(clean_line):
                flush_bullets()
                table_lines.append(clean_line)
                continue

            bullet_match = re.match(r"^(?:[-*]|\d+[.)])\s+(.*)$", clean_line)
            if bullet_match and not self._is_heading(clean_line):
                flush_table()
                bullet_items.append(bullet_match.group(1))
                continue

            flush_bullets()
            flush_table()
            if self._is_heading(clean_line):
                current_section += 1
                flowables.append(Spacer(1, 0.08 * inch if current_section > 1 else 0))
                flowables.append(Paragraph(self._heading_text(clean_line), styles["section_heading"]))
            else:
                flowables.append(Paragraph(self._inline_markup(clean_line), styles["body"]))

        flush_bullets()
        flush_table()
        return flowables

    def _markdown_table(
        self,
        table_lines: List[str],
        styles: dict[str, ParagraphStyle],
    ):
        rows = []
        for line in table_lines:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            rows.append(cells)

        if len(rows) < 2:
            return None

        column_count = max(len(row) for row in rows)
        normalized_rows = [
            row + [""] * (column_count - len(row))
            for row in rows
        ]
        available_width = self.page_width - 84
        first_weight = 1.55 if column_count >= 4 else 1.25
        remaining_weight = max(column_count - 1, 1)
        unit = available_width / (first_weight + remaining_weight)
        column_widths = [first_weight * unit] + [unit] * (column_count - 1)

        table_data = []
        for row_index, row in enumerate(normalized_rows):
            style = styles["table_header"] if row_index == 0 else styles["table_cell"]
            table_data.append([Paragraph(self._inline_markup(cell), style) for cell in row])

        table = Table(table_data, colWidths=column_widths, hAlign="LEFT", repeatRows=1)
        table_style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111827")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.0),
            ("LEADING", (0, 0), (-1, -1), 10.2),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#2563eb")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7dee8")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ]
        for row_index, row in enumerate(normalized_rows[1:], start=1):
            status_text = " ".join(row).upper()
            if "[CRITICAL]" in status_text:
                table_style.append(("TEXTCOLOR", (-1, row_index), (-1, row_index), colors.HexColor("#991b1b")))
            elif "[HIGH]" in status_text:
                table_style.append(("TEXTCOLOR", (-1, row_index), (-1, row_index), colors.HexColor("#9a3412")))
            elif "[MEDIUM]" in status_text:
                table_style.append(("TEXTCOLOR", (-1, row_index), (-1, row_index), colors.HexColor("#92400e")))
        table.setStyle(TableStyle(table_style))
        return table

    def _draw_page(self, canvas, document, title: str, include_header: bool) -> None:
        canvas.saveState()
        left = document.leftMargin
        right = self.page_width - document.rightMargin
        top = self.page_height - 34

        if include_header:
            if self.brand_icon_path.exists():
                canvas.drawImage(
                    str(self.brand_icon_path),
                    left,
                    top - 16,
                    width=18,
                    height=18,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            canvas.setFont("Helvetica-Bold", 8.5)
            canvas.setFillColor(colors.HexColor("#0f172a"))
            canvas.drawString(left + 26, top - 9, "Varsapradaya AI Observability")

            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#64748b"))
            canvas.drawRightString(right, top - 9, title[:72])
            canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
            canvas.setLineWidth(0.6)
            canvas.line(left, top - 24, right, top - 24)

        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawString(left, 28, "Confidential - operational report")
        canvas.drawRightString(right, 28, f"Page {document.page}")
        canvas.restoreState()

    def _logo(self, width: float, height: float):
        if self.brand_icon_path.exists():
            return Image(str(self.brand_icon_path), width=width, height=height, kind="proportional")
        return Paragraph("<b>V</b>", self._styles()["fallback_logo"])

    @staticmethod
    def _styles() -> dict[str, ParagraphStyle]:
        base = getSampleStyleSheet()
        return {
            "brand": ParagraphStyle(
                "Brand",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9.5,
                leading=12,
                textColor=colors.HexColor("#0f172a"),
            ),
            "meta": ParagraphStyle(
                "Meta",
                parent=base["BodyText"],
                alignment=TA_LEFT,
                fontName="Helvetica",
                fontSize=8.2,
                leading=12,
                textColor=colors.HexColor("#0f172a"),
            ),
            "eyebrow": ParagraphStyle(
                "Eyebrow",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=8.2,
                leading=11,
                textColor=colors.HexColor("#2563eb"),
                uppercase=True,
            ),
            "pill": ParagraphStyle(
                "Pill",
                parent=base["BodyText"],
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
                fontSize=8,
                leading=10,
                textColor=colors.HexColor("#475569"),
            ),
            "title": ParagraphStyle(
                "ReportTitle",
                parent=base["Title"],
                alignment=TA_LEFT,
                fontName="Helvetica-Bold",
                fontSize=21,
                leading=26,
                spaceBefore=8,
                textColor=colors.HexColor("#0f172a"),
            ),
            "subtitle": ParagraphStyle(
                "Subtitle",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9.2,
                leading=12,
                textColor=colors.HexColor("#64748b"),
            ),
            "card": ParagraphStyle(
                "ContextCard",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=8.5,
                leading=12,
                textColor=colors.HexColor("#0f172a"),
            ),
            "section_heading": ParagraphStyle(
                "SectionHeading",
                parent=base["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=12.3,
                leading=15.5,
                spaceBefore=10,
                spaceAfter=6,
                textColor=colors.HexColor("#0f172a"),
                keepWithNext=1,
            ),
            "body": ParagraphStyle(
                "ReportBody",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=9.0,
                leading=13.2,
                spaceAfter=4.5,
                textColor=colors.HexColor("#334155"),
            ),
            "bullet_marker": ParagraphStyle(
                "BulletMarker",
                parent=base["BodyText"],
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
                fontSize=9.0,
                leading=13.2,
                textColor=colors.HexColor("#2563eb"),
            ),
            "table_header": ParagraphStyle(
                "TableHeader",
                parent=base["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=7.8,
                leading=10,
                textColor=colors.white,
            ),
            "table_cell": ParagraphStyle(
                "TableCell",
                parent=base["BodyText"],
                fontName="Helvetica",
                fontSize=7.7,
                leading=10,
                textColor=colors.HexColor("#334155"),
            ),
            "fallback_logo": ParagraphStyle(
                "FallbackLogo",
                parent=base["BodyText"],
                alignment=TA_CENTER,
                fontName="Helvetica-Bold",
                fontSize=18,
                leading=20,
                textColor=colors.HexColor("#0f172a"),
            ),
        }

    @staticmethod
    def _clean_line(value: str) -> str:
        return value.strip().replace("\u2011", "-").replace("\u2013", "-").replace("\u2014", "-")

    @staticmethod
    def _is_separator(value: str) -> bool:
        return bool(re.fullmatch(r"[-_=]{4,}", value.strip()))

    @staticmethod
    def _is_table_line(value: str) -> bool:
        return value.startswith("|") and value.endswith("|") and value.count("|") >= 2

    @staticmethod
    def _is_duplicate_title(value: str, title: str) -> bool:
        normalized_value = re.sub(r"[^a-z0-9]+", "", value.lower())
        normalized_title = re.sub(r"[^a-z0-9]+", "", title.lower())
        return normalized_value in {normalized_title, normalized_title + "report"}

    @staticmethod
    def _is_heading(value: str) -> bool:
        stripped = value.strip().strip("*# ")
        if len(stripped) > 100:
            return False
        return bool(
            re.match(r"^\d+\.\s+[A-Za-z]", stripped)
            or re.match(r"^[A-Z][A-Za-z /&-]+:$", stripped)
            or re.match(r"^\*\*[^*]+\*\*$", value.strip())
            or re.match(r"^#{1,3}\s+", value.strip())
        )

    @staticmethod
    def _heading_text(value: str) -> str:
        value = re.sub(r"^#{1,3}\s+", "", value.strip())
        value = re.sub(r"^\s*\d+\.\s*", "", value)
        value = value.strip().strip("*: ")
        return html.escape(value)

    @classmethod
    def _inline_markup(cls, value: str) -> str:
        value = cls._clean_line(value)
        value = re.sub(r"^\s*\d+[.)]\s+", "", value)
        value = re.sub(r"^\s*[-*]\s+", "", value)

        parts = re.split(r"(\*\*.*?\*\*|`.*?`|\[(?:LOW|MEDIUM|HIGH|CRITICAL)\])", value)
        rendered = []
        for part in parts:
            if part.startswith("**") and part.endswith("**"):
                rendered.append(f"<b>{html.escape(part[2:-2].strip())}</b>")
            elif part.startswith("`") and part.endswith("`"):
                rendered.append(f"<font name='Courier'>{html.escape(part[1:-1])}</font>")
            elif part in {"[LOW]", "[MEDIUM]", "[HIGH]", "[CRITICAL]"}:
                rendered.append(cls._severity_label(part))
            else:
                rendered.append(html.escape(part))
        return "".join(rendered)

    @staticmethod
    def _severity_label(value: str) -> str:
        colors_by_label = {
            "[LOW]": "#166534",
            "[MEDIUM]": "#92400e",
            "[HIGH]": "#9a3412",
            "[CRITICAL]": "#991b1b",
        }
        return (
            f"<font color='{colors_by_label[value]}'><b>{html.escape(value)}</b></font>"
        )
