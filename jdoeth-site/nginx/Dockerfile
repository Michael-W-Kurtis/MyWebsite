FROM nginx:1.27-alpine

# Replace the stock default.conf rather than adding alongside it.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY conf.d/site.conf /etc/nginx/conf.d/site.conf
COPY proxy_common.conf /etc/nginx/proxy_common.conf

EXPOSE 80
