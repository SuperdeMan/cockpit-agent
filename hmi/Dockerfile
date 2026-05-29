# build context 为 hmi 目录（见 deploy/docker-compose.yaml）。
FROM node:20-slim
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 5173
CMD ["npm", "run", "dev"]
