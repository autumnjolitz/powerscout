-- Idempotent or non-migra safe SQL here
CREATE EXTENSION IF NOT EXISTS timescaledb
;

CREATE OR REPLACE FUNCTION sum_double_array(double precision[]) RETURNS double precision
    AS $$
    SELECT COALESCE(sum(v), 0.0)
    FROM unnest($1) as tmp(v);
    $$
LANGUAGE SQL IMMUTABLE STRICT PARALLEL SAFE;

DO $$ BEGIN
    IF to_regtype('volts') IS NULL THEN
        CREATE DOMAIN volts AS double precision
            DEFAULT 0::double precision NOT NULL CHECK (VALUE >= 0);
    END IF;
END $$;
DO $$ BEGIN
    IF to_regtype('watts') IS NULL THEN
        CREATE DOMAIN watts AS double precision
            DEFAULT 0::double precision NOT NULL CHECK(VALUE >= 0);
    END IF;
END $$;
DO $$ BEGIN
    IF to_regtype('kilowatts') IS NULL THEN
        CREATE DOMAIN kilowatts AS double precision
            DEFAULT 0::double precision NOT NULL CHECK(VALUE >= 0);
    END IF;
END $$;
DO $$ BEGIN
    IF to_regtype('kilowatt_hours') IS NULL THEN
        CREATE DOMAIN kilowatt_hours AS double precision
            DEFAULT 0::double precision NOT NULL CHECK(VALUE >= 0);
    END IF;
END $$;
DO $$ BEGIN
    IF to_regtype('percent') IS NULL THEN
    CREATE DOMAIN percent AS double precision
        DEFAULT 0::double precision NOT NULL CHECK(VALUE BETWEEN 0 AND 100);
    END IF;
END $$;
DO $$ BEGIN
    IF to_regtype('hourly_cost_rate') IS NULL THEN
    CREATE DOMAIN hourly_cost_rate AS NUMERIC(6, 4)
        DEFAULT 0::double precision NOT NULL CHECK(VALUE >= 0);
    END IF;
END $$;
DO $$ BEGIN
    IF to_regtype('timeleft') IS NULL THEN
    CREATE TYPE timeleft AS ("value" real, "unit" TEXT);
    END IF;
END $$;
