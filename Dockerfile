FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# uv.lock에서 내보낸 고정 버전(requirements.txt)으로 설치해 배포마다 같은 버전을 쓴다
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --no-deps .

COPY alembic.ini ./
COPY migrations ./migrations

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
