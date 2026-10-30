# One image for the whole app: Django serves the API at /api and the built React app at /.
# Railway builds this file automatically; see DEPLOY.md. The price data is not in the image: it
# lives on a volume mounted at /data (BACKTESTER_DATA_DIR).

# --- stage 1: build the frontend into plain files (web/dist). Node is thrown away afterwards. -----
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- stage 2: Python, our code, and the built frontend -------------------------------------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app:/app/api \
    BACKTESTER_DATA_DIR=/data \
    WEB_DIST=/app/web/dist
WORKDIR /app

# Dependencies first, from pyproject.toml alone, so a code change doesn't reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['api']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

COPY engine/ engine/
COPY api/ api/
COPY deploy/start.sh deploy/start.sh
COPY --from=web /web/dist web/dist

# Compile the simulator now so the first request doesn't wait for it (numba caches it next to the code).
RUN python -c "from engine.sim import warm; warm()"

CMD ["sh", "/app/deploy/start.sh"]
