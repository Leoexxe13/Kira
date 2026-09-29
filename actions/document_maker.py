import os
import json
import traceback
from datetime import datetime
from pathlib import Path

try:
    import docx
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
except ImportError:
    docx = None

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.utils import get_column_letter
except ImportError:
    openpyxl = None

try:
    import pptx
    from pptx.util import Inches as PptxInches, Pt as PptxPt
    from pptx.dml.color import RGBColor as PptxRGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
except ImportError:
    pptx = None



def _apply_pptx_theme(prs, theme_name):
    # This is a conceptual theme applicator for testing structure
    pass

def _structural_qa_pptx(content_list):
    """Validates that slides are not saturated (Visual QA)."""
    for idx, slide in enumerate(content_list):
        text_len = 0
        bullets = 0
        if isinstance(slide.get('content'), list):
            bullets = len(slide.get('content'))
            text_len = sum(len(str(c)) for c in slide.get('content'))
        elif isinstance(slide.get('content'), str):
            text_len = len(slide.get('content'))
            bullets = slide.get('content').count('\n') + 1

        if bullets > 6:
            raise ValueError(f"Visual QA Failed: Slide {idx+1} ('{slide.get('title')}') is saturated with too many items ({bullets} > 6). Split into multiple slides.")
        if text_len > 350:
            raise ValueError(f"Visual QA Failed: Slide {idx+1} ('{slide.get('title')}') has too much text ({text_len} chars). Split it to maintain visual quality.")

def handle_action(args: dict, context: dict = None) -> str:
    action = args.get("action", "")
    format_opts = args.get("format_options", {})
    file_path = args.get("file_path", "")
    import os
    if file_path: file_path = os.path.expanduser(file_path)
    title = args.get("title", "Document")
    theme = args.get("theme", "light").lower()
    
    content_raw = args.get("content", "[]")
    if isinstance(content_raw, str):
        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError:
            content = [{"type": "paragraph", "text": content_raw}]
    else:
        content = content_raw

    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    
    try:
        if action == 'create_txt' or action == 'create_md':
            with open(file_path, 'w', encoding='utf-8') as f:
                if isinstance(content, list):
                    for item in content:
                        f.write(str(item.get('text', item)) + "\n")
                else:
                    f.write(str(content))
        
        elif action == 'create_docx':
            if not docx: return "Error: python-docx not installed."
            doc = docx.Document()
            # Premium DOCX features
            section = doc.sections[0]
            header = section.header
            header.paragraphs[0].text = f"{title} - Generado por KIRA"
            
            doc.add_heading(title, 0)
            if isinstance(content, list):
                for item in content:
                    t = item.get('type', 'paragraph')
                    text = item.get('text', '')
                    if t == 'heading':
                        doc.add_heading(text, level=item.get('level', 1))
                    elif t == 'list':
                        doc.add_paragraph(text, style='List Bullet')
                    elif t == 'quote':
                        p = doc.add_paragraph(text, style='Quote')
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif t == 'table':
                        rows = item.get('data', [])
                        if rows:
                            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                            table.style = 'Light Shading Accent 1'
                            for i, row in enumerate(rows):
                                for j, cell in enumerate(row):
                                    table.cell(i, j).text = str(cell)
                    else:
                        doc.add_paragraph(text)
            doc.save(file_path)

        elif action == 'create_xlsx':
            if not openpyxl: return "Error: openpyxl not installed."
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = title[:31]
            
            calc_cols = format_opts.get('calculated_columns', {})
            totals_row = format_opts.get('totals_row', False)
            curr_cols = format_opts.get('currency_cols', [])
            
            # Helper to convert col letter to index (1-based)
            def col_letter_to_index(letter):
                return sum((ord(c) - 64) * (26 ** i) for i, c in enumerate(reversed(letter.upper())))
                
            currency_indices = [col_letter_to_index(c) for c in curr_cols]
            
            if isinstance(content, str):
                import csv, io
                # 1. Try parsing as JSON first
                try:
                    parsed_json = json.loads(content)
                    if isinstance(parsed_json, dict):
                        # If it's a dict like {"Sheet1": [...]}, take the first value
                        content = list(parsed_json.values())[0]
                    elif isinstance(parsed_json, list):
                        content = parsed_json
                except Exception:
                    # 2. Fallback to CSV
                    try:
                        reader = csv.reader(io.StringIO(content.strip()))
                        content = list(reader)
                    except Exception:
                        content = [[content]]
                    
            if isinstance(content, list) and len(content) > 0:
                # Convert list of dicts to list of lists
                if isinstance(content[0], dict):
                    headers = list(content[0].keys())
                    new_content = [headers]
                    for row_dict in content:
                        new_content.append([row_dict.get(h, "") for h in headers])
                    content = new_content
            
            if isinstance(content, list):
                for row_idx, row in enumerate(content, 1):
                    if isinstance(row, list):
                        for col_idx, val in enumerate(row, 1):
                            cell = ws.cell(row=row_idx, column=col_idx, value=val)
                            
                            # Apply formulas if row > 1 and column has a calculated formula
                            c_letter = get_column_letter(col_idx)
                            if row_idx > 1 and c_letter in calc_cols:
                                f_str = calc_cols[c_letter].replace('{row}', str(row_idx))
                                cell.value = f_str
                                
                            # Premium header styling
                            if row_idx == 1:
                                cell.font = Font(bold=True, color="FFFFFF")
                                cell.fill = PatternFill(start_color="1F4E78", fill_type="solid")
                                cell.alignment = Alignment(horizontal="center")
                                
                            # Formatting
                            if row_idx > 1 and col_idx in currency_indices:
                                cell.number_format = '$#,##0.00'
                                
                # Add totals row
                if totals_row and len(content) > 1:
                    total_row_idx = len(content) + 1
                    ws.cell(row=total_row_idx, column=1, value="TOTAL").font = Font(bold=True)
                    for c_letter in calc_cols:
                        c_idx = col_letter_to_index(c_letter)
                        cell = ws.cell(row=total_row_idx, column=c_idx, value=f"=SUM({c_letter}2:{c_letter}{total_row_idx-1})")
                        cell.font = Font(bold=True)
                        if c_idx in currency_indices:
                            cell.number_format = '$#,##0.00'
                    
                    # Also sum regular currency columns that aren't calculated
                    for c_idx in currency_indices:
                        c_letter = get_column_letter(c_idx)
                        if c_letter not in calc_cols:
                            cell = ws.cell(row=total_row_idx, column=c_idx, value=f"=SUM({c_letter}2:{c_letter}{total_row_idx-1})")
                            cell.font = Font(bold=True)
                            cell.number_format = '$#,##0.00'

                # Auto filter, freeze panes, and column width
                if len(content) > 1:
                    # Width adjustment
                    for col in ws.columns:
                        max_length = 0
                        column = col[0].column_letter
                        for cell in col:
                            try:
                                if len(str(cell.value)) > max_length:
                                    max_length = len(str(cell.value))
                            except: pass
                        ws.column_dimensions[column].width = min(max_length + 2, 50)
                    
                    ws.auto_filter.ref = f"A1:{get_column_letter(len(content[0]))}{len(content)}"
                    ws.freeze_panes = "A2"
            wb.save(file_path)

        elif action == 'create_pptx':
            if not pptx: return "Error: python-pptx not installed."
            
            # VISUAL QA LOOP (Pre-render)
            _structural_qa_pptx(content)
            
            prs = pptx.Presentation()
            _apply_pptx_theme(prs, theme)
            
            # Portada
            title_slide = prs.slides.add_slide(prs.slide_layouts[0])
            title_slide.shapes.title.text = title
            title_slide.placeholders[1].text = "Generado por KIRA"
            
            if isinstance(content, list):
                for item in content:
                    layout_type = item.get('layout', 'content')
                    
                    if layout_type == 'section':
                        slide = prs.slides.add_slide(prs.slide_layouts[2]) # Section header
                        slide.shapes.title.text = item.get('title', 'Sección')
                    
                    elif layout_type == 'quote':
                        slide = prs.slides.add_slide(prs.slide_layouts[1])
                        slide.shapes.title.text = item.get('title', '')
                        tf = slide.placeholders[1].text_frame
                        tf.text = f'"{item.get("content", [""])[0]}"'
                        for p in tf.paragraphs:
                            p.font.italic = True
                            p.alignment = PP_ALIGN.CENTER
                            
                    elif layout_type == 'chart':
                        slide = prs.slides.add_slide(prs.slide_layouts[5]) # Title only
                        slide.shapes.title.text = item.get('title', 'Gráfico')
                        visual = item.get('visual', {})
                        chart_data = CategoryChartData()
                        chart_data.categories = visual.get('categories', ['A', 'B'])
                        chart_data.add_series('Serie 1', visual.get('series', [1, 2]))
                        x, y, cx, cy = PptxInches(2), PptxInches(2), PptxInches(6), PptxInches(4.5)
                        slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, x, y, cx, cy, chart_data)
                        
                    else:
                        # Default content / two_column
                        slide = prs.slides.add_slide(prs.slide_layouts[1])
                        slide.shapes.title.text = item.get('title', 'Slide')
                        tf = slide.placeholders[1].text_frame
                        bullets = item.get('content', [])
                        if isinstance(bullets, list):
                            for i, bullet in enumerate(bullets):
                                p = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
                                p.text = str(bullet)
                        elif isinstance(bullets, str):
                            tf.text = bullets
                            
            prs.save(file_path)

        else:
            return f"Error: formato {action} no soportado."
            
    except ValueError as ve:
        # Expected QA rejections
        return str(ve)
    except Exception as e:
        return f"Error creating file: {str(e)}\n{traceback.format_exc()}"

    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return f"Error: La verificación del archivo falló. El archivo podría estar corrupto o vacío."

    deliverable = {
        "status": "DELIVERABLE_CREATED",
        "type": action,
        "title": title,
        "path": file_path,
        "metadata": args.get("metadata", {}),
        "created_at": datetime.now().isoformat()
    }
    
    return json.dumps(deliverable)


TOOL = {
    "name": "document_maker",
    "description": (
        "Creates PREMIUM formatted deliverables: DOCX, XLSX, PPTX, TXT, MD, CSV. "
        "For PPTX, use structural 'layout' (title, section, content, two_column, comparison, quote, chart, image). "
        "For PPTX 'theme', use 'light', 'dark', or 'kira'. "
        "Do NOT overload slides with text. If a slide is saturated, the Visual QA will REJECT it and you must split it. "
        "Returns a Deliverable result."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "The format: 'create_docx', 'create_xlsx', 'create_pptx', 'create_txt', 'create_md'"
            },
            "file_path": {
                "type": "STRING",
                "description": "Absolute path where to save the file."
            },
            "title": {
                "type": "STRING",
                "description": "Title of the deliverable."
            },
            "theme": {
                "type": "STRING",
                "description": "Visual theme (light, dark, kira) mainly for PPTX."
            },
            "metadata": {
                "type": "OBJECT",
                "description": "Optional metadata for the Deliverable Card (e.g., {'Visual QA': 'Verificado', 'Renderer': 'python-pptx'})."
            },
            "format_options": {
                "type": "OBJECT",
                "description": "For XLSX: {'calculated_columns': {'D': '=B{row}*C{row}'}, 'totals_row': True, 'currency_cols': ['C', 'D']}"
            },
            "content": {
                "type": "STRING",
                "description": "JSON string containing the structured content. PPTX slide schema: [{'title': '', 'layout': 'content|two_column|quote|chart', 'content': [], 'visual': {'chart_type': 'bar', 'data': {...}}, 'notes': ''}]."
            }
        },
        "required": ["action", "file_path", "title", "content"]
    },
    "handler": handle_action
}
