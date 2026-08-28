FROM andrius/asterisk:22

USER root

ARG SOUNDS_VERSION=1.6.1

RUN cd /var/lib/asterisk/sounds && \
    for c in g729 gsm ulaw alaw; do \
        curl -fsSL -o core-$c.tar.gz \
        https://downloads.asterisk.org/pub/telephony/sounds/releases/asterisk-core-sounds-en-$c-${SOUNDS_VERSION}.tar.gz || true ; \
        tar -xzf core-$c.tar.gz --strip-components=1 2>/dev/null || true ; \
        rm -f core-$c.tar.gz ; \
    done

COPY ./agi-bin /var/lib/asterisk/agi-bin
COPY ./entrypoint.sh /usr/local/bin/entrypoint.sh
COPY ./modules /usr/lib/asterisk/modules
COPY ./etc /etc/asterisk

RUN chown -R asterisk:asterisk /var/lib/asterisk/sounds /etc/asterisk /usr/lib/asterisk/modules /var/lib/asterisk/agi-bin && \
    chmod +x /usr/local/bin/entrypoint.sh /var/lib/asterisk/agi-bin/*.py && \
    chmod 755 /usr/lib/asterisk/modules/codec_g723.so && \
    chmod 755 /usr/lib/asterisk/modules/codec_g729.so

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD []

USER asterisk
