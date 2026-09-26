# Build context is the repo root (see docker-compose.yml): the page is prerendered from files outside
# console/ — the fixtures, the prompt files, config.py, the eval sets, the catalog and docs/metrics.md.
FROM node:22-alpine AS build
WORKDIR /repo/console

COPY console/package.json console/package-lock.json ./
RUN npm ci

COPY console/ ./
COPY data/fixtures/ /repo/data/fixtures/
COPY data/kit/deeplinks.json /repo/data/kit/
COPY data/gold/ /repo/data/gold/
COPY eval/sets/ /repo/eval/sets/
COPY docs/metrics.md /repo/docs/
COPY api/app/config.py /repo/api/app/
COPY api/app/llm/prompts/ /repo/api/app/llm/prompts/

# The browser calls the API directly, so this is the address as the *viewer* reaches it, baked in
# at build time (NEXT_PUBLIC_*). 127.0.0.1 rather than localhost: see lib/stream.ts.
ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

# turbopack.root is the repo, so the standalone server sits one folder down, under console/.
COPY --from=build /repo/console/.next/standalone/ ./
COPY --from=build /repo/console/.next/static ./console/.next/static
COPY --from=build /repo/console/public ./console/public

USER node
EXPOSE 3000
CMD ["node", "console/server.js"]
