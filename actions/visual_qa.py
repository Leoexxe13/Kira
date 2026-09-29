import os
import json
import traceback
import tempfile
import subprocess
import shutil

# Detect LibreOffice
def get_libreoffice_path():
    paths = [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for p in paths:
        if os.path.exists(p) and os.access(p, os.X_OK):
            return p
    # Try finding in PATH
    path_lo = shutil.which("soffice")
    if path_lo:
        return path_lo
    return None

def detect_libreoffice():
    lo_path = get_libreoffice_path()
    if not lo_path:
        return {"available": False}
    
    try:
        res = subprocess.run([lo_path, "--version"], capture_output=True, text=True, timeout=5)
        version = res.stdout.strip()
        return {
            "available": True,
            "executable": lo_path,
            "version": version,
            "supported_formats": ["pptx", "docx", "xlsx"]
        }
    except Exception:
        return {"available": True, "executable": lo_path, "version": "unknown"}

def render_to_pdf(file_path: str, out_dir: str, lo_path: str) -> str:
    # Use a temporary user profile so LibreOffice doesn't conflict or break in sandbox
    profile_dir = tempfile.mkdtemp(prefix="lo_profile_")
    try:
        cmd = [
            lo_path,
            f"-env:UserInstallation=file://{profile_dir}",
            "--headless",
            "--convert-to", "pdf",
            "--outdir", out_dir,
            file_path
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=30)
        if res.returncode != 0:
            raise RuntimeError(f"LibreOffice failed: {res.stderr.decode('utf-8', errors='ignore')}")
            
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        pdf_path = os.path.join(out_dir, f"{base_name}.pdf")
        if not os.path.exists(pdf_path):
            raise RuntimeError("PDF file was not created by LibreOffice.")
        return pdf_path
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

def pdf_to_images(pdf_path: str, out_dir: str) -> list[dict]:
    import Quartz
    import objc
    from CoreFoundation import CFURLCreateFromFileSystemRepresentation
    
    url = CFURLCreateFromFileSystemRepresentation(None, pdf_path.encode('utf-8'), len(pdf_path), False)
    pdf = Quartz.CGPDFDocumentCreateWithURL(url)
    if not pdf:
        raise RuntimeError("Failed to open PDF with Quartz.")
        
    pages = Quartz.CGPDFDocumentGetNumberOfPages(pdf)
    results = []
    
    with objc.autorelease_pool():
        for i in range(1, min(pages + 1, 11)): # Max 10 pages to avoid overload
            page = Quartz.CGPDFDocumentGetPage(pdf, i)
            rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
            
            width = int(Quartz.CGRectGetWidth(rect))
            height = int(Quartz.CGRectGetHeight(rect))
            
            out_path = os.path.join(out_dir, f"page_{i}.png")
            url_out = CFURLCreateFromFileSystemRepresentation(None, out_path.encode('utf-8'), len(out_path), False)
            dest = Quartz.CGImageDestinationCreateWithURL(url_out, 'public.png', 1, None)
            
            color_space = Quartz.CGColorSpaceCreateDeviceRGB()
            context = Quartz.CGBitmapContextCreate(None, width, height, 8, width * 4, color_space, Quartz.kCGImageAlphaPremultipliedLast)
            
            Quartz.CGContextSetRGBFillColor(context, 1.0, 1.0, 1.0, 1.0)
            Quartz.CGContextFillRect(context, rect)
            
            Quartz.CGContextDrawPDFPage(context, page)
            image = Quartz.CGBitmapContextCreateImage(context)
            
            Quartz.CGImageDestinationAddImage(dest, image, None)
            Quartz.CGImageDestinationFinalize(dest)
            
            results.append({
                "page_number": i,
                "image_path": out_path,
                "width": width,
                "height": height
            })
            
    return results

def handle_action(args: dict, context: dict = None) -> str:
    action = args.get("action", "render")
    
    if action == "detect":
            return json.dumps(detect_libreoffice())
            
    file_path = args.get("file_path", "")
    if not os.path.exists(file_path):
            return "Error: File does not exist."
            
    lo_info = detect_libreoffice()
    if not lo_info.get("available"):
            return "Error: Renderer no disponible. LibreOffice no detectado. Continúa con validación estructural."
            
    out_dir = tempfile.mkdtemp(prefix="kira_qa_")
    try:
            # Render
            pdf_path = render_to_pdf(file_path, out_dir, lo_info["executable"])
            
            # Convert
            pages = pdf_to_images(pdf_path, out_dir)
            
            # We delete the PDF immediately to save space, but keep images because main.py needs to read them.
            # Images will be deleted by main.py or OS temp cleanup.
            try: os.remove(pdf_path)
            except: pass
            
            result = {
                "status": "VISUAL_QA_READY",
                "images": [p["image_path"] for p in pages],
                "metadata": {
                    "renderer": "LibreOffice",
                    "pages_rendered": len(pages)
                }
            }
            return json.dumps(result)
    except Exception as e:
            return f"Error en Visual QA: {str(e)}\n{traceback.format_exc()}\nDocumento conservado. Aplica fallback estructural."

TOOL = {
    "name": "visual_qa",
    "description": (
            "Renders a Deliverable (PPTX, DOCX, PDF) to images for REAL Visual QA. "
            "Actions: 'detect' (check if renderer available), 'render' (convert to images). "
            "Use 'render' right after document_maker for PREMIUM presentations. "
            "It generates images and feeds them back to your vision. "
            "Analyze clipping, overlap, readability, empty space. Give a Score 0-100. "
            "If problems exist, use your REPAIR loop (max 2 times) to regenerate the specific failed slides."
    ),
    "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "'detect' or 'render'. Default 'render'."
                },
                "file_path": {
                    "type": "STRING",
                    "description": "Absolute path to the PPTX or DOCX."
                }
            },
            "required": ["file_path"]
    },
    "handler": handle_action
}
