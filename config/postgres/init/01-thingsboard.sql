-- Creates an additional logical database for ThingsBoard inside the shared Postgres instance.
-- Runs once at container first-boot via /docker-entrypoint-initdb.d.
CREATE DATABASE thingsboard WITH OWNER iot_user;
GRANT ALL PRIVILEGES ON DATABASE thingsboard TO iot_user;
