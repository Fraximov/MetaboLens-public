FROM rust:1-slim AS msdial-aef-reader-builder

WORKDIR /build
COPY tools/msdial-aef-reader/Cargo.toml tools/msdial-aef-reader/Cargo.lock ./
COPY tools/msdial-aef-reader/src ./src
RUN cargo build --release


FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.2 && \
    python -m pip install --no-cache-dir -r requirements.txt
COPY --from=msdial-aef-reader-builder /build/target/release/msdial-aef-reader /usr/local/bin/msdial-aef-reader
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
ENV PYTHONPATH=/app
ENV MSDIAL_AEF_READER_BIN=/usr/local/bin/msdial-aef-reader
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
