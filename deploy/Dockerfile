FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements-relay.txt /app/requirements-relay.txt
RUN pip install --no-cache-dir -r /app/requirements-relay.txt

COPY common /app/common
COPY relay /app/relay

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health').status==200 else 1)"

ENTRYPOINT ["python", "-m", "relay.app"]
