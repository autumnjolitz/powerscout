# syntax=docker/dockerfile:1

FROM docker.io/python:3.10-alpine AS builder

RUN python -m pip install -U pip setuptools wheel && apk add zip
WORKDIR /usr/local/bin
ADD scripts/recursiveldd.py recursive-ldd

WORKDIR /artifacts
RUN python -m venv --symlinks --upgrade-deps /app && \
    mkdir wheels && \
    apk add zip && \
    cd /usr/local/lib/python3.10 && \
    python -m compileall && \
    zip ../python310.zip $(find . -type d -path './site-packages' -prune -o $(find . -name "*.so" -print | xargs -n 1 dirname | sort | uniq | xargs -n1 -I {}\; echo '-type d -path ' {}\; '-prune -o') -type f) && \
                           find . -type d -path './site-packages' -prune -o $(find . -name "*.so" -print | xargs -n 1 dirname | sort | uniq | xargs -n1 -I {}\; echo '-type d -path ' {}\; '-prune -o') -type f -exec rm {} \; && \
    find . -empty -type d -delete && \
    recursive-ldd /usr/local/bin/python $(find . -type f -name "*.so" -print)

WORKDIR /

WORKDIR /build/powerscout
ADD requirements.deps.lock .
RUN python -m pip wheel -r requirements.deps.lock -w /artifacts/wheels/
ADD requirements.lock .
RUN python -m pip wheel -r requirements.lock -w /artifacts/wheels/
RUN /app/bin/python -m pip install --no-index -f /artifacts/wheels/ -r requirements.lock -r requirements.deps.lock

ADD dist/ dist/
RUN /app/bin/python -m pip install --no-index -f dist powerscout && \
    /app/bin/python -m pip uninstall -y pip


RUN export DIRECT_LDD_FILES=$(recursive-ldd /usr/local/bin/python $(find /usr/local/lib/python3.10 -type f -name "*.so" -print) | grep -vE '^/usr/local/lib/python3.10') && \
    echo "${DIRECT_LDD_FILES}" && \
    find /usr/local/lib/python3.10 /app \( -name '*.pyx' -o -name '*.pyi' -o -name '*.c' -o -name '*.pyd' \) -exec rm -f -- {} \; && \
    mkdir /arch && \
    tar -cf - \
        /app \
        /etc/passwd \
        /usr/local/bin/python* \
        /usr/local/lib/python310.zip \
        /usr/local/lib/python3.10 \
        ${DIRECT_LDD_FILES} \ | \
    tar -C /arch -xvf -  && \
    ls /arch

FROM scratch
ENV PYTHONHOME='/usr/local'
EXPOSE 8080

COPY --from=builder /lib/ld-musl-x86_64.so.1 /lib/ld-musl-x86_64.so.1
COPY --from=builder /usr/bin/env /usr/bin/env
COPY --from=builder /bin/sh /bin/sh
COPY --from=builder /bin/busybox /bin/busybox
COPY --from=builder /arch /

ENTRYPOINT ["/app/bin/python", "-m", "powerscout"]
CMD ["-d", "cli"]