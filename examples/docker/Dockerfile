# For the official Debian-based AstrBot image. Keep the same data volume.
# Override both image arguments with tested tags/digests for repeatable builds.
ARG NODE_IMAGE=node:24-bookworm-slim
ARG ASTRBOT_IMAGE=soulter/astrbot:latest
FROM ${NODE_IMAGE} AS node-runtime

FROM ${ASTRBOT_IMAGE}
USER root

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libstdc++6 libatomic1 fonts-noto-cjk \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version \
    && npm --version \
    && ffmpeg -version \
    && ffprobe -version \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Python requirements are installed by AstrBot when the plugin is installed.
# Install optional PO Token components explicitly with tools/setup_pot_provider.py
# inside the running container, with --data-dir under the persistent data volume.
