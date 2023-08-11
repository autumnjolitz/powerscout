#!/usr/bin/env sh

PGCTL=$(find / -name pg_ctl -print -quit)
if [ "x$PGCTL" = 'x' ]; then
    echo 'Unable to find pg_ctl!!'
    exit 404
fi
echo 'Using '$PGCTL

exec /powerscout/python/bin/python <(cat <<EOF
import os
import shlex
import subprocess
import signal
from contextlib import suppress
from tempfile import TemporaryDirectory, NamedTemporaryFile

from pytest_postgresql.executor import PostgreSQLExecutor

postgres_user = 'postgres'
with suppress(KeyError):
    postgres_user = os.environ["POSTGRES_USER"]

postgres_password = ''
with suppress(KeyError):
    if os.environ.get("POSTGRES_HOST_AUTH_METHOD") != "trust":
        postgres_password = os.environ["POSTGRES_PASSWORD"]

credentials = postgres_user
if postgres_password:
    credentials = f"{postgres_user}:{postgres_password}"

with (
    TemporaryDirectory() as data,
    TemporaryDirectory() as socket,
    NamedTemporaryFile() as log,
    PostgreSQLExecutor(
        "${PGCTL}",
        "127.0.0.1",
        5433,
        data,
        socket,
        log.name,
        "-w",
        "postgres",
    ) as temp_database
):
    model_db_url = f"postgresql://{temp_database.host}:{temp_database.port}/postgres"
    destination_db = f"postgresql://{credentials}@/postgres?host=/var/run/postgresql/"

    subprocess.check_call(shlex.split(f"psql -v ON_ERROR_STOP=1 '{destination_db}' -f /powerscout/sql/01-datatypes.sql"))
    subprocess.check_call(shlex.split(f"psql -v ON_ERROR_STOP=1 '{model_db_url}' -f /powerscout/sql/01-datatypes.sql -f /powerscout/sql/02-init.sql"))

    migra_fh = subprocess.Popen(
        shlex.split(f"migra --unsafe --with-privileges {destination_db} {model_db_url}"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL
    )
    stdout, stderr = migra_fh.communicate()
    if migra_fh.returncode == 0:
        print("Nothing to migrate to!")
        raise SystemExit(0)
    elif migra_fh.returncode == 2:
        with NamedTemporaryFile('w+b') as fh:
            print(f"Migration statements at {fh.name}:\n{stdout.decode()}")
            fh.write(stdout)
            fh.flush()
            subprocess.check_call(shlex.split(f"psql -v ON_ERROR_STOP=1 '{destination_db}' -f {fh.name}"))
        subprocess.check_call(shlex.split(f"psql -v ON_ERROR_STOP=1 '{destination_db}' -f /powerscout/sql/03-after-migration.sql"))
        raise SystemExit(0)
    else:
        print("Encountered error!")
        print(stderr.decode())
        raise SystemExit(migra_fh.returncode)

EOF
)