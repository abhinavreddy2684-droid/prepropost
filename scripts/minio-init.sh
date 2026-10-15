#!/bin/sh
# Creates the private media bucket in local MinIO and applies CORS for the frontend origins.
# Run by the `minio-init` service in docker-compose; safe to re-run.
set -eu

mc alias set local http://minio:9000 "$ACCESS_KEY" "$SECRET_KEY"
mc mb --ignore-existing "local/$BUCKET"
mc anonymous set none "local/$BUCKET"   # private: every read goes through a signed URL

rules=""
for origin in $(echo "$ORIGINS" | tr ',' ' '); do
  if [ "$origin" = "*" ]; then
    echo "Refusing CORS origin '*'; list exact origins in MEDIA_CORS_ALLOWED_ORIGINS." >&2
    exit 1
  fi
  rules="$rules<AllowedOrigin>$origin</AllowedOrigin>"
done

cat > /tmp/cors.xml <<XML
<CORSConfiguration>
  <CORSRule>
    $rules
    <AllowedMethod>POST</AllowedMethod>
    <AllowedMethod>GET</AllowedMethod>
    <AllowedHeader>*</AllowedHeader>
    <ExposeHeader>ETag</ExposeHeader>
    <MaxAgeSeconds>3000</MaxAgeSeconds>
  </CORSRule>
</CORSConfiguration>
XML

# Older MinIO builds have no per-bucket CORS; the server-wide MINIO_API_CORS_ALLOW_ORIGIN
# (same exact origins) still applies there.
mc cors set "local/$BUCKET" /tmp/cors.xml \
  || echo "Bucket CORS not supported by this MinIO build; using server-wide CORS origins."
echo "Bucket $BUCKET ready."
