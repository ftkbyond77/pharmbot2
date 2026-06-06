import os
from dotenv import load_dotenv
from google import genai


load_dotenv()
# แนะนำให้ตั้งค่า API Key ผ่าน Environment Variable เพื่อความปลอดภัย
# เช่น ใน Terminal ให้พิมพ์: set GEMINI_API_KEY="your_api_key_here" (สำหรับ Windows)
# หรือ export GEMINI_API_KEY="your_api_key_here" (สำหรับ Mac/Linux)
api_key = os.environ.get("GEMINI_API_KEY")

# หรือถ้าต้องการใส่ API Key ลงไปในโค้ดตรงๆ (ไม่แนะนำสำหรับ Production)
# api_key = "AIzaSyYourAPIKeyHere..."

def list_available_models():
    if not api_key:
        print("Error: Please set your GEMINI_API_KEY.")
        return

    try:
        # สร้าง Client ของ Google GenAI
        client = genai.Client(api_key=api_key)
        
        print("กำลังดึงรายชื่อโมเดล...")
        print("-" * 30)
        
        # ดึงและแสดงรายชื่อโมเดลทั้งหมดที่ API Key นี้มองเห็น
        models = client.models.list()
        for model in models:
            print(f"Model Name: {model.name}")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    list_available_models()