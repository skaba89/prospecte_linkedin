# nginx-unprivileged : image officielle conçue pour tourner 100% non-root
# (user 101) sur un port non privilégié (8080). Pas de master process root.
FROM nginxinc/nginx-unprivileged:alpine

# Configuration nginx : sert le static + proxy /api vers le backend
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Dashboard dual-mode (index.html) — en http il active l'adaptateur API
COPY public/ /usr/share/nginx/html/

# Port non privilégié (user 101 ne peut binder le port 80).
EXPOSE 8080

# Healthcheck : la racine sert index.html (200). busybox wget présent dans l'image.
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1

# L'image tourne déjà en user 101 (non-root) — pas de USER explicite requis.
CMD ["nginx", "-g", "daemon off;"]
