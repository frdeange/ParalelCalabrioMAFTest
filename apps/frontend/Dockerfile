# syntax=docker/dockerfile:1.7
# =============================================================================
# WFM Data Assistant — Frontend container (apps/frontend)
#
# Multi-stage Next.js 16 (App Router) build producing a standalone server:
#   1. ``deps``    installs node_modules from the lockfile (cache-friendly).
#   2. ``builder`` runs ``next build`` to emit ``.next/standalone``. The
#      ``NEXT_PUBLIC_*`` values are INLINED into the client bundle at this
#      step, so they must be supplied as build args (not runtime env).
#   3. ``runtime`` copies only the standalone server + static assets and runs
#      as a non-root user (uid 10001) with ``node server.js``.
#
# Build (NEXT_PUBLIC_* baked in at build time):
#     docker build -t frontend apps/frontend \
#       --build-arg NEXT_PUBLIC_AZURE_CLIENT_ID=... \
#       --build-arg NEXT_PUBLIC_AZURE_TENANT_ID=... \
#       --build-arg NEXT_PUBLIC_REDIRECT_URI=... \
#       --build-arg NEXT_PUBLIC_API_SCOPE=... \
#       --build-arg NEXT_PUBLIC_BACKEND_API_URL=...
# =============================================================================

ARG NODE_VERSION=22

# -----------------------------------------------------------------------------
# Stage 1 — deps
# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS deps

WORKDIR /app

# ``.npmrc`` carries ``legacy-peer-deps=true`` (React 19 vs msal-react peer
# range). Copy it alongside the manifests so ``npm ci`` honours it.
COPY package.json package-lock.json .npmrc ./
RUN npm ci

# -----------------------------------------------------------------------------
# Stage 2 — builder
# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* are compile-time constants inlined into the browser bundle.
# They MUST be present before ``next build`` or the client ships with empty
# MSAL config and the login flow throws at runtime.
ARG NEXT_PUBLIC_AZURE_CLIENT_ID
ARG NEXT_PUBLIC_AZURE_TENANT_ID
ARG NEXT_PUBLIC_REDIRECT_URI
ARG NEXT_PUBLIC_API_SCOPE
ARG NEXT_PUBLIC_BACKEND_API_URL
ENV NEXT_PUBLIC_AZURE_CLIENT_ID=${NEXT_PUBLIC_AZURE_CLIENT_ID} \
    NEXT_PUBLIC_AZURE_TENANT_ID=${NEXT_PUBLIC_AZURE_TENANT_ID} \
    NEXT_PUBLIC_REDIRECT_URI=${NEXT_PUBLIC_REDIRECT_URI} \
    NEXT_PUBLIC_API_SCOPE=${NEXT_PUBLIC_API_SCOPE} \
    NEXT_PUBLIC_BACKEND_API_URL=${NEXT_PUBLIC_BACKEND_API_URL} \
    NEXT_TELEMETRY_DISABLED=1

RUN npm run build

# -----------------------------------------------------------------------------
# Stage 3 — runtime
# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Non-root user (CIS / Defender for Cloud baseline parity with the backend).
RUN addgroup --system --gid 10001 nodejs \
 && adduser  --system --uid 10001 --ingroup nodejs nextjs

WORKDIR /app

# Standalone output bundles a minimal ``server.js`` + the traced subset of
# node_modules. ``static`` and ``public`` are not traced, so copy them too.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:'+(process.env.PORT||3000)+'/',r=>process.exit(r.statusCode<500?0:1)).on('error',()=>process.exit(1))"

CMD ["node", "server.js"]
