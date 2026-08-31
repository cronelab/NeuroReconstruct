# ─────────────────────────────────────────────────────────────────────────────
# NeuroReconstruct — Azure App Service (Linux container) image
#
# Build in Azure so the multi-GB layers never cross the VPN:
#   az acr build --registry rit3845neuroreconacr01 --image neurorecon:1.0 .
#
# Stage 1 builds the React bundle; stage 2 is the runtime. The bundle is copied
# to backend/frontend_build/, which main.py already mounts when present (that
# path was added for the PyInstaller build and works unfrozen too), so the API
# and the UI are served same-origin and CORS stays out of the picture.
# ─────────────────────────────────────────────────────────────────────────────

FROM node:20-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build


FROM python:3.11-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    # ANTs/TF are CPU-only here; see services/structure_extractor.py for why GPU
    # was rejected (no speedup -- CPU preprocessing dominates).
    CUDA_VISIBLE_DEVICES=-1 \
    # ANTs writes large temporary warp fields. Keep that traffic on the
    # container's local disk, NOT on the mounted SMB share.
    TMPDIR=/tmp

# System libraries:
#   libgl1 / libglib2.0-0  -> open3d
#   libgomp1               -> ANTs + scikit-image OpenMP
#   unixodbc + msodbcsql18 -> Azure SQL via aioodbc
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates \
        libgl1 libglib2.0-0 libgomp1 unixodbc \
    && curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-cloud.txt

COPY backend/ /app/backend/
COPY --from=frontend /build/build /app/backend/frontend_build

# Bake the pretrained weights and the MNI template into the image. Without this
# the first upload of each container generation silently re-downloads hundreds
# of MB mid-request. Uses the ANTs MNI template as the warm-up subject, so the
# template is fetched at build time too.
RUN python /app/backend/warmup_models.py

WORKDIR /app/backend
EXPOSE 8000

# One worker on purpose: mesh extraction and MNI export run as in-process
# background tasks, and the startup reaper in main.py assumes a single instance.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-${WEBSITES_PORT:-8000}} --workers 1 --timeout-keep-alive 120"]
