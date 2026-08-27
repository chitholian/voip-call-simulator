FROM andrius/asterisk:22

USER root

ARG SOUNDS_VERSION=1.6.1

RUN cd /var/lib/asterisk/sounds && \
    for c in g729 g723 gsm ulaw alaw; do \
        curl -fsSL -o core-$c.tar.gz \
        https://downloads.asterisk.org/pub/telephony/sounds/releases/asterisk-core-sounds-en-$c-${SOUNDS_VERSION}.tar.gz && \
        tar -xzf core-$c.tar.gz --strip-components=1 && \
        rm -f core-$c.tar.gz; \
    done

COPY ./modules /usr/lib/asterisk/modules
COPY ./etc /etc/asterisk
COPY ./sounds /var/lib/asterisk/sounds

RUN chown -R asterisk:asterisk /var/lib/asterisk/sounds /etc/asterisk /usr/lib/asterisk/modules

RUN chmod 755 /usr/lib/asterisk/modules/codec_g723.so && \
    chmod 755 /usr/lib/asterisk/modules/codec_g729.so

USER asterisk
