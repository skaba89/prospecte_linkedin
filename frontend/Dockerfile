FROM nginx:alpine

# Configuration nginx : sert le static + proxy /api vers le backend
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Dashboard dual-mode (index.html) — en http il active l'adaptateur API
COPY public/ /usr/share/nginx/html/

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
