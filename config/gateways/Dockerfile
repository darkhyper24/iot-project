FROM nodered/node-red:3.1

USER root
RUN npm install --unsafe-perm --no-update-notifier --no-fund --only=production \
        node-red-contrib-coap \
        node-red-contrib-aedes \
        node-red-dashboard \
 && chown -R node-red:node-red /usr/src/node-red /data || true
USER node-red
