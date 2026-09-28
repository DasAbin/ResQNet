FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY node.py /app/node.py
RUN groupadd --gid 10001 resqnet && useradd --uid 10001 --gid 10001 --no-create-home resqnet \
    && mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
CMD ["python", "node.py", "--help"]
