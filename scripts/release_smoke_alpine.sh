#!/bin/sh
# Alpine has no confluent-kafka wheels. Its current librdkafka package is
# older than confluent-kafka 2.16 needs, so build the matching native library
# before the unchanged all-plugins release smoke install.
set -eu

apk add --no-cache libstdc++ build-base unixodbc-dev curl openssl-dev \
  cyrus-sasl-dev zstd-dev lz4-dev curl-dev
curl --fail --silent --show-error --location \
  https://github.com/confluentinc/librdkafka/archive/refs/tags/v2.16.0.tar.gz \
  --output /tmp/librdkafka.tar.gz
echo 'e6b61de61d3282879a88e4ee3d9f634a8b05bf78a9198dfc25c4820c0c9ed231  /tmp/librdkafka.tar.gz' | sha256sum -c -
tar -xzf /tmp/librdkafka.tar.gz -C /tmp
(
  cd /tmp/librdkafka-2.16.0
  ./configure --prefix=/usr
  make -j2
  make install
)
python scripts/release_smoke.py --version "$VERSION" --source dist --kernel rust --attempts 1
