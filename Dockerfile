FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt \
    && groupadd --gid 10001 paperplane \
    && useradd --uid 10001 --gid 10001 --no-create-home paperplane \
    && mkdir /data && chown 10001:10001 /data
COPY run.py ./
COPY studio/ ./studio/
COPY web/ ./web/
COPY skills/ ./skills/
COPY agents/ ./agents/
COPY docs/visual-styles/aiclaw-warm-engineering-v1/ ./docs/visual-styles/aiclaw-warm-engineering-v1/
COPY tools/restore_studio.py ./tools/restore_studio.py
USER 10001:10001
EXPOSE 8787
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8787", "--data-dir", "/data"]
