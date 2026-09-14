FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    FLASK_APP=wsgi

# Fast ikke-root bruger (uid/gid 10001), som gunicorn kører som
RUN groupadd --system --gid 10001 musikquiz \
 && useradd --system --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin musikquiz

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY musikquiz ./musikquiz
COPY wsgi.py gunicorn.conf.py ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# sed fjerner evt. Windows-linjeskift, så scriptet altid kan køres
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
 && chmod 0755 /usr/local/bin/docker-entrypoint.sh \
 && mkdir -p /data/media \
 && chown -R 10001:10001 /data

EXPOSE 8000

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "wsgi:app"]
