# PharmBot

Placeholder repository scaffold for PharmBot (phase 1 components).

See `api/` for backend and `web/` for frontend placeholders.



1. Backend (FastAPI): 
```
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```


2. Frontend
```
- cd web
- npm install (if not exist)
- npm run dev
```


ล้าง Qdrant + Cache

### ลบ collection เก่า
python -c "
from qdrant_client import QdrantClient
c = QdrantClient(url='http://localhost:6333')
c.delete_collection('pharmbot_guidelines')
print('deleted')
"