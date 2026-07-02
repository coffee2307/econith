# =============================================================================
# ECONITH :: frontend_dashboard image (Node.js / Next.js)
# Dev-oriented image that serves the dashboard over HTTPS via Next.js
# `--experimental-https` (self-signed certs auto-generated on first boot).
# For production, build a standalone image (see dashboard/README.md).
# =============================================================================
FROM node:22-alpine

WORKDIR /app

# install deps first for better layer caching
COPY package.json ./
RUN npm install

# app source
COPY . .

EXPOSE 3000

# `dev` script runs: next dev --experimental-https -p 3000
CMD ["npm", "run", "dev"]
