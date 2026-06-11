import os
from dotenv import load_dotenv
from google import genai


load_dotenv()


api_key = os.environ.get("GEMINI_API_KEY")


def list_available_models():
    if not api_key:
        print("Error: Please set your GEMINI_API_KEY.")
        return

    try:
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