# syntax=docker/dockerfile:1
ARG NODE_VERSION=20
ARG NGINX_VERSION=1.27

FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:${NGINX_VERSION}-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -q --spider http://127.0.0.1/ || exit 1
