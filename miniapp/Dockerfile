FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Ссылка на бота для кнопки «Открыть чат» (экран 18); Vite вшивает её при сборке
ARG VITE_BOT_URL=
ENV VITE_BOT_URL=$VITE_BOT_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
