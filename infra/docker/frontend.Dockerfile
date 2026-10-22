FROM python:3.12-slim

WORKDIR /app
COPY frontend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 && \
    python -m pip install --no-cache-dir -r requirements.txt
COPY frontend/ ./
EXPOSE 8050
CMD ["python", "app.py"]
