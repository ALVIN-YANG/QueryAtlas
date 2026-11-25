#!/bin/sh
# Run in the checkout on the Docker host.
set -eu
umask 077
test -e bootstrap.key || openssl rand -hex 36 > bootstrap.key
if ! test -e .env; then
  {
    openssl rand -hex 28 | sed 's/^/QA_DB_PASSWORD=/'
    openssl rand -hex 28 | sed 's/^/QA_PROBE_KEY=/'
  } > .env
fi
mkdir -p models
revision=75c43b069aac4d136ba6bc1122f995fedcfd2781
for path in tokenizer.json onnx/model_quantized.onnx; do
  dest="models/${path##*/}"
  if ! test -f "$dest"; then
    curl -fsSL --retry 2 "https://huggingface.co/Xenova/bge-small-zh-v1.5/resolve/$revision/$path" -o "$dest.tmp"
    mv "$dest.tmp" "$dest"
  fi
done
(cd models && sha256sum -c - <<'SUMS'
48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26  tokenizer.json
15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc  model_quantized.onnx
SUMS
)
for key in QA_SR_READ QA_SR_LOAD; do
  if ! grep -q "^${key}=" .env; then openssl rand -hex 24 | sed "s/^/$key=/" >> .env; fi
done
