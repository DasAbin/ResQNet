FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY node.py /app/node.py
COPY core.py /app/core.py
# AES-GCM for the lab wire protocol; pinned range matches requirements.txt.
RUN pip install --no-cache-dir "cryptography>=43"
RUN groupadd --gid 10001 resqnet && useradd --uid 10001 --gid 10001 --no-create-home resqnet \
    && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
CMD ["python", "node.py", "--help"]
