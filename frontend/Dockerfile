FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm install

COPY . .

# Stays root -- a non-root user was tried here (chown /app to the image's
# built-in `node` user, then USER node) and it broke at runtime: Vite
# writes a temp config bundle into node_modules/.vite-temp/, but
# node_modules is an anonymous volume (docker-compose.yml's
# `/app/node_modules` mount) -- and Docker does not reliably carry the
# image's chown over onto a freshly-initialized anonymous volume, so that
# directory came back root-owned at container start regardless, and the
# `node` user got EACCES trying to write into it. Verified by actually
# hitting the failure, not assumed. Same root cause class as the backend
# staying root (see backend/Dockerfile), just via a different path
# (anonymous volume vs. bind mount).
EXPOSE 5173

CMD ["npm", "run", "dev"]
