CREATE TABLE IF NOT EXISTS apc_measurements (
    "endpoint"       TEXT NOT NULL CHECK (LENGTH("endpoint") > 1),
    "at"             timestamptz NOT NULL DEFAULT NOW(),
    "source_voltage" volts NOT NULL DEFAULT 0::volts,
    "outlet_labels"  TEXT[] NOT NULL DEFAULT '{}'::TEXT[],
    "outlet_loads"   watts[] NOT NULL DEFAULT '{}'::watts[],
    "load"           watts GENERATED ALWAYS AS (sum_double_array("outlet_loads"::double precision[])::watts) STORED,
    "capacity"       percent NOT NULL DEFAULT 0::percent,
    "estimated_time_left" timeleft NOT NULL DEFAULT ROW(0, 'minutes')::timeleft,
    "battery_status" TEXT NOT NULL,
    "on_battery"     BOOLEAN NOT NULL DEFAULT false,
    CONSTRAINT "check_outlet_length" CHECK (
        cardinality("outlet_labels") = cardinality("outlet_loads")
    ),
    PRIMARY KEY ("endpoint", "at")
);


CREATE TABLE IF NOT EXISTS meters (
    "meter_id"          TEXT NOT NULL CHECK (LENGTH("meter_id") > 0),
    "at"                timestamptz NOT NULL, -- meter's timestamp in our time zone
    "instand_demand"    kilowatts NOT NULL DEFAULT 0::kilowatts,
    "consumed"          kilowatt_hours NOT NULL DEFAULT 0::kilowatt_hours,
    "price"             hourly_cost_rate NOT NULL DEFAULT 0::hourly_cost_rate,
    "tier"              TEXT NOT NULL CHECK (LENGTH("tier") > 0),
    "generated"         kilowatt_hours NOT NULL DEFAULT 0::kilowatt_hours,
    PRIMARY KEY ("meter_id", "at")
);


