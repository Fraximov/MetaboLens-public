# Docker image for the MS-DIAL 5 Nextflow workflow

FROM mcr.microsoft.com/dotnet/runtime:8.0

ARG MSDIAL5_RELEASE_URL=https://github.com/systemsomicslab/MsdialWorkbench/releases/download/MSDIAL-v5.5.251021/MSDIAL.console.v5.5.251021-linux-net8.zip

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends ca-certificates wget unzip procps && \
    rm -rf /var/lib/apt/lists/*

RUN wget -O /tmp/msdial5.zip ${MSDIAL5_RELEASE_URL} && \
    unzip -q /tmp/msdial5.zip -d /opt/msdial5 && \
    rm -f /tmp/msdial5.zip && \
    chmod +x /opt/msdial5/MSDIALCUI && \
    ln -sf /opt/msdial5/MSDIALCUI /usr/local/bin/MSDIALCUI
