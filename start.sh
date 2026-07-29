# 在项目根目录下，复制此文件为 .env
cp .env.example .env 2>/dev/null || true

# 启动后端（需要 Python 3.11+）
cd backend
pip install -r requirements.txt
python seed_data.py   # 导入种子数据
uvicorn main:app --reload --host 0.0.0.0 --port 8000 &

# 启动前端（需要 Node.js 18+）
cd ../frontend
npm install
npm run dev
