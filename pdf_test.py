import fitz  # PyMuPDF
import re
from pathlib import Path

def diagnose_pdf(file_path: str):
    path = Path(file_path)
    if not path.exists():
        print(f"❌ ไม่พบไฟล์: {path}")
        return

    print(f"🔍 เริ่มการวินิจฉัยไฟล์: {path.name}")
    print("=" * 60)

    try:
        doc = fitz.open(str(path))
        
        # 1. ตรวจสอบการเข้ารหัสและสิทธิ์ (DRM / Security)
        print("\n[1] 🔒 Security & DRM Information")
        print(f"  - ไฟล์ถูกเข้ารหัส (Encrypted) หรือไม่: {doc.is_encrypted}")
        
        # ตรวจสอบสิทธิ์การ Copy/Extract (bit 4 ของ PDF permissions)
        can_copy = (doc.permissions & fitz.PDF_PERM_COPY) > 0
        print(f"  - อนุญาตให้ดึงข้อความ (Text Extraction Allowed): {'✅ ใช่' if can_copy else '❌ ไม่ (ถูกล็อก)'}")

        # 2. ตรวจสอบ Metadata
        print("\n[2] 📄 Document Metadata")
        meta = doc.metadata
        print(f"  - สร้างโดยโปรแกรม (Creator) : {meta.get('creator', 'Unknown')}")
        print(f"  - PDF Producer           : {meta.get('producer', 'Unknown')}")

        # 3. เจาะลึกรายหน้า (สุ่มตรวจ 5 หน้าแรก)
        print("\n[3] 🔬 Page-by-Page Analysis (5 หน้าแรก)")
        for i in range(min(5, len(doc))):
            page = doc[i]
            
            # ดึงข้อความ
            text_raw = page.get_text("text")
            text_chars = len(re.sub(r"\s+", "", text_raw))
            
            # ดึงโครงสร้างวัตถุ
            images = page.get_images(full=True)
            drawings = page.get_drawings()
            fonts = page.get_fonts()
            
            print(f"\n  ➤ หน้า {i+1}:")
            print(f"    - ความยาวข้อความ (ตัวอักษร) : {text_chars}")
            print(f"    - จำนวนฟอนต์ที่ใช้ (Fonts)   : {len(fonts)}")
            print(f"    - จำนวนรูปภาพ (Images)     : {len(images)}")
            print(f"    - จำนวนเส้นวาด (Vectors)   : {len(drawings)}")
            
            # 💡 สรุปการวินิจฉัยเบื้องต้นของหน้านั้นๆ
            if not can_copy:
                print("      ⚠️ สาเหตุ: ไฟล์ถูกล็อก DRM ทำให้ดึง Text ไม่ได้")
            elif text_chars == 0 and len(drawings) > 500:
                print("      ⚠️ สาเหตุ: ตัวอักษรถูกแปลงเป็นเส้น Vector Outlines (ทำให้ Text=0, Image=0)")
            elif text_chars == 0 and len(images) > 0:
                print("      ⚠️ สาเหตุ: หน้ากระดาษเป็นรูปภาพ Scanned ทั้งหน้า แต่ OCR อาจจะจับไม่ได้")
            elif text_chars > 0 and len(fonts) > 0:
                # ลองปริ้นข้อความมาดูว่าเข้ารหัสพังไหม
                snippet = text_raw[:50].replace('\n', ' ')
                print(f"      ✅ พบข้อความปกติ (ตัวอย่าง: '{snippet}...')")

        doc.close()
        print("\n" + "=" * 60)

    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการอ่านไฟล์: {e}")

# วิธีใช้งาน: เปลี่ยน Path ให้ตรงกับโฟลเดอร์ของคุณ
pdf_path = "data/guidelines/AAFP_2022_Original.pdf"
diagnose_pdf(pdf_path)