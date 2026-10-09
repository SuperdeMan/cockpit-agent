FROM node:22-alpine

WORKDIR /app/dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
# Keep the repository-relative, read-only icon imports available in the image.
COPY hmi/src/components/icons.gen.ts hmi/src/components/icons.custom.ts /app/hmi/src/components/
COPY mobile/src/ui/icons.local.ts /app/mobile/src/ui/icons.local.ts

EXPOSE 5174
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
