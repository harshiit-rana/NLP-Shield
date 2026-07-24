import sys
import os
from pdf2image import convert_from_path

def convert_pdf(pdf_path, out_dir="pdf_pages", dpi=300):
    os.makedirs(out_dir, exist_ok=True)
    pages = convert_from_path(
        pdf_path,
        dpi=dpi,
        poppler_path=r"D:\\ocr-try\\Release-26.02.0-0\\poppler-26.02.0\\Library\bin"
    )

    base = os.path.splitext(os.path.basename(pdf_path))[0]
    saved_paths = []

    for i, page in enumerate(pages, start=1):
        out_path = os.path.join(out_dir, f"{base}_page{i:03d}.png")
        page.save(out_path, "PNG")
        saved_paths.append(out_path)
        print(f"Saved: {out_path}")

    return saved_paths

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pdf_to_images.py path/to/document.pdf")
        sys.exit(1)

    convert_pdf(sys.argv[1])