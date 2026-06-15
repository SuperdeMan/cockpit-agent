FROM node:22-alpine

WORKDIR /app
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./

EXPOSE 5174
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
